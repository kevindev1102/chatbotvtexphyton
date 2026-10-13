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



def customer_text(text: str) -> str:
    """Remove legal cross references without removing the surrounding requirements."""
    text = re.sub(
        r"requisitos indicados en la secci[oó]n\s+\d+(?:\.\d+)*\)?(?:\s+de Inscripci[oó]n)?",
        "requisitos de inscripción", text, flags=re.IGNORECASE,
    )
    text = re.sub(r"\([^)]*\b(?:ver|seg[uú]n)\s+(?:detalle\s+en\s+)?(?:la\s+)?secci[oó]n[^)]*\)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\(en adelante[^)]*\)", "", text, flags=re.IGNORECASE)
    return " ".join(text.split())


def enrollment_reply(passages: list[dict], query: str) -> str | None:
    if document_query(query) == query:
        return None
    source = normalize(" ".join(" ".join(p["texto"].split()) for p in passages))
    points = []
    if all(word in source for word in ("formulario", "web", "presentacion en tienda")):
        points.append("📝 Puedes inscribirte completando el formulario en la web del CES o presentándolo en tienda.")
    if all(word in source for word in ("completar todos los datos", "aceptar los terminos", "envio del formulario")):
        points.append("💻 Para hacerlo en línea, completa tus datos, acepta los términos y condiciones y envía el formulario.")
    if all(word in source for word in ("mayores de 18", "residentes en peru", "dni", "extranjeria", "vigente")):
        points.append("✅ Debes ser mayor de 18 años, residir en Perú y tener DNI o carné de extranjería vigente.")
    if "relacionadas y/o dedicarse profesionalmente a actividades de construccion" in source:
        points.append("🔨 Debes estar relacionado con actividades de construcción o dedicarte profesionalmente a ellas.")
    deadline = re.search(r"transcurrido\s+(\d+)\s+dias habiles despues de finalizado el registro", source)
    if deadline:
        points.append(f"⏳ Tu condición de socio activo se habilita {deadline.group(1)} días hábiles después de finalizar el registro.")
    if not points:
        return None
    return "📌 Para inscribirte al CES:\n\n" + "\n\n".join(points) + "\n\n💬 ¿Te ayudo con algo más sobre el CES?"

def compact_excerpt(passages: list[dict], query: str = "") -> str:
    """Bounded extractive fallback: keep complete sentences, never cut a condition in half."""
    if not passages:
        return "🔎 No encontré información sobre esa consulta del CES. ¿Me cuentas un poquito más sobre lo que necesitas? 😊"
    enrollment = enrollment_reply(passages, query)
    if enrollment:
        return enrollment
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
            selected.append("• " + customer_text(sentence))
            remaining -= len(sentence) + 4
        if len(selected) == 4:
            break
    if not selected:
        return "🔎 ¿Qué detalle del CES te gustaría conocer? Así puedo darte una respuesta más precisa. 😊"
    return "📌 " + "\n\n".join(selected) + "\n\n💬 ¿Te ayudo con algo más sobre el CES?"
