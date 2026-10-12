"""Local embeddings and versioned Qdrant retrieval; no calls to embedding APIs."""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
import re
import threading
from dataclasses import dataclass

from qdrant_client import QdrantClient, models
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from app.config import PROJECT_ROOT, _environment
from app.errors import ExternalServiceError
from app.sodimac_documents import terms

PIPELINE_VERSION = "pdf-ocr-hybrid-v1"


@dataclass(frozen=True)
class VectorSettings:
    url: str = "http://localhost:6333"
    api_key: str = ""
    alias: str = "sodimac_ces"
    model: str = "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"
    cache: str = str(PROJECT_ROOT / ".cache/embeddings")
    min_score: float = 0.35
    ocr_language: str = "spa+eng"

    @classmethod
    def load(cls):
        env = _environment()
        return cls(
            url=env.get("QDRANT_URL", cls.url),
            api_key=env.get("QDRANT_API_KEY", ""),
            alias=env.get("SODIMAC_COLLECTION_ALIAS", cls.alias),
            model=env.get("SODIMAC_EMBEDDING_MODEL", cls.model),
            cache=env.get("FASTEMBED_CACHE_PATH", cls.cache),
            min_score=float(env.get("SODIMAC_MIN_VECTOR_SCORE", "0.35")),
            ocr_language=env.get("SODIMAC_OCR_LANGUAGE", "spa+eng"),
        )


def sparse(text: str) -> models.SparseVector:
    counts: dict[int, int] = {}
    for word in re.findall(r"\w+", text):
        for token in terms(word):
            index = int.from_bytes(hashlib.blake2s(token.encode(), digest_size=4).digest(), "big")
            counts[index] = counts.get(index, 0) + 1
    indices = sorted(counts)
    return models.SparseVector(indices=indices, values=[1 + math.log(counts[i]) for i in indices])


class LocalEmbeddings:
    def __init__(self, settings: VectorSettings):
        self.settings = settings
        self._model = None
        self._tokenizer = None
        self._lock = threading.RLock()

    def _load(self):
        if self._model is None:
            from fastembed import TextEmbedding
            from tokenizers import Tokenizer

            self._model = TextEmbedding(
                model_name=self.settings.model,
                cache_dir=self.settings.cache,
                threads=max(1, min(4, os.cpu_count() or 1)),
            )
            self._tokenizer = Tokenizer.from_str(self._model.model.tokenizer.to_str())
            self._tokenizer.no_truncation()
            self._tokenizer.no_padding()

    def encode(self, texts: list[str]) -> list[list[float]]:
        with self._lock:
            self._load()
            return [vector.tolist() for vector in self._model.embed(texts, batch_size=16)]

    def split(self, text: str, size: int = 320, overlap: int = 40) -> list[str]:
        with self._lock:
            self._load()
            offsets = self._tokenizer.encode(text, add_special_tokens=False).offsets
            chunks = []
            for start in range(0, len(offsets), size - overlap):
                end = min(start + size, len(offsets))
                chunks.append(text[offsets[start][0] : offsets[end - 1][1]])
                if end == len(offsets):
                    break
            return chunks


class QdrantDocuments:
    def __init__(self, settings: VectorSettings | None = None, *, client=None, embeddings=None):
        self.settings = settings or VectorSettings.load()
        self.client = client or QdrantClient(
            url=self.settings.url,
            api_key=self.settings.api_key or None,
            timeout=15,
            check_compatibility=False,
        )
        self.embeddings = embeddings or LocalEmbeddings(self.settings)
        self._gate = asyncio.Semaphore(2)

    def close(self):
        self.client.close()

    def active_collection(self) -> str | None:
        return next(
            (
                a.collection_name
                for a in self.client.get_aliases().aliases
                if a.alias_name == self.settings.alias
            ),
            None,
        )

    def retrieve(self, query: str, limit: int = 3) -> list[dict]:
        collection = self.active_collection()
        if not collection:
            raise ExternalServiceError("Todavía no hay un PDF activado en Qdrant")
        info = self.client.get_collection(collection)
        if (info.config.metadata or {}).get("embedding_model") != self.settings.model:
            raise ExternalServiceError("El modelo del índice no coincide; vuelve a indexar el PDF")
        vector = self.embeddings.encode([query])[0]
        # A dense threshold is applied before fusion; a lexical match is also evidence.
        dense = self.client.query_points(
            collection,
            query=vector,
            using="dense",
            limit=12,
            score_threshold=self.settings.min_score,
            with_payload=True,
        ).points
        lexical = sparse(query)
        prefetch = [
            models.Prefetch(query=vector, using="dense", limit=12, score_threshold=self.settings.min_score)
        ]
        if lexical.indices:
            prefetch.append(models.Prefetch(query=lexical, using="lexical", limit=12))
        fused = self.client.query_points(
            collection,
            prefetch=prefetch,
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=12,
            with_payload=True,
        ).points
        dense_ids = {p.id for p in dense}
        selected = []
        seen = set()
        remaining = 6500
        for point in fused:
            payload = point.payload or {}
            text = payload.get("texto", "")
            # Never accept a low-similarity result solely because RRF returned something.
            if point.id not in dense_ids and not (terms(query) & terms(text)):
                continue
            parent = payload.get("parent_id", str(point.id))
            if parent in seen or not text or len(text) > remaining:
                continue
            seen.add(parent)
            selected.append(
                {
                    "id": len(selected),
                    "texto": text,
                    "pagina": payload.get("pagina"),
                    "documento": payload.get("documento"),
                    "version": payload.get("sha256"),
                }
            )
            remaining -= len(text)
            if len(selected) >= limit:
                break
        return selected

    async def search(self, query: str, limit: int = 3) -> list[dict]:
        async with self._gate:
            try:
                return await asyncio.to_thread(self.retrieve, query, limit)
            except (
                UnexpectedResponse,
                ResponseHandlingException,
                OSError,
                ValueError,
                RuntimeError,
            ) as error:
                raise ExternalServiceError("No se pudo consultar el documento en Qdrant") from error

    @staticmethod
    def render(passages: list[dict]) -> str:
        if not passages:
            return "No encontré esa información en el documento del CES. ¿Puedes precisar tu consulta?"
        return "Según el documento proporcionado:\n\n" + "\n\n".join(p["texto"] for p in passages[:3])
