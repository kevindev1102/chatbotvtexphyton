import httpx

from app.clients import VtexClient
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
