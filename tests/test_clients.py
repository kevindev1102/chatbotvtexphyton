import httpx

from app.clients import CoolboxClient, VtexClient
from app.config import Settings
from app.models import Product, SkuOffer


def settings() -> Settings:
    return Settings(
        port=8080,
        openai_api_key="test",
        openai_model="gpt-4.1-mini",
        openai_base_url="https://api.openai.com/v1",
        vtex_base_url="https://example.vtex.test",
        vtex_app_key="app-key",
        vtex_app_token="app-token",
        coolbox_base_url="https://example.coolbox.test",
        coolbox_username="user",
        coolbox_password="pass",
        connect_timeout_seconds=1,
        response_timeout_seconds=1,
    )


def test_maps_best_available_vtex_offer() -> None:
    client = VtexClient(settings())
    product = client._to_product(
        {
            "productId": "10",
            "productName": "Laptop de prueba",
            "brand": "HP",
            "categories": ["/Computación/Laptops/"],
            "allSpecifications": ["Memoria RAM"],
            "Memoria RAM": ["16 GB"],
            "items": [
                {
                    "itemId": "sku-10",
                    "images": [{"imageUrl": "https://example.test/image.jpg"}],
                    "sellers": [
                        {
                            "sellerId": "1",
                            "commertialOffer": {"Price": 3200, "AvailableQuantity": 0},
                        },
                        {
                            "sellerId": "seller-2",
                            "commertialOffer": {"Price": 3300, "AvailableQuantity": 4},
                        },
                    ],
                }
            ],
        }
    )

    assert str(product.precio) == "3300"
    assert product.stock_disponible == 4
    assert product.disponible is True
    assert product.imagen_url == "https://example.test/image.jpg"
    assert product.caracteristicas["Memoria RAM"] == ["16 GB"]
    assert product.sku_offers[0].sku_id == "sku-10"


def test_vtex_orders_authentication_is_configured() -> None:
    client = VtexClient(settings())

    assert client.can_query_orders is True
    assert client._auth_headers["X-VTEX-API-AppKey"] == "app-key"
    assert client._auth_headers["X-VTEX-API-AppToken"] == "app-token"


async def test_maps_pickup_stores_from_checkout_simulation() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/checkout/pub/orderForms/simulation"
        assert request.headers["X-VTEX-API-AppKey"] == "app-key"
        return httpx.Response(
            200,
            json={
                "logisticsInfo": [
                    {
                        "slas": [
                            {
                                "deliveryChannel": "delivery",
                                "name": "Envío express",
                                "shippingEstimate": "4h",
                            },
                            {
                                "deliveryChannel": "pickup-in-point",
                                "pickupPointId": "store-1",
                                "pickupDistance": 0.25,
                                "shippingEstimate": "2bd",
                                "pickupStoreInfo": {
                                    "friendlyName": "Coolbox Miraflores",
                                    "address": {
                                        "street": "Av. Larco",
                                        "number": "628",
                                        "neighborhood": "Miraflores",
                                        "city": "Lima",
                                    },
                                },
                            }
                        ]
                    }
                ]
            },
        )

    client = VtexClient(settings(), transport=httpx.MockTransport(handler))
    product = Product(
        nombre="iPhone 15",
        sku_offers=[SkuOffer(sku_id="100", seller_id="1", available_quantity=2)],
    )
    try:
        stores = await client.pickup_stores(product, latitude=-12.12, longitude=-77.03)
    finally:
        await client.close()

    assert len(stores) == 1
    assert stores[0].nombre == "Coolbox Miraflores"
    assert stores[0].direccion == "Av. Larco 628"
    assert stores[0].distancia_km == 0.25


async def test_maps_home_delivery_and_pickup_from_checkout_simulation() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "logisticsInfo": [
                    {
                        "slas": [
                            {
                                "deliveryChannel": "delivery",
                                "name": "Envío a domicilio",
                                "shippingEstimate": "2bd",
                            },
                            {
                                "deliveryChannel": "pickup-in-point",
                                "pickupPointId": "store-1",
                                "pickupStoreInfo": {"friendlyName": "Coolbox San Isidro"},
                            },
                        ]
                    }
                ]
            },
        )

    client = VtexClient(settings(), transport=httpx.MockTransport(handler))
    product = Product(
        nombre="iPhone 17 Pro Max",
        disponible=True,
        sku_offers=[SkuOffer(sku_id="100", seller_id="marketplace", available_quantity=2)],
    )
    try:
        availability = await client.location_availability(
            product, latitude=-12.09, longitude=-77.03
        )
    finally:
        await client.close()

    assert availability.disponible_online is True
    assert availability.opciones_envio[0].tiempo_entrega == "2bd"
    assert availability.tiendas_recojo[0].nombre == "Coolbox San Isidro"


async def test_coolbox_refreshes_token_after_unauthorized_response() -> None:
    login_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal login_count
        if request.url.path == "/api/Auth/login":
            login_count += 1
            return httpx.Response(200, json={"token": f"token-{login_count}"})
        if request.headers.get("Authorization") == "Bearer token-1":
            return httpx.Response(401, json={"message": "expired"})
        return httpx.Response(200, json={"result": True, "pedidos": [{"numero": "123"}]})

    client = CoolboxClient(settings(), transport=httpx.MockTransport(handler))
    try:
        response = await client.orders_by_number("123")
    finally:
        await client.close()

    assert response.result is True
    assert login_count == 2


async def test_coolbox_accepts_null_order_collection_when_cic_finds_no_orders() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/Auth/login":
            return httpx.Response(200, json={"token": "token"})
        return httpx.Response(
            200,
            json={
                "result": False,
                "mensaje": "Ud. no tiene pedidos pendientes en este momento.",
                "pedidos": None,
            },
        )

    client = CoolboxClient(settings(), transport=httpx.MockTransport(handler))
    try:
        response = await client.orders_by_number("FLB-3249328509")
    finally:
        await client.close()

    assert response.result is False
    assert response.pedidos == []
    assert response.mensaje == "Ud. no tiene pedidos pendientes en este momento."
