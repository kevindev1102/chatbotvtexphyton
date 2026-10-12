"""CLI: index a PDF, inspect its extraction report, then atomically activate it."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from filelock import FileLock
from qdrant_client import models
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from app.sodimac_pdf import extract_pdf, sections
from app.sodimac_vectors import PIPELINE_VERSION, QdrantDocuments, sparse


class PdfIndexer:
    def __init__(self, store: QdrantDocuments, reports: Path):
        self.store = store
        self.client = store.client
        self.reports = reports
        self.reports.mkdir(parents=True, exist_ok=True)

    def index(self, path: Path, ocr_mode: str = "auto") -> dict:
        if path.suffix.lower() != ".pdf" or not path.is_file():
            raise ValueError("Indica la ruta de un archivo PDF existente")
        if path.stat().st_size > 50_000_000:
            raise ValueError("El PDF excede 50 MB")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        signature = json.dumps(
            {
                "sha256": digest,
                "model": self.store.settings.model,
                "pipeline": PIPELINE_VERSION,
                "ocr": ocr_mode,
                "language": self.store.settings.ocr_language,
            },
            sort_keys=True,
        )
        version = hashlib.sha256(signature.encode()).hexdigest()[:24]
        collection = self.store.settings.alias + "_" + version
        if self.client.collection_exists(collection):
            metadata = self.client.get_collection(collection).config.metadata or {}
            if metadata.get("ready") and self.client.count(collection, exact=True).count == metadata.get(
                "chunks"
            ):
                return {
                    "collection": collection,
                    "sha256": digest,
                    "changed": False,
                    "report": str(self.reports / (collection + ".json")),
                    "active": self.store.active_collection(),
                }
            # Resume an interrupted staging build; never delete an active collection.
            if collection == self.store.active_collection():
                raise ValueError("El índice activo está incompleto; no se modificará")
            self.client.delete_collection(collection)

        print("Extrayendo texto y tablas del PDF...", file=sys.stderr, flush=True)
        pages = extract_pdf(path, self.store.settings.ocr_language, ocr_mode)
        print("Preparando embeddings locales...", file=sys.stderr, flush=True)
        parents = sections(pages)
        chunks = []
        for parent_id, parent in enumerate(parents):
            for child in self.store.embeddings.split(parent["texto"]):
                chunks.append({**parent, "embedding_text": child, "parent_id": parent_id})
        if not chunks:
            raise ValueError("El PDF no contiene fragmentos indexables")
        # Validate the embeddings before creating the staged collection.
        first = self.store.embeddings.encode([chunks[0]["embedding_text"]])[0]
        metadata = {
            "documento": path.name,
            "sha256": digest,
            "embedding_model": self.store.settings.model,
            "pipeline": PIPELINE_VERSION,
            "chunks": len(chunks),
            "pages": len(pages),
            "ready": False,
        }
        self.client.create_collection(
            collection,
            vectors_config={"dense": models.VectorParams(size=len(first), distance=models.Distance.COSINE)},
            sparse_vectors_config={"lexical": models.SparseVectorParams(modifier=models.Modifier.IDF)},
            metadata=metadata,
        )
        for start in range(0, len(chunks), 1):
            batch = chunks[start : start + 1]
            vectors = self.store.embeddings.encode([c["embedding_text"] for c in batch])
            if len(vectors) != len(batch):
                raise ValueError("El modelo no generó todos los embeddings")
            points = [
                models.PointStruct(
                    id=start + offset,
                    vector={"dense": vector, "lexical": sparse(chunk["embedding_text"])},
                    payload={
                        k: v
                        for k, v in {**chunk, "documento": path.name, "sha256": digest}.items()
                        if k != "embedding_text"
                    },
                )
                for offset, (chunk, vector) in enumerate(zip(batch, vectors, strict=True))
            ]
            self.client.upsert(collection, points=points, wait=True)
            if (start + 1) % 20 == 0 or start + 1 == len(chunks):
                print(f"Fragmentos guardados: {start + 1}/{len(chunks)}", file=sys.stderr, flush=True)
        if self.client.count(collection, exact=True).count != len(chunks):
            raise ValueError("No se guardaron todos los fragmentos; la versión anterior sigue activa")
        report = {
            **metadata,
            "ready": True,
            "collection": collection,
            "pages_extracted": [asdict(p) for p in pages],
        }
        report_path = self.reports / (collection + ".json")
        temporary = report_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        temporary.replace(report_path)
        metadata["ready"] = True
        self.client.update_collection(collection, metadata=metadata)
        return {
            "collection": collection,
            "sha256": digest,
            "changed": True,
            "chunks": len(chunks),
            "ocr_pages": [p.number for p in pages if p.ocr],
            "report": str(report_path),
            "active": self.store.active_collection(),
        }

    def activate(self, collection: str) -> dict:
        if not re.fullmatch(re.escape(self.store.settings.alias) + r"_[0-9a-f]{24}", collection):
            raise ValueError("La colección no es una versión del documento de Sodimac")
        metadata = self.client.get_collection(collection).config.metadata or {}
        if not metadata.get("ready") or metadata.get("embedding_model") != self.store.settings.model:
            raise ValueError("El índice no está completo o usa otro modelo de embeddings")
        if self.client.count(collection, exact=True).count != metadata.get("chunks"):
            raise ValueError("El índice no contiene todos los fragmentos esperados")
        previous = self.store.active_collection()
        operations = []
        if previous:
            operations.append(
                models.DeleteAliasOperation(
                    delete_alias=models.DeleteAlias(alias_name=self.store.settings.alias)
                )
            )
        operations.append(
            models.CreateAliasOperation(
                create_alias=models.CreateAlias(
                    collection_name=collection, alias_name=self.store.settings.alias
                )
            )
        )
        self.client.update_collection_aliases(change_aliases_operations=operations)
        return {"active": collection, "previous": previous}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    index = commands.add_parser("index", help="Procesa el PDF y prepara una versión sin activarla")
    index.add_argument("pdf", type=Path)
    index.add_argument("--ocr", choices=["auto", "always"], default="auto")
    activate = commands.add_parser("activate", help="Activa una versión previamente revisada")
    activate.add_argument("collection")
    report = commands.add_parser("report", help="Muestra el texto extraído para revisar tablas y cifras")
    report.add_argument("collection")
    commands.add_parser("status", help="Muestra la versión activa y sus metadatos")
    args = parser.parse_args(argv)
    store = QdrantDocuments()
    reports = Path(os.getenv("SODIMAC_REPORTS_PATH", "documents/sodimac/reports"))
    try:
        indexer = PdfIndexer(store, reports)
        # Shared model volume serializes updates from Compose jobs on this host.
        lock = Path(store.settings.cache) / "index.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(lock), timeout=0):
            if args.command == "index":
                output = indexer.index(args.pdf, args.ocr)
            elif args.command == "activate":
                output = indexer.activate(args.collection)
            elif args.command == "report":
                if not re.fullmatch(re.escape(store.settings.alias) + r"_[0-9a-f]{24}", args.collection):
                    raise ValueError("Nombre de colección inválido")
                output = json.loads((reports / (args.collection + ".json")).read_text())
            else:
                active = store.active_collection()
                output = {
                    "active": active,
                    "metadata": store.client.get_collection(active).config.metadata if active else None,
                }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except (
        ValueError,
        OSError,
        RuntimeError,
        subprocess.SubprocessError,
        UnexpectedResponse,
        ResponseHandlingException,
    ) as error:
        print(f"No se modificó la versión activa: {error}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
