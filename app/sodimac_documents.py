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


def document_query(query: str) -> str:
    """Normalize enrollment synonyms before hybrid search without changing the index."""
    normalized = normalize(query)
    if re.search(r"\b(aplicar|postular|ingresar|unirme|registr\w*|inscri\w*|afili\w*)\b", normalized):
        return "Inscripción CES requisitos formulario modalidades presencial tiendas virtual web " + query
    return query


def compact_excerpt(passages: list[dict], query: str = "") -> str:
    """Bounded extractive fallback: keep complete sentences, never cut a condition in half."""
    if not passages:
        return "🔎 No encontré información sobre esa consulta del CES. ¿Me cuentas un poquito más sobre lo que necesitas? 😊"
    wanted = terms(document_query(query))
    candidates = []
    seen = set()
    for passage in passages:
        text = passage["texto"]
        # Join PDF line wraps, preserving paragraphs and list boundaries.
        for paragraph in re.split(r"\n\s*\n|(?=•)", text):
            paragraph = " ".join(paragraph.split()).strip("• ")
            for sentence in [paragraph]:
                if not sentence or sentence in seen or len(sentence) > 500 or sentence.endswith(":"):
                    continue
                seen.add(sentence)
                overlap = len(wanted & terms(sentence))
                if not overlap and query:
                    continue
                candidates.append((overlap, sentence))
    candidates.sort(key=lambda item: item[0], reverse=True)
    selected = []
    remaining = 1050
    for _, sentence in candidates:
        if len(sentence) + 4 <= remaining:
            selected.append("• " + sentence)
            remaining -= len(sentence) + 4
        if len(selected) == 4:
            break
    if not selected:
        return "🔎 ¿Qué detalle del CES te gustaría conocer? Así puedo darte una respuesta más precisa. 😊"
    return "📌 " + "\n\n".join(selected) + "\n\n💬 ¿Te ayudo con algo más sobre el CES?"
