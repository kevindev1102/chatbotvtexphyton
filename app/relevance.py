from __future__ import annotations

import re
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

GAME_TERMS = {"juego", "juegos", "videojuego", "videojuegos", "gaming software"}
MAIN_DEVICE_CATEGORY_TERMS = {
    "celular",
    "smartphone",
    "tablet",
    "laptop",
    "consola",
    "televisor",
    "impresora",
}
ACCESSORY_CATEGORY_TERMS = {
    "accesorio",
    "cable",
    "cargador",
    "case",
    "estuche",
    "funda",
    "protector",
}

ACCESSORY_MATCH_TERMS: dict[str, set[str]] = {
    "cargador": {"cargador", "cubo de carga", "adaptador de corriente"},
    "funda": {"funda", "folio cover"},
    "audifono": {"audifono", "headphone", "earbuds"},
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
    "informacion",
    "sobre",
}


def _contains_term(text: str, term: str) -> bool:
    padded = f" {text} "
    if f" {term} " in padded:
        return True
    if " " not in term:
        return any(f" {variant} " in padded for variant in (f"{term}s", f"{term}es"))
    return False


def _requested_family(analysis: QueryAnalysis, query: str) -> str | None:
    if analysis.categoria in FAMILY_TERMS:
        return analysis.categoria
    for family, terms in FAMILY_TERMS.items():
        if any(_contains_term(query, term) for term in terms):
            return family
    return None


def _specific_terms(query: str) -> list[str]:
    return [term for term in SPECIFIC_PRODUCT_TERMS if _contains_term(query, term)]


def _model_number_terms(query: str) -> list[str]:
    return re.findall(
        r"\b(?:galaxy|google pixel|ipad|iphone|nintendo switch|pixel|playstation|switch|watch|xbox)"
        r"\s*(\d{1,2})\b",
        query,
    )


def _model_code_terms(query: str) -> list[str]:
    return [
        token
        for token in query.split()
        if re.search(r"[a-z]", token)
        and re.search(r"\d", token)
        and token not in {"4g", "5g", "4k"}
    ]


def _is_accessory(text: str) -> bool:
    return any(_contains_term(text, term) for term in ACCESSORY_TERMS)


def _matches_family(product: Product, family: str) -> bool:
    name = normalize(product.nombre)
    if family == "celular":
        if any(_contains_term(name, term) for term in FAMILY_TERMS[family]):
            return True
        return any(
            "/celular/" in category.lower().replace(" ", "")
            or "/celulares/" in category.lower().replace(" ", "")
            for category in product.categorias
        )
    searchable = normalize(" ".join([product.nombre, product.categoria or "", *product.categorias]))
    return any(_contains_term(searchable, term) for term in FAMILY_TERMS[family])


def rank_catalog_products(products: list[Product], analysis: QueryAnalysis) -> list[Product]:
    query = normalize(analysis.busqueda or analysis.marca or analysis.categoria)
    family = _requested_family(analysis, query)
    specific_terms = _specific_terms(query)
    model_numbers = _model_number_terms(query)
    model_codes = _model_code_terms(query)
    accessory_request = _is_accessory(query)
    requested_accessories = [term for term in ACCESSORY_TERMS if _contains_term(query, term)]
    query_tokens = {
        token
        for token in query.split()
        if len(token) > 1 and token not in QUERY_NOISE and not token.isdigit()
    }

    ranked: list[tuple[int, Product]] = []
    for product in products:
        name = normalize(product.nombre)
        categories = normalize(" ".join(product.categorias))
        description = normalize(product.descripcion)
        brand = normalize(product.marca)
        product_text = f"{name} {categories} {description}"

        if family and not accessory_request and not _matches_family(product, family):
            continue
        if family == "consola" and any(_contains_term(categories, term) for term in GAME_TERMS):
            continue
        if family == "consola" and _contains_term(categories, "accesorio"):
            continue
        if specific_terms:
            target_brands = {
                "iphone": "apple",
                "ipad": "apple",
                "macbook": "apple",
                "galaxy": "samsung",
                "playstation": "sony",
                "ps5": "sony",
                "xbox": "microsoft",
            }
            matches_specific = all(
                _contains_term(product_text, term) or brand == target_brands.get(term)
                for term in specific_terms
            )
            if not matches_specific:
                continue
        if model_numbers and not all(_contains_term(name, number) for number in model_numbers):
            continue
        if model_codes and not all(_contains_term(name, code) for code in model_codes):
            continue
        if requested_accessories and not any(
            any(
                _contains_term(product_text, candidate)
                for candidate in ACCESSORY_MATCH_TERMS.get(term, {term})
            )
            for term in requested_accessories
        ):
            continue
        is_main_device_category = any(_contains_term(categories, term) for term in MAIN_DEVICE_CATEGORY_TERMS)
        category_without_mixed_parent = categories.replace("celulares y accesorios", "")
        is_accessory_category = any(
            _contains_term(category_without_mixed_parent, term)
            for term in ACCESSORY_CATEGORY_TERMS
        )
        if accessory_request and is_main_device_category and not is_accessory_category:
            continue
        if family and not accessory_request and is_accessory_category:
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
        if model_numbers or model_codes:
            score += 60
        if query_tokens and score == 0:
            continue
        ranked.append((score, product))

    def price_order(product: Product) -> Decimal:
        if product.precio is None:
            return Decimal("Infinity")
        return product.precio if analysis.ordenar_por_menor_precio else -product.precio

    ranked.sort(
        key=lambda item: (
            -item[0],
            not item[1].disponible,
            price_order(item[1]),
        )
    )
    return [product for _, product in ranked]
