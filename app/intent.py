from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

from app.models import IntentType, QueryAnalysis

KNOWN_BRANDS = {
    "apple",
    "samsung",
    "xiaomi",
    "motorola",
    "lenovo",
    "hp",
    "asus",
    "acer",
    "lg",
    "sony",
    "huawei",
    "honor",
    "nintendo",
    "epson",
    "canon",
    "logitech",
    "jbl",
    "anker",
}

FILLER_WORDS = {
    "hola",
    "holaa",
    "buenas",
    "buenos",
    "dias",
    "tardes",
    "noches",
    "oye",
    "hey",
    "como",
    "estas",
    "coolbot",
    "bot",
    "ayuda",
    "ayudame",
    "quiero",
    "quisiera",
    "necesito",
    "busco",
    "buscar",
    "buscame",
    "encuentra",
    "lista",
    "listame",
    "muestrame",
    "mostrar",
    "dame",
    "ver",
    "los",
    "las",
    "el",
    "la",
    "un",
    "una",
    "unos",
    "unas",
    "de",
    "del",
    "para",
    "por",
    "con",
    "que",
    "cual",
    "es",
    "mas",
    "y",
    "o",
    "a",
    "favor",
    "me",
    "puedes",
    "podrias",
    "producto",
    "productos",
    "mejores",
    "mejor",
    "top",
    "ranking",
    "recomienda",
    "recomiendame",
    "recomendacion",
    "sugiere",
    "sugerencia",
    "sugerencias",
    "sugerir",
    "sugerirme",
    "precio",
    "precios",
    "stock",
    "disponibilidad",
    "caracteristicas",
    "caracteristica",
    "cuanto",
    "cuesta",
    "vale",
    "hay",
    "tienen",
    "tiene",
    "tienes",
    "comparar",
    "compara",
    "comparando",
    "comparame",
    "compararme",
    "comparacion",
    "versus",
    "vs",
    "entre",
    "opciones",
    "destacados",
    "destacado",
    "disponible",
    "disponibles",
    "unidad",
    "unidades",
    "existencias",
    "estudiar",
    "estudio",
    "economico",
    "economica",
    "viajar",
    "viaje",
    "bueno",
    "buena",
    "barato",
    "barata",
    "mi",
    "casa",
    "esta",
    "dime",
    "modelo",
    "modelos",
    "venden",
    "quedan",
    "cueste",
    "consulta",
    "consultar",
    "saber",
    "porfa",
    "en",
    "si",
    "donde",
    "tienda",
    "tiendas",
    "local",
    "locales",
}


def normalize(text: str | None) -> str:
    if not text:
        return ""
    value = unicodedata.normalize("NFD", text)
    value = "".join(character for character in value if unicodedata.category(character) != "Mn")
    value = re.sub(r"[^a-z0-9ñ\s-]", " ", value.lower())
    value = re.sub(r"\s+", " ", value).strip()
    corrections = {
        r"\bstok\b": "stock",
        r"\bay\b": "hay",
        r"\bcaracterizticas\b": "caracteristicas",
        r"\bdisponibilida\b": "disponibilidad",
    }
    for pattern, replacement in corrections.items():
        value = re.sub(pattern, replacement, value)
    return value


def contains_any(text: str, *phrases: str) -> bool:
    normalized = f" {normalize(text)} "
    return any(f" {normalize(phrase)} " in normalized for phrase in phrases)


def is_purchase_location_request(text: str) -> bool:
    normalized = normalize(text)
    return bool(
        re.search(r"\bdonde\b.*\b(?:compr(?:ar\w*|o)|consig\w*|vend\w*)\b", normalized)
        or re.search(
            r"\ben\s+que\s+(?:tienda|tiendas|local|locales)\b.*"
            r"\b(?:compr(?:ar\w*|o)|consig\w*|vend\w*)\b",
            normalized,
        )
    )


def _contains_transfer_request(text: str) -> bool:
    normalized = normalize(text)
    human_role = bool(
        re.search(r"\b(?:asesor|asesora|agente|operador|operadora|persona|humano|humana)\b", normalized)
    )
    if normalized in {"asesor", "asesora", "agente", "operador", "operadora", "persona", "humano"}:
        return True
    transfer_word = bool(re.search(r"\btransf(?:er\w*|ier\w*)\b", normalized))
    financial_transfer = contains_any(
        normalized,
        "transferencia bancaria",
        "transferencia de dinero",
        "transferencia de pago",
    )
    request_word = contains_any(
        normalized,
        "quiero",
        "necesito",
        "solicito",
        "deseo",
        "puedes",
        "podrias",
        "por favor",
    )
    if transfer_word and not financial_transfer and (human_role or request_word):
        return True
    human_contact = bool(
        re.search(
            r"\b(?:habl\w*|comunic\w*|contact\w*|deriv\w*|pas\w*)\b.*"
            r"\b(?:asesor|asesora|agente|operador|operadora|persona|humano|humana)\b",
            normalized,
        )
    )
    if human_contact:
        return True
    return contains_any(
        text,
        "hablar con un asesor",
        "hablar con asesor",
        "hablar con un agente",
        "hablar con agente",
        "hablar con una persona",
        "hablar con alguien",
        "asesor humano",
        "agente humano",
        "atencion humana",
        "asesoria humana",
        "comunicarme con un asesor",
        "comunicarme con un agente",
        "contactar un asesor",
        "contactar a un asesor",
        "quiero un asesor",
        "necesito un asesor",
        "asesor por favor",
        "hablar con operador",
        "hablar con un operador",
        "transferirme",
        "transfiereme",
        "me transfieras",
        "quiero que me transfieran",
        "derivame",
        "pasame con",
    )


def _is_only_social_message(text: str, phrases: tuple[str, ...], allowed_words: set[str]) -> bool:
    normalized = normalize(text)
    if not any(contains_any(normalized, phrase) for phrase in phrases):
        return False
    return set(normalized.split()).issubset(allowed_words)


def _is_greeting(text: str) -> bool:
    return _is_only_social_message(
        text,
        (
            "hola",
            "holaa",
            "buenos dias",
            "buenas tardes",
            "buenas noches",
            "buenas",
            "hey",
            "te estoy saludando",
            "te saludo",
            "solo saludaba",
            "vengo a saludar",
        ),
        {
            "hola",
            "holaa",
            "buenos",
            "buenas",
            "dias",
            "tardes",
            "noches",
            "hey",
            "como",
            "estas",
            "que",
            "tal",
            "coolbot",
            "bot",
            "te",
            "estoy",
            "saludando",
            "saludo",
            "solo",
            "saludaba",
            "vengo",
            "a",
            "saludar",
        },
    )


def _is_farewell(text: str) -> bool:
    return _is_only_social_message(
        text,
        (
            "adios",
            "hasta luego",
            "hasta pronto",
            "nos vemos",
            "chau",
            "chao",
            "eso es todo",
            "nada mas",
            "muchas gracias",
            "gracias",
            "gracias por todo",
            "me despido",
            "bye",
        ),
        {
            "adios",
            "hasta",
            "luego",
            "pronto",
            "nos",
            "vemos",
            "chau",
            "chao",
            "eso",
            "es",
            "todo",
            "nada",
            "mas",
            "gracias",
            "muchas",
            "muchisimas",
            "por",
            "la",
            "ayuda",
            "tu",
            "listo",
            "seria",
            "me",
            "despido",
            "bye",
        },
    )


def _singular(word: str) -> str:
    if len(word) <= 4 or word.isdigit() or word in {"airpods", "series"} or word.endswith("pods"):
        return word
    if word.endswith(("ores", "ares", "eres", "les")):
        return word[:-2]
    if word.endswith("s"):
        return word[:-1]
    return word


def _extract_identifier(pattern: str, text: str, flags: int = re.IGNORECASE) -> str | None:
    match = re.search(pattern, text, flags)
    return match.group(1) if match else None


def _extract_prices(text: str) -> tuple[Decimal | None, Decimal | None]:
    normalized = normalize(text)

    def decimal(value: str | None) -> Decimal | None:
        try:
            return Decimal(value.replace(",", ".")) if value else None
        except InvalidOperation:
            return None

    between = re.search(r"\bentre\s+(\d+(?:[.,]\d+)?)\s+(?:y|a)\s+(\d+(?:[.,]\d+)?)", normalized)
    if between:
        return decimal(between.group(1)), decimal(between.group(2))
    minimum = _extract_identifier(
        r"(?:desde|mayor(?:es)?\s+a|mas\s+de|mas\s+que|minimo|min)\s+"
        r"(\d+(?:[.,]\d+)?)(?![a-z0-9])",
        normalized,
    )
    maximum = _extract_identifier(
        r"(?:hasta|menor(?:es)?\s+a|menos\s+de|debajo\s+de|por\s+debajo\s+de|maximo|max)\s+"
        r"(\d+(?:[.,]\d+)?)(?![a-z0-9])",
        normalized,
    )
    return decimal(minimum), decimal(maximum)


def _support_identifiers(text: str) -> dict[str, str | None]:
    order_id = _extract_identifier(
        r"(?:pedido|orden|compra)\s*(?:n(?:ro|umero)?\.?|#|id)?\s*([a-z0-9-]*\d{6,}[a-z0-9-]*(?:-\d{2})?)",
        text,
    )
    if not order_id:
        order_id = _extract_identifier(r"\b([a-z0-9-]*\d{10,}[a-z0-9-]*-\d{2})\b", text)
    document = _extract_identifier(
        r"(?:dni|ruc|cex|documento)\s*(?:n(?:ro|umero)?\.?|#)?\s*([a-z0-9]{8,12})",
        text,
    )
    if not document:
        document = _extract_identifier(r"\b(\d{8}|\d{11})\b", text)
    credit_note = _extract_identifier(r"\b(?:nota\s*(?:de\s*)?credito|ncr)?\s*([a-z]\d{3}-\d{6,})\b", text)
    service_order = _extract_identifier(r"\b(ODS\d{6,})\b", text)
    return {
        "orderId": order_id,
        "documentNumber": document,
        "creditNote": credit_note.upper() if credit_note else None,
        "serviceOrder": service_order.upper() if service_order else None,
    }


def _detect_intent(text: str, identifiers: dict[str, str | None]) -> IntentType:
    if _contains_transfer_request(text):
        return IntentType.TRANSFERENCIA
    if _is_farewell(text):
        return IntentType.DESPEDIDA
    if _is_greeting(text):
        return IntentType.SALUDO
    if is_purchase_location_request(text):
        return IntentType.CONSULTAR_STOCK
    if identifiers["serviceOrder"] or contains_any(
        text, "ods", "orden de servicio", "servicio tecnico", "reparacion"
    ):
        return IntentType.CONSULTAR_ORDEN_SERVICIO
    if contains_any(text, "extorno", "extornos", "contracargo", "reverso de pago"):
        return IntentType.CONSULTAR_EXTORNO
    if contains_any(text, "devolucion de dinero", "nota de credito", "reembolso", "reembolsos", "ncr"):
        return IntentType.CONSULTAR_DEVOLUCION_DINERO
    if (
        identifiers["documentNumber"] and contains_any(text, "dni", "ruc", "cex", "documento")
    ) or contains_any(
        text,
        "pedido",
        "pedidos",
        "orden",
        "compra",
        "seguimiento",
        "tracking",
        "despacho",
        "envio",
        "entrega",
    ):
        return IntentType.CONSULTAR_PEDIDO
    natural_comparison = bool(
        re.search(r"\b(?:cual|que)\b.*\b(?:mejor|conviene)\b", text)
        and re.search(r"\s+o\s+", text)
    )
    similarity_comparison = contains_any(
        text,
        "caracteristicas iguales",
        "caracteristicas similares",
        "mismas caracteristicas",
        "similar a",
        "parecido a",
        "parecida a",
        "equivalente a",
        "alternativa a",
    )
    comparison_request = bool(
        re.search(
            r"\bcompar(?:a(?:me|ndo)?|ar(?:me)?|acion|e(?:me)?)\b",
            text,
        )
    )
    if (
        natural_comparison
        or similarity_comparison
        or comparison_request
        or contains_any(text, "vs", "versus", "contra")
    ):
        return IntentType.COMPARAR_PRODUCTOS
    if contains_any(text, "para que sirve", "que es", "como funciona", "diferencia entre", "explicame"):
        return IntentType.CONSULTA_GENERAL
    if contains_any(
        text,
        "regalo",
        "obsequio",
        "recomienda",
        "recomiendame",
        "recomendar",
        "sugiere",
        "sugerir",
        "sugerirme",
        "sugerencia",
        "sugerencias",
        "conviene",
    ):
        return IntentType.RECOMENDAR_PRODUCTOS
    if contains_any(text, "stock", "disponible", "disponibilidad", "hay", "quedan", "en tienda", "tienes"):
        return IntentType.CONSULTAR_STOCK
    if contains_any(
        text,
        "precio",
        "precios",
        "cuesta",
        "cuanto esta",
        "a cuanto",
        "vale",
        "costo",
        "barato",
        "economico",
        "caro",
        "presupuesto",
        "menos de",
        "hasta",
        "por debajo de",
        "debajo de",
    ):
        return IntentType.CONSULTAR_PRECIO
    if contains_any(
        text,
        "caracteristicas",
        "especificaciones",
        "ficha tecnica",
        "memoria",
        "procesador",
        "pantalla",
        "camara",
    ):
        return IntentType.CONSULTAR_CARACTERISTICAS
    if contains_any(text, "ranking", "top", "mejores", "mas vendidos"):
        return IntentType.RANKING_PRODUCTOS
    if contains_any(text, "destacados", "destacado", "ofertas", "promociones", "novedades"):
        return IntentType.PRODUCTOS_DESTACADOS
    if contains_any(text, "categoria", "linea"):
        return IntentType.LISTAR_POR_CATEGORIA
    if any(re.search(rf"\b{re.escape(brand)}\b", normalize(text)) for brand in KNOWN_BRANDS):
        return IntentType.BUSCAR_POR_MARCA
    return IntentType.BUSCAR_PRODUCTO


def _comparison_terms(text: str) -> list[str]:
    normalized = normalize(text)
    similarity_terms = _similarity_comparison_terms(normalized)
    if similarity_terms:
        return similarity_terms
    comparison_verb = r"compar(?:a(?:me|ndo)?|ar(?:me)?|acion|e(?:me)?)"
    cleaned = re.sub(
        rf"^.*?\b{comparison_verb}\b(?:\s+(?:entre|el|la|los|las|un|una|de|del))?\s*",
        "",
        normalized,
    )
    if re.search(r"\s+o\s+", cleaned) and re.search(r"\b(?:mejor|conviene)\b", cleaned):
        cleaned = re.sub(r"^.*?\b(?:mejor|conviene)\b\s*", "", cleaned)
    parts = re.split(r"\s+(?:vs|versus|contra|con|o|y)\s+", cleaned, maxsplit=1)
    return [_clean_search(part) for part in parts if _clean_search(part)] if len(parts) == 2 else []


def _similarity_comparison_terms(text: str) -> list[str]:
    marker = (
        r"(?:caracteristicas\s+(?:iguales|similares)(?:\s+a\s+las?)?\s+(?:de|del)|"
        r"mismas\s+caracteristicas\s+(?:de|del)|"
        r"(?:similar|parecid[oa]|equivalente|alternativa)\s+a(?:\s+la|\s+el)?)"
    )
    reference_match = re.search(rf"\b{marker}\s+(.+)$", text)
    if not reference_match:
        return []
    reference = _clean_search(reference_match.group(1))
    prefix = text[: reference_match.start()]
    brand = next(
        (candidate for candidate in KNOWN_BRANDS if re.search(rf"\b{candidate}\b", prefix)),
        None,
    )
    category = next(
        (
            candidate
            for candidate in ("laptop", "celular", "tablet", "monitor", "televisor", "consola")
            if re.search(rf"\b{candidate}", prefix)
        ),
        None,
    )
    alternative = " ".join(value for value in (brand, category) if value)
    if not reference or not alternative:
        return []
    return [reference, alternative]


def _clean_search(text: str) -> str:
    value = normalize(text)
    value = re.sub(
        r"\b(?:entre\s+\d+(?:[.,]\d+)?\s+(?:y|a)\s+\d+(?:[.,]\d+)?|"
        r"(?:desde|mayor(?:es)?\s+a|mas\s+de|mas\s+que|minimo|min|hasta|menor(?:es)?\s+a|"
        r"menos\s+de|debajo\s+de|por\s+debajo\s+de|maximo|max)\s+\d+(?:[.,]\d+)?)\b",
        " ",
        value,
    )
    value = re.sub(r"\b(?:soles|pen|pulgadas|pulgada)\b", " ", value)
    words = [_singular(word) for word in value.split() if word not in FILLER_WORDS]
    result = " ".join(words).strip()
    translations = {
        "algo escuchar musica": "audio",
        "escuchar musica": "audio",
        "algo cargar celular auto": "cargador auto",
        "algo hacer videollamada desde pc": "webcam",
        "adaptador enchufe cuando viajo": "adaptador universal",
        "convertir inteligente": "casa inteligente",
        "televisor grande sala": "tv 65",
    }
    return translations.get(result, result)


class IntentService:
    def analyze(self, message: str) -> QueryAnalysis:
        normalized = normalize(message)
        identifiers = _support_identifiers(message)
        intent = _detect_intent(normalized, identifiers)
        minimum, maximum = _extract_prices(normalized)
        if maximum and contains_any(
            normalized, "buena camara", "buen procesador", "buena bateria", "para fotos"
        ):
            intent = IntentType.RECOMENDAR_PRODUCTOS

        comparisons: list[str] = []
        if intent.is_support:
            query = next((value for value in identifiers.values() if value), None)
        elif intent == IntentType.COMPARAR_PRODUCTOS:
            comparisons = _comparison_terms(message)
            query = " ".join(comparisons)
        else:
            query = _clean_search(message)

        brand = next(
            (brand for brand in KNOWN_BRANDS if re.search(rf"\b{re.escape(brand)}\b", normalized)), None
        )
        category = next(
            (
                category
                for category in (
                    "laptop",
                    "celular",
                    "tablet",
                    "monitor",
                    "televisor",
                    "impresora",
                    "consola",
                    "audio",
                )
                if re.search(rf"\b{category}", normalized)
            ),
            None,
        )
        if re.search(r"\biphones?\b", normalized):
            category = "celular"
        elif re.search(r"\bipads?\b", normalized):
            category = "tablet"
        elif re.search(r"\b(?:playstation|ps5|xbox|nintendo\s+switch)\b", normalized):
            category = "consola"
        ambiguous = (
            not intent.is_support
            and not is_purchase_location_request(normalized)
            and intent
            not in {
                IntentType.SALUDO,
                IntentType.DESPEDIDA,
                IntentType.TRANSFERENCIA,
                IntentType.CONSULTA_GENERAL,
                IntentType.PRODUCTOS_DESTACADOS,
            }
            and (not query or query in {"producto", "productos", "equipo", "algo", "opcion"})
        )
        if ambiguous:
            intent = IntentType.ACLARACION_REQUERIDA

        return QueryAnalysis.model_validate(
            {
                "intencion": intent,
                "busqueda": query or None,
                "marca": brand,
                "categoria": category,
                "precioMinimo": minimum,
                "precioMaximo": maximum,
                "ordenarPorMenorPrecio": contains_any(
                    normalized, "barato", "barata", "economico", "economica"
                ),
                "requiereAclaracion": ambiguous,
                "productosComparacion": comparisons,
                "modelosExcluidos": [],
                "cantidad": None,
                "almacenamientoMinimoGb": None,
                **identifiers,
            }
        )
