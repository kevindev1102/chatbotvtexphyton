"""Spanish lexical normalization shared by catalog and hybrid vector search."""

from __future__ import annotations

import re

from app.intent import normalize

STOP = {
    "de",
    "del",
    "la",
    "las",
    "el",
    "los",
    "un",
    "una",
    "unos",
    "y",
    "o",
    "en",
    "que",
    "como",
    "cuanto",
    "cuales",
    "para",
    "por",
    "con",
    "me",
    "mi",
    "es",
    "son",
    "se",
    "al",
    "a",
    "puedo",
    "sodimac",
    "ces",
    "documento",
    "pdf",
    "terminos",
    "condiciones",
}


def terms(text: str) -> set[str]:
    # Small stemming handles inscribir/inscripción, categoría/categorías, etc.
    return {
        word[:6] for word in re.findall(r"[a-z0-9]+", normalize(text)) if len(word) > 2 and word not in STOP
    }
