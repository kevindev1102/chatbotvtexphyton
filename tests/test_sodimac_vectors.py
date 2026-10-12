from pathlib import Path
from unittest.mock import patch

import pytest
from qdrant_client import QdrantClient

from app.errors import ExternalServiceError
from app.sodimac_index import PdfIndexer
from app.sodimac_pdf import ExtractedPage, extract_pdf, sections
from app.sodimac_vectors import QdrantDocuments, VectorSettings, sparse


class FakeEmbeddings:
    """Deterministic semantic fixture; Qdrant itself is real in these tests."""

    def __init__(self):
        self.calls = 0

    def encode(self, texts):
        self.calls += 1
        return [
            [1.0, 0.0, 0.0]
            if any(w in t.lower() for w in ["inscrip", "afiliar", "registro"])
            else [0.0, 1.0, 0.0]
            if "alquiler" in t.lower()
            else [0.0, 0.0, 1.0]
            for t in texts
        ]

    def split(self, text):
        return [text]


@pytest.fixture
def vector_store(tmp_path):
    client = QdrantClient(":memory:")
    store = QdrantDocuments(
        VectorSettings(cache=str(tmp_path / "models")), client=client, embeddings=FakeEmbeddings()
    )
    yield store
    store.close()


@pytest.fixture
def indexer(vector_store, tmp_path):
    return PdfIndexer(vector_store, tmp_path / "reports")


def staged(
    indexer, tmp_path, text="Inscripción: mayores de 18 años residentes en Perú.", version=b"version1"
):
    source = tmp_path / "ces.pdf"
    source.write_bytes(version)
    with patch("app.sodimac_index.extract_pdf", return_value=[ExtractedPage(1, text)]):
        return indexer.index(source)


def test_staging_does_not_change_active_and_semantic_synonym_search_works(indexer, vector_store, tmp_path):
    result = staged(indexer, tmp_path)
    assert vector_store.active_collection() is None
    with pytest.raises(ExternalServiceError, match="activado"):
        vector_store.retrieve("afiliarme")
    indexer.activate(result["collection"])
    found = vector_store.retrieve("¿Cómo puedo afiliarme?")
    assert found
    assert "Inscripción" in found[0]["texto"]
    assert "página" not in vector_store.render(found)
    assert vector_store.retrieve("intergaláctico extraterrestre") == []


def test_idempotent_index_does_not_reextract_or_generate_embeddings(indexer, vector_store, tmp_path):
    first = staged(indexer, tmp_path)
    calls = vector_store.embeddings.calls
    with patch("app.sodimac_index.extract_pdf", side_effect=AssertionError("must not extract twice")):
        second = indexer.index(tmp_path / "ces.pdf")
    assert first["collection"] == second["collection"]
    assert second["changed"] is False
    assert vector_store.embeddings.calls == calls


def test_update_switches_version_and_preserves_old_for_rollback(indexer, vector_store, tmp_path):
    first = staged(indexer, tmp_path, text="Inscripción: condición antigua.")
    indexer.activate(first["collection"])
    second = staged(indexer, tmp_path, text="Inscripción: condición nueva.", version=b"version2")
    assert vector_store.active_collection() == first["collection"]
    activated = indexer.activate(second["collection"])
    assert activated["previous"] == first["collection"]
    assert "nueva" in vector_store.retrieve("afiliarme")[0]["texto"]
    assert vector_store.client.collection_exists(first["collection"])
    indexer.activate(first["collection"])
    assert "antigua" in vector_store.retrieve("afiliarme")[0]["texto"]


def test_failed_extraction_preserves_active(indexer, vector_store, tmp_path):
    first = staged(indexer, tmp_path)
    indexer.activate(first["collection"])
    (tmp_path / "ces.pdf").write_bytes(b"bad version")
    with (
        patch("app.sodimac_index.extract_pdf", side_effect=ValueError("OCR baja confianza")),
        pytest.raises(ValueError),
    ):
        indexer.index(tmp_path / "ces.pdf")
    assert vector_store.active_collection() == first["collection"]


def test_failed_upload_preserves_active_and_incomplete_collection_cannot_activate(
    indexer, vector_store, tmp_path
):
    first = staged(indexer, tmp_path)
    indexer.activate(first["collection"])
    with (
        patch.object(vector_store.client, "upsert", side_effect=RuntimeError("offline")),
        pytest.raises(RuntimeError),
    ):
        staged(indexer, tmp_path, version=b"new")
    assert vector_store.active_collection() == first["collection"]
    incomplete = next(
        c.name for c in vector_store.client.get_collections().collections if c.name != first["collection"]
    )
    with pytest.raises(ValueError, match="completo"):
        indexer.activate(incomplete)
    # Re-indexing can recover an interrupted staging operation.
    recovered = staged(indexer, tmp_path, version=b"new")
    indexer.activate(recovered["collection"])


def test_activation_rejects_missing_chunks_and_wrong_model(indexer, vector_store, tmp_path):
    result = staged(indexer, tmp_path)
    name = result["collection"]
    vector_store.client.delete(name, points_selector=[0], wait=True)
    with pytest.raises(ValueError, match="fragmentos"):
        indexer.activate(name)
    with pytest.raises(ValueError):
        indexer.activate("unrelated_collection")


def test_qdrant_data_and_alias_persist_on_disk(tmp_path):
    settings = VectorSettings(cache=str(tmp_path / "models"))
    path = str(tmp_path / "qdrant")
    first = QdrantDocuments(settings, client=QdrantClient(path=path), embeddings=FakeEmbeddings())
    indexer = PdfIndexer(first, tmp_path / "reports")
    result = staged(indexer, tmp_path)
    indexer.activate(result["collection"])
    first.close()
    second = QdrantDocuments(settings, client=QdrantClient(path=path), embeddings=FakeEmbeddings())
    try:
        assert second.active_collection() == result["collection"]
        assert second.retrieve("afiliarme")
    finally:
        second.close()


def test_sparse_vectors_are_stable_and_include_numeric_tokens():
    assert sparse("inscripción Black 11000") == sparse("inscripción Black 11000")
    assert sparse("11000").indices
    assert sparse("el la y").indices == []


def test_actual_pdf_detects_image_table_and_uses_ocr(monkeypatch):
    calls = []

    def recognize(path, index, language):
        calls.append(index)
        return "Categoría   Compra en 3 meses\nBlack   S/11.000\nOro   S/4.000\nPlata   S/1.500", 96.0

    monkeypatch.setattr("app.sodimac_pdf.ocr_page", recognize)
    pages = extract_pdf(Path("documents/sodimac/ces.pdf"))
    assert len(pages) == 9
    assert calls == [4]
    assert "S/11.000" in pages[4].text
    assert pages[4].ocr
    assert "Tecnofast" in pages[6].text
    blocks = sections(pages)
    assert any("S/11.000" in p["texto"] for p in blocks)
    assert all(p["pagina"] in range(1, 10) for p in blocks)


def test_low_confidence_ocr_rejects_document(monkeypatch):
    monkeypatch.setattr("app.sodimac_pdf.ocr_page", lambda *args: ("Texto ilegible", 30.0))
    with pytest.raises(ValueError, match="baja confianza"):
        extract_pdf(Path("documents/sodimac/ces.pdf"))


async def test_async_search_does_not_require_remote_embedding_api(vector_store, indexer, tmp_path):
    result = staged(indexer, tmp_path)
    indexer.activate(result["collection"])
    assert (await vector_store.search("afiliarme"))[0]["texto"].startswith("Inscripción")
