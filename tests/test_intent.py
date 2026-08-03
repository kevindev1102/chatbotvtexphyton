from app.intent import IntentService
from app.models import IntentType


def test_detects_product_price_and_query() -> None:
    analysis = IntentService().analyze("¿Cuánto está el Samsung Galaxy S25 Ultra?")

    assert analysis.intencion == IntentType.CONSULTAR_PRECIO
    assert analysis.busqueda == "samsung galaxy s25 ultra"


def test_detects_order_without_sending_it_to_ai() -> None:
    analysis = IntentService().analyze("¿Dónde está mi pedido 1234567890123-01?")

    assert analysis.intencion == IntentType.CONSULTAR_PEDIDO
    assert analysis.order_id == "1234567890123-01"


def test_detects_document_in_natural_sentence() -> None:
    analysis = IntentService().analyze("Mi DNI es 62022494")

    assert analysis.intencion == IntentType.CONSULTAR_PEDIDO
    assert analysis.document_number == "62022494"


def test_comparison_has_two_queries() -> None:
    analysis = IntentService().analyze("Compara iPhone 15 vs Samsung S24")

    assert analysis.intencion == IntentType.COMPARAR_PRODUCTOS
    assert analysis.productos_comparacion == ["iphone 15", "samsung s24"]


def test_extracts_budget() -> None:
    analysis = IntentService().analyze("Recomiéndame un celular por menos de 1000 soles")

    assert analysis.intencion == IntentType.RECOMENDAR_PRODUCTOS
    assert analysis.busqueda == "celular"
    assert str(analysis.precio_maximo) == "1000"


def test_generic_iphone_stock_has_clean_query_and_phone_category() -> None:
    analysis = IntentService().analyze("¿Tienes stock de iPhones?")

    assert analysis.intencion == IntentType.CONSULTAR_STOCK
    assert analysis.busqueda == "iphone"
    assert analysis.categoria == "celular"


def test_phone_availability_wording_is_stock_intent() -> None:
    analysis = IntentService().analyze("¿Celular iPhone tienes?")

    assert analysis.intencion == IntentType.CONSULTAR_STOCK
    assert analysis.busqueda == "celular iphone"


def test_ipad_query_removes_conversational_noise() -> None:
    analysis = IntentService().analyze("¿Qué iPads tienes en stock? Dime el modelo")

    assert analysis.intencion == IntentType.CONSULTAR_STOCK
    assert analysis.busqueda == "ipad"
    assert analysis.categoria == "tablet"


def test_greeting_does_not_contaminate_product_search() -> None:
    analysis = IntentService().analyze("Hola, ¿qué iPhones tienes?")

    assert analysis.busqueda == "iphone"
    assert analysis.categoria == "celular"


def test_long_greeting_does_not_contaminate_product_search() -> None:
    analysis = IntentService().analyze("Buenas tardes Coolbot, quisiera saber qué laptops Lenovo tienes")

    assert analysis.busqueda == "laptop lenovo"
    assert analysis.marca == "lenovo"
    assert analysis.categoria == "laptop"


def test_product_specs_are_not_misread_as_price_limits() -> None:
    analysis = IntentService().analyze(
        "dime las características del iPhone 17 Pro Max 5G 256GB"
    )

    assert analysis.busqueda == "iphone 17 pro max 5g 256gb"
    assert analysis.precio_minimo is None
    assert analysis.precio_maximo is None
