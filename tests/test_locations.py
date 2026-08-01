from app.locations import find_named_location, without_named_location


def test_finds_city_ignoring_accents_and_case() -> None:
    location = find_named_location("¿Hay una tienda cerca en HUÁNUCO?")

    assert location is not None
    assert location.name == "Huánuco"


def test_removes_city_from_catalog_query() -> None:
    message = "¿Qué tiendas tienen stock del iPhone 15 en Piura?"
    location = find_named_location(message)

    assert without_named_location(message, location) == "que tiendas tienen stock del iphone 15"
