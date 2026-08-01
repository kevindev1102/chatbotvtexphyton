from unittest.mock import AsyncMock

from app.chat import ChatService
from app.errors import ExternalServiceError
from app.intent import IntentService
from app.models import ChatRequest, CoolboxResponse, IntentType, PickupStore, Product


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


async def test_order_number_uses_vtex_oms_when_credentials_are_available() -> None:
    chat, vtex, coolbox, ai = service()
    vtex.can_query_orders = True
    vtex.get_order.return_value = {
        "orderId": "1234567890123-01",
        "status": "handling",
        "statusDescription": "Preparando entrega",
        "creationDate": "2026-07-30T12:00:00Z",
        "items": [{"name": "Audífonos Bluetooth"}],
        "shippingData": {"logisticsInfo": [{"shippingEstimateDate": "2026-08-01T18:00:00Z"}]},
    }

    response = await chat.process(ChatRequest(mensaje="¿Dónde está mi pedido 1234567890123-01?"))

    assert response.intencion == IntentType.CONSULTAR_PEDIDO
    assert "Preparando entrega" in response.respuesta
    assert "Audífonos Bluetooth" in response.respuesta
    vtex.get_order.assert_awaited_once_with("1234567890123-01")
    coolbox.orders_by_number.assert_not_awaited()
    ai.analyze.assert_not_awaited()


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
    vtex.pickup_stores.return_value = [
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
    vtex.pickup_stores.assert_awaited_once_with(
        product,
        latitude=-12.12,
        longitude=-77.03,
    )


async def test_store_stock_infers_piura_from_webchat_message() -> None:
    chat, vtex, _, ai = service()
    ai.analyze.side_effect = ExternalServiceError("offline")
    product = Product(id="10", nombre="iPhone 15", disponible=True)
    vtex.search.return_value = [product]
    vtex.pickup_stores.return_value = [PickupStore(id="piura-1", nombre="Coolbox Piura", ciudad="Piura")]

    response = await chat.process(ChatRequest(mensaje="¿Qué tiendas tienen stock del iPhone 15 en Piura?"))

    assert response.intencion == IntentType.CONSULTAR_STOCK
    assert "Coolbox Piura" in response.respuesta
    assert "cerca de Piura" in response.respuesta
    vtex.pickup_stores.assert_awaited_once_with(
        product,
        latitude=-5.1945,
        longitude=-80.6328,
    )
