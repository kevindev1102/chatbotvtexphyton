from unittest.mock import AsyncMock

from app.chat import ChatService
from app.errors import ExternalServiceError
from app.intent import IntentService
from app.models import (
    ChatRequest,
    CoolboxResponse,
    HistoryMessage,
    IntentType,
    LocationAvailability,
    PickupStore,
    Product,
    QueryAnalysis,
)


def service() -> tuple[ChatService, AsyncMock, AsyncMock, AsyncMock]:
    vtex = AsyncMock()
    coolbox = AsyncMock()
    ai = AsyncMock()
    return ChatService(IntentService(), vtex, coolbox, ai), vtex, coolbox, ai


async def test_order_query_is_routed_locally_without_ai() -> None:
    chat, vtex, coolbox, ai = service()
    vtex.can_query_orders = False
    coolbox.orders_by_number.return_value = CoolboxResponse.model_validate(
        {
            "result": True,
            "pedidos": [
                {
                    "numero": "1234567890123-01",
                    "estado": "En ruta",
                    "mensajeEstado": "Tu pedido está en camino.",
                }
            ],
        }
    )

    response = await chat.process(ChatRequest(mensaje="¿Dónde está mi pedido 1234567890123-01?"))

    assert response.intencion == IntentType.CONSULTAR_PEDIDO
    assert "En ruta" in response.respuesta
    ai.analyze.assert_not_awaited()
    ai.answer.assert_not_awaited()
    coolbox.orders_by_number.assert_awaited_once_with("1234567890123-01")


async def test_order_number_uses_vtex_oms_when_credentials_are_available() -> None:
    chat, vtex, coolbox, ai = service()
    vtex.can_query_orders = True
    vtex.get_order.return_value = {
        "orderId": "1234567890123-01",
        "status": "handling",
        "statusDescription": "Preparando entrega",
        "creationDate": "2026-07-30T12:00:00Z",
        "items": [{"name": "Audífonos Bluetooth"}],
        "clientProfileData": {"document": "62022494"},
        "shippingData": {"logisticsInfo": [{"shippingEstimateDate": "2026-08-01T18:00:00Z"}]},
    }

    response = await chat.process(ChatRequest(mensaje="¿Dónde está mi pedido 1234567890123-01?"))

    assert response.intencion == IntentType.CONSULTAR_PEDIDO
    assert "Preparando entrega" in response.respuesta
    assert "Audífonos Bluetooth" in response.respuesta
    vtex.get_order.assert_awaited_once_with("1234567890123-01")
    coolbox.orders_by_number.assert_not_awaited()
    ai.analyze.assert_not_awaited()


async def test_order_query_can_use_document_without_order_number() -> None:
    chat, vtex, coolbox, ai = service()
    vtex.can_query_orders = True
    coolbox.orders_by_document.return_value = CoolboxResponse.model_validate(
        {"result": True, "pedidos": [{"numero": "123-01", "estado": "Entregado"}]}
    )

    response = await chat.process(ChatRequest(mensaje="Consulta mis pedidos con DNI 62022494"))

    assert "Entregado" in response.respuesta
    vtex.get_order.assert_not_awaited()
    coolbox.orders_by_document.assert_awaited_once_with("62022494")
    ai.analyze.assert_not_awaited()


async def test_order_query_without_identifier_requests_order_or_document() -> None:
    chat, vtex, coolbox, _ = service()

    response = await chat.process(ChatRequest(mensaje="Quiero consultar mi pedido"))

    assert "número de pedido o tu DNI/RUC/CEX" in response.respuesta
    vtex.get_order.assert_not_awaited()
    coolbox.orders_by_document.assert_not_awaited()


async def test_store_stock_limitation_does_not_call_dependencies() -> None:
    chat, vtex, coolbox, ai = service()

    response = await chat.process(ChatRequest(mensaje="¿En qué tiendas hay stock?"))

    assert response.intencion == IntentType.CONSULTAR_STOCK
    assert "qué ciudad" in response.respuesta.lower()
    vtex.search.assert_not_awaited()
    coolbox.orders_by_number.assert_not_awaited()
    ai.analyze.assert_not_awaited()


async def test_store_stock_uses_checkout_pickup_simulation_with_location() -> None:
    chat, vtex, _, ai = service()
    ai.analyze.side_effect = ExternalServiceError("offline")
    product = Product(id="10", nombre="iPhone 15", disponible=True)
    vtex.search.return_value = [product]
    vtex.location_availability.return_value = LocationAvailability(
        tiendas_recojo=[
            PickupStore(
                id="store-1",
                nombre="Coolbox Pardo II",
                direccion="Avenida José Pardo 221",
                distrito="Miraflores",
                ciudad="Lima",
                distancia_km=0.1,
                tiempo_recojo="3bd",
            )
        ]
    )

    response = await chat.process(
        ChatRequest.model_validate(
            {
                "mensaje": "¿En qué tiendas hay stock del iPhone 15?",
                "ubicacion": {"latitud": -12.12, "longitud": -77.03},
            }
        )
    )

    assert response.intencion == IntentType.CONSULTAR_STOCK
    assert "Coolbox Pardo II" in response.respuesta
    assert "disponible para recojo" in response.respuesta
    assert "3 días hábiles" in response.respuesta
    vtex.location_availability.assert_awaited_once_with(
        product,
        latitude=-12.12,
        longitude=-77.03,
    )


async def test_store_stock_infers_piura_from_webchat_message() -> None:
    chat, vtex, _, ai = service()
    ai.analyze.side_effect = ExternalServiceError("offline")
    product = Product(id="10", nombre="iPhone 15", disponible=True)
    vtex.search.return_value = [product]
    vtex.location_availability.return_value = LocationAvailability(
        tiendas_recojo=[PickupStore(id="piura-1", nombre="Coolbox Piura", ciudad="Piura")]
    )

    response = await chat.process(ChatRequest(mensaje="¿Qué tiendas tienen stock del iPhone 15 en Piura?"))

    assert response.intencion == IntentType.CONSULTAR_STOCK
    assert "Coolbox Piura" in response.respuesta
    assert "cerca de Piura" in response.respuesta
    vtex.location_availability.assert_awaited_once_with(
        product,
        latitude=-5.1945,
        longitude=-80.6328,
    )


async def test_generic_iphone_query_excludes_accessories() -> None:
    chat, vtex, _, ai = service()
    ai.analyze.side_effect = ExternalServiceError("offline")
    vtex.search.return_value = [
        Product(
            id="cable",
            nombre="Cable Lightning para iPhone",
            categoria="Accesorios",
            categorias=["/Accesorios/"],
            disponible=True,
            stock_disponible=100,
        ),
        Product(
            id="phone",
            nombre="Apple iPhone 15 128GB",
            categoria="Celulares",
            categorias=["/Celulares y Smartphones/"],
            disponible=True,
            stock_disponible=4,
            imagen_url="https://cdn.example.test/iphone-15.jpg",
        ),
    ]

    response = await chat.process(ChatRequest(mensaje="¿Tienes stock de iPhones?"))

    assert response.intencion == IntentType.CONSULTAR_STOCK
    assert "Apple iPhone 15" in response.respuesta
    assert "Cable Lightning" not in response.respuesta
    assert "unidades" not in response.respuesta
    assert "disponible en tienda online" in response.respuesta
    assert response.imagen is None


async def test_greeting_before_iphone_query_returns_phones() -> None:
    chat, vtex, _, ai = service()
    ai.analyze.side_effect = ExternalServiceError("offline")
    phone = Product(
        nombre="Apple iPhone 15 128GB",
        categoria="Celulares",
        categorias=["/Celulares y Smartphones/"],
        disponible=True,
    )
    vtex.search.return_value = [phone]

    response = await chat.process(ChatRequest(mensaje="hola que iphones tienes?"))

    assert "Apple iPhone 15" in response.respuesta
    vtex.search.assert_awaited_once_with("iphone", order_by=None)


async def test_clear_catalog_query_uses_only_one_ai_call() -> None:
    chat, vtex, _, ai = service()
    vtex.search_many.return_value = [
        Product(
            nombre="Apple iPhone 15 128GB",
            marca="Apple",
            categoria="Celulares",
            categorias=["/Celulares y Accesorios/Celulares/Apple/"],
            disponible=True,
        )
    ]

    response = await chat.process(
        ChatRequest(mensaje="hola quisiera información sobre celulares Apple")
    )

    assert "Apple iPhone 15" in response.respuesta
    ai.analyze.assert_not_awaited()
    ai.answer.assert_not_awaited()
    assert response.imagen is None


async def test_specific_product_query_returns_only_one_image() -> None:
    chat, vtex, _, ai = service()
    ai.analyze.side_effect = ExternalServiceError("offline")
    vtex.search.return_value = [
        Product(
            nombre="Apple iPhone 15 128GB",
            marca="Apple",
            categorias=["/Celulares y Smartphones/"],
            precio=2999,
            disponible=True,
            imagen_url="https://cdn.example.test/iphone-15.jpg",
        ),
        Product(
            nombre="Apple iPhone 15 256GB",
            marca="Apple",
            categorias=["/Celulares y Smartphones/"],
            precio=3499,
            disponible=True,
            imagen_url="https://cdn.example.test/iphone-15-256.jpg",
        ),
    ]

    response = await chat.process(ChatRequest(mensaje="¿Cuánto cuesta el iPhone 15?"))

    assert response.imagen == "https://cdn.example.test/iphone-15.jpg"


async def test_specific_product_image_is_not_repeated_when_already_in_history() -> None:
    chat, vtex, _, ai = service()
    ai.analyze.side_effect = ExternalServiceError("offline")
    product = Product(
        nombre="Apple iPhone 15 128GB",
        marca="Apple",
        categorias=["/Celulares y Smartphones/"],
        disponible=True,
        imagen_url="https://cdn.example.test/iphone-15.jpg",
    )
    vtex.search.return_value = [product]

    response = await chat.process(
        ChatRequest(
            mensaje="¿Qué características tiene el iPhone 15?",
            historial=[
                HistoryMessage(
                    rol="asistente",
                    contenido="El Apple iPhone 15 128GB está disponible en tienda online.",
                )
            ],
        )
    )

    assert response.imagen is None


async def test_location_only_followup_reuses_product_from_history() -> None:
    chat, vtex, _, ai = service()
    ai.analyze.side_effect = ExternalServiceError("offline")
    product = Product(
        id="phone",
        nombre="Apple iPhone 15 128GB",
        categoria="Celulares",
        categorias=["/Celulares y Smartphones/"],
        disponible=True,
    )
    vtex.search.return_value = [product]
    vtex.location_availability.return_value = LocationAvailability(
        tiendas_recojo=[PickupStore(id="lima-1", nombre="Coolbox Centro Cívico", ciudad="Lima")]
    )

    response = await chat.process(
        ChatRequest(
            mensaje="¿Y en Lima sí hay?",
            historial=[HistoryMessage(rol="usuario", contenido="¿Tienes stock del iPhone 15?")],
        )
    )

    assert response.intencion == IntentType.CONSULTAR_STOCK
    assert "Coolbox Centro Cívico" in response.respuesta
    vtex.search.assert_awaited_once_with("iphone 15", order_by=None)
    vtex.location_availability.assert_awaited_once_with(
        product,
        latitude=-12.0464,
        longitude=-77.0428,
    )


async def test_online_only_product_does_not_claim_it_is_unavailable_in_lima() -> None:
    chat, vtex, _, ai = service()
    ai.analyze.side_effect = ExternalServiceError("offline")
    product = Product(nombre="iPhone 17 Pro Max Cosmic Orange", disponible=True)
    vtex.search.return_value = [product]
    vtex.location_availability.return_value = LocationAvailability(disponible_online=True)

    response = await chat.process(ChatRequest(mensaje="¿Está disponible el iPhone 17 Pro Max en Lima?"))

    assert "sí figura con stock para compra online" in response.respuesta
    assert "No encontré tiendas" not in response.respuesta
    assert "dirección exacta" in response.respuesta


async def test_home_delivery_is_reported_separately_from_store_pickup() -> None:
    chat, vtex, _, ai = service()
    ai.analyze.side_effect = ExternalServiceError("offline")
    product = Product(nombre="iPhone 17 Pro Max Cosmic Orange", disponible=True)
    vtex.search.return_value = [product]
    vtex.location_availability.return_value = LocationAvailability.model_validate(
        {
            "disponible_online": True,
            "opciones_envio": [
                {"nombre": "Envío a domicilio", "tiempo_entrega": "2bd"}
            ],
        }
    )

    response = await chat.process(ChatRequest(mensaje="¿Está disponible el iPhone 17 Pro Max en Lima?"))

    assert "envío a domicilio en Lima" in response.respuesta
    assert "2 días hábiles" in response.respuesta
    assert "recojo en una tienda física" in response.respuesta


async def test_ordinal_followup_uses_product_named_by_assistant() -> None:
    chat, vtex, _, ai = service()
    ai.analyze.side_effect = ExternalServiceError("offline")
    product = Product(nombre="iPad Mini 128GB", precio=2200, disponible=True)
    vtex.search.return_value = [product]

    response = await chat.process(
        ChatRequest(
            mensaje="¿Cuánto cuesta el segundo?",
            historial=[
                HistoryMessage(
                    rol="asistente",
                    contenido="- **iPad 11 128GB**: S/ 1.499,00\n- **iPad Mini 128GB**: S/ 2.200,00",
                )
            ],
        )
    )

    assert response.intencion == IntentType.CONSULTAR_PRECIO
    assert "iPad Mini 128GB" in response.respuesta
    assert response.respuesta.count("•") == 1
    vtex.search.assert_awaited_once_with("ipad mini 128gb", order_by=None)


async def test_ordinal_followup_extracts_only_name_from_unformatted_sales_bullet() -> None:
    chat, vtex, _, ai = service()
    ai.analyze.side_effect = ExternalServiceError("offline")
    product = Product(
        nombre="Audífonos Bluetooth JBL Live Flex 3",
        marca="JBL",
        disponible=True,
        caracteristicas={"Cancelación de ruido": ["Sí"], "Autonomía": ["50 horas"]},
    )
    vtex.search.return_value = [product]

    response = await chat.process(
        ChatRequest(
            mensaje="Sí, dame más características del segundo",
            historial=[
                HistoryMessage(
                    rol="asistente",
                    contenido=(
                        "• JBL Live Buds 3, resistentes al agua IP55 - S/ 599,00\n"
                        "• JBL Live Flex 3 (colores plateado, lila y azul), cancelación de ruido, "
                        "Smart Case, hasta 50 horas de autonomía - S/ 599,00\n"
                        "• JBL Wave Buds, resistentes al agua IP54 - S/ 129,00"
                    ),
                )
            ],
        )
    )

    assert response.intencion == IntentType.CONSULTAR_CARACTERISTICAS
    assert "Cancelación de ruido" in response.respuesta
    vtex.search.assert_awaited_once_with("jbl live flex 3", order_by=None)


def test_explicit_brand_filter_never_falls_back_to_other_brands() -> None:
    products = [
        Product(nombre="Smart TV LG", marca="LG", categorias=["/Televisores/"], disponible=True),
        Product(
            nombre="Smart TV Samsung",
            marca="Samsung",
            categorias=["/Televisores/"],
            disponible=True,
        ),
    ]
    analysis = QueryAnalysis(
        intencion=IntentType.BUSCAR_POR_MARCA,
        busqueda="televisor samsung",
        marca="samsung",
        categoria="televisor",
    )

    result = ChatService._filter_products(products, analysis)

    assert [item.nombre for item in result] == ["Smart TV Samsung"]


def test_local_explicit_query_wins_over_ai_plural_rewrite() -> None:
    local = QueryAnalysis(
        intencion=IntentType.CONSULTAR_STOCK,
        busqueda="laptop lenovo",
        marca="lenovo",
        categoria="laptop",
    )
    ai = QueryAnalysis(
        intencion=IntentType.LISTAR_POR_CATEGORIA,
        busqueda="laptops",
        marca="lenovo",
        categoria="laptop",
    )

    merged = ChatService._merge_analysis(ai, local)

    assert merged.busqueda == "laptop lenovo"


async def test_category_search_adds_broad_fallback_for_vtex() -> None:
    chat, vtex, _, _ = service()
    vtex.search_many.return_value = []
    analysis = QueryAnalysis(
        intencion=IntentType.CONSULTAR_STOCK,
        busqueda="playstation 5",
        categoria="consola",
    )

    await chat._catalog_products(analysis)

    vtex.search_many.assert_awaited_once_with(["playstation 5", "playstation 5 consola", "consola"])
