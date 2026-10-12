"""Reusable PDF extraction, including OCR of pages with raster content."""

from __future__ import annotations

import csv
import io
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader


@dataclass
class ExtractedPage:
    number: int
    text: str
    ocr: bool = False
    confidence: float | None = None


def ocr_page(path: Path, index: int, language: str) -> tuple[str, float]:
    import pypdfium2 as pdfium

    with tempfile.TemporaryDirectory(prefix="sodimac-ocr-") as temporary:
        image_path = Path(temporary) / "page.png"
        with pdfium.PdfDocument(path) as document:
            page = document[index]
            bitmap = page.render(scale=3)
            bitmap.to_pil().save(image_path)
            bitmap.close()
            page.close()
        result = subprocess.run(
            ["tesseract", str(image_path), "stdout", "-l", language, "--psm", "3", "tsv"],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        lines: dict[tuple, list[dict]] = {}
        confidences = []
        for row in csv.DictReader(io.StringIO(result.stdout), delimiter="\t", quoting=csv.QUOTE_NONE):
            if not row.get("text", "").strip():
                continue
            confidence = float(row["conf"])
            confidences.append(confidence)
            key = (row["block_num"], row["par_num"], row["line_num"])
            lines.setdefault(key, []).append(row)
        output = []
        last_block = None
        for key, words in lines.items():
            if last_block is not None and key[:2] != last_block:
                output.append("")
            # Preserve large horizontal gaps for tables instead of merging their columns.
            line = ""
            previous_right = None
            for word in words:
                left = int(word["left"])
                gap = "   " if previous_right is not None and left - previous_right > 35 else " "
                line += (gap if line else "") + word["text"]
                previous_right = left + int(word["width"])
            output.append(line)
            last_block = key[:2]
        return "\n".join(output), sum(confidences) / max(1, len(confidences))


def extract_pdf(path: Path, language: str = "spa+eng", ocr_mode: str = "auto") -> list[ExtractedPage]:
    reader = PdfReader(path)
    if reader.is_encrypted:
        raise ValueError("El PDF está cifrado; proporciona una copia sin contraseña")
    if not 1 <= len(reader.pages) <= 300:
        raise ValueError("El PDF debe tener entre 1 y 300 páginas")
    pages = []
    for index, page in enumerate(reader.pages):
        text = page.extract_text(extraction_mode="layout") or ""
        use_ocr = ocr_mode == "always" or len(text.strip()) < 60 or bool(page.images)
        confidence = None
        if use_ocr:
            recognized, confidence = ocr_page(path, index, language)
            if confidence < 75 or len(recognized.strip()) < 30:
                raise ValueError(f"OCR de baja confianza en página {index + 1}; revisa el documento")
            # Original text remains authoritative; OCR supplies content only available in images.
            if len(text.strip()) >= 60:
                text += "\n\nContenido reconocido por OCR (revisar tablas):\n" + recognized
            else:
                text = recognized
        if len(text.strip()) < 30:
            raise ValueError(f"Página {index + 1} sin contenido extraíble")
        pages.append(ExtractedPage(index + 1, text.strip(), use_ocr, confidence))
    return pages


def sections(pages: list[ExtractedPage]) -> list[dict]:
    results = []
    heading = ""
    for page in pages:
        blocks = re.split(r"\n\s*\n", page.text)
        current = []
        length = 0
        for block in blocks:
            block = block.strip()
            if not block or re.fullmatch(r"\d+", block):
                continue
            is_heading = bool(re.match(r"^(?:[IVX]+\.|\d+(?:\.\d+)*\.)\s+\D", block)) and len(block) < 220
            if current and (is_heading or length + len(block) > 3500):
                results.append({"pagina": page.number, "texto": "\n\n".join(current), "ocr": page.ocr})
                current, length = [], 0
            if is_heading:
                heading = re.sub(r"\s+", " ", block)
            if not current and heading and heading != re.sub(r"\s+", " ", block):
                current.append(heading)
                length += len(heading)
            current.append(block)
            length += len(block)
        if current:
            results.append({"pagina": page.number, "texto": "\n\n".join(current), "ocr": page.ocr})
    return results
