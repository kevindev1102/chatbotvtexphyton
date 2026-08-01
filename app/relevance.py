from __future__ import annotations

from decimal import Decimal

from app.intent import normalize
from app.models import Product, QueryAnalysis

ACCESSORY_TERMS = {
    "adaptador",
    "audifono",
    "cable",
    "cargador",
    "case",
    "correa",
    "estuche",
    "funda",
    "lamina",
    "mica",
    "power bank",
    "protector",
    "soporte",
    "vidrio",
}

FAMILY_TERMS: dict[str, set[str]] = {
    "celular": {"celular", "smartphone", "telefono movil", "iphone", "galaxy"},
    "tablet": {"tablet", "ipad"},
    "laptop": {"laptop", "notebook", "macbook"},
    "monitor": {"monitor"},
    "televisor": {"televisor", "television", "smart tv", "tv"},
    "impresora": {"impresora", "multifuncional"},
    "consola": {"consola", "playstation", "ps5", "xbox", "nintendo switch"},
    "audio": {"parlante", "barra de sonido", "audio", "airpods", "audifono"},
}

SPECIFIC_PRODUCT_TERMS = {
    "airpods",
    "galaxy",
    "ipad",
    "iphone",
    "macbook",
    "nintendo switch",
    "playstation",
    "ps5",
    "xbox",
}

QUERY_NOISE = {
    "dime",
    "modelo",
    "modelos",
    "opcion",
    "opciones",
    "equipo",
    "equipos",
    "catalogo",
}


def _contains_term(text: str, term: str) -> bool:
    return f" {term} " in f" {text} "


def _requested_family(analysis: QueryAnalysis, query: str) -> str | None:
    if analysis.categoria in FAMILY_TERMS:
        return analysis.categoria
    for family, terms in FAMILY_TERMS.items():
        if any(_contains_term(query, term) for term in terms):
            return family
    return None


def _specific_terms(query: str) -> list[str]:
    return [term for term in SPECIFIC_PRODUCT_TERMS if _contains_term(query, term)]


def _is_accessory(text: str) -> bool:
    return any(_contains_term(text, term) for term in ACCESSORY_TERMS)


def _matches_family(product: Product, family: str) -> bool:
    searchable = normalize(" ".join([product.nombre, product.categoria or "", *product.categorias]))
    return any(_contains_term(searchable, term) for term in FAMILY_TERMS[family])


def rank_catalog_products(products: list[Product], analysis: QueryAnalysis) -> list[Product]:
    query = normalize(analysis.busqueda or analysis.marca or analysis.categoria)
    family = _requested_family(analysis, query)
    specific_terms = _specific_terms(query)
    accessory_request = _is_accessory(query)
    query_tokens = {
        token
        for token in query.split()
        if len(token) > 1 and token not in QUERY_NOISE and not token.isdigit()
    }

    ranked: list[tuple[int, Product]] = []
    for product in products:
        name = normalize(product.nombre)
        categories = normalize(" ".join(product.categorias))

        if family and not accessory_request and not _matches_family(product, family):
            continue
        if specific_terms and not all(_contains_term(name, term) for term in specific_terms):
            continue
        if (family or specific_terms) and not accessory_request and _is_accessory(name):
            continue

        name_matches = sum(1 for token in query_tokens if _contains_term(name, token))
        category_matches = sum(1 for token in query_tokens if _contains_term(categories, token))
        score = name_matches * 10 + category_matches * 3
        if query and query in name:
            score += 30
        if family and _matches_family(product, family):
            score += 20
        if specific_terms:
            score += 40
        if query_tokens and score == 0:
            continue
        ranked.append((score, product))

    ranked.sort(
        key=lambda item: (
            -item[0],
            not item[1].disponible,
            item[1].precio if item[1].precio is not None else Decimal("Infinity"),
        )
    )
    return [product for _, product in ranked]
