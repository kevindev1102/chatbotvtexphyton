from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

import httpx

from app.config import Settings
from app.errors import ExternalServiceError
from app.models import (
    CoolboxResponse,
    DeliveryOption,
    LocationAvailability,
    PickupStore,
    Product,
    SkuOffer,
)


def _decimal(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
        return parsed if parsed > 0 else None
    except (InvalidOperation, TypeError):
        return None


class BaseHttpClient:
    def __init__(self, base_url: str, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        timeout = httpx.Timeout(
            settings.response_timeout_seconds,
            connect=settings.connect_timeout_seconds,
        )
        self.http = httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport)

    async def close(self) -> None:
        await self.http.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self.http.request(method, path, **kwargs)
                if response.status_code < 500:
                    response.raise_for_status()
                    return response
                response.raise_for_status()
            except (httpx.TransportError, httpx.HTTPStatusError) as error:
                last_error = error
                retryable = isinstance(error, httpx.TransportError) or (
                    isinstance(error, httpx.HTTPStatusError) and error.response.status_code >= 500
                )
                if not retryable or attempt == 2:
                    break
                await asyncio.sleep(0.25 * (2**attempt))
        raise ExternalServiceError(f"No fue posible consultar {self.http.base_url.host}") from last_error


class VtexClient(BaseHttpClient):
    SEARCH_PATH = "/api/catalog_system/pub/products/search"
    ORDERS_PATH = "/api/oms/pvt/orders"

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        super().__init__(settings.vtex_base_url, settings, transport)
        self._auth_headers = (
            {
                "X-VTEX-API-AppKey": settings.vtex_app_key,
                "X-VTEX-API-AppToken": settings.vtex_app_token,
            }
            if settings.vtex_app_key and settings.vtex_app_token
            else {}
        )

    @property
    def can_query_orders(self) -> bool:
        return bool(self._auth_headers)

    async def search(self, query: str | None, limit: int = 20, order_by: str | None = None) -> list[Product]:
        path = self.SEARCH_PATH if not query else f"{self.SEARCH_PATH}/{quote(query.strip(), safe='')}"
        params: dict[str, Any] = {"_from": 0, "_to": max(0, limit - 1)}
        if order_by:
            params["O"] = order_by
        response = await self._request("GET", path, params=params, headers=self._auth_headers)
        payload = response.json()
        return [self._to_product(item) for item in payload if isinstance(item, dict)]

    async def get_order(self, order_id: str) -> dict[str, Any]:
        if not self.can_query_orders:
            raise ExternalServiceError("No están configuradas las credenciales VTEX OMS")
        safe_order_id = quote(order_id.strip(), safe="-")
        response = await self._request(
            "GET",
            f"{self.ORDERS_PATH}/{safe_order_id}",
            headers=self._auth_headers,
        )
        payload = response.json()
        if not isinstance(payload, dict):
            raise ExternalServiceError("VTEX OMS devolvió una respuesta inválida")
        return payload

    async def list_orders(self, *, page: int = 1, per_page: int = 1) -> dict[str, Any]:
        if not self.can_query_orders:
            raise ExternalServiceError("No están configuradas las credenciales VTEX OMS")
        response = await self._request(
            "GET",
            self.ORDERS_PATH,
            params={
                "page": max(1, page),
                "per_page": min(max(1, per_page), 30),
                "orderBy": "creationDate,desc",
            },
            headers=self._auth_headers,
        )
        payload = response.json()
        if not isinstance(payload, dict):
            raise ExternalServiceError("VTEX OMS devolvió una respuesta inválida")
        return payload

    async def pickup_stores(
        self,
        product: Product,
        *,
        latitude: float,
        longitude: float,
        limit: int = 5,
    ) -> list[PickupStore]:
        availability = await self.location_availability(
            product,
            latitude=latitude,
            longitude=longitude,
            limit=limit,
        )
        return availability.tiendas_recojo

    async def location_availability(
        self,
        product: Product,
        *,
        latitude: float,
        longitude: float,
        limit: int = 5,
    ) -> LocationAvailability:
        offers = sorted(product.sku_offers, key=lambda offer: offer.seller_id != "1")[:5]
        if not offers:
            return LocationAvailability(disponible_online=product.disponible)

        stores: dict[str, PickupStore] = {}
        delivery_options: dict[tuple[str, str | None], DeliveryOption] = {}
        for offer in offers:
            response = await self._request(
                "POST",
                "/api/checkout/pub/orderForms/simulation",
                params={"sc": 1},
                json={
                    "items": [
                        {
                            "id": offer.sku_id,
                            "quantity": 1,
                            "seller": offer.seller_id,
                        }
                    ],
                    # VTEX espera primero longitud y luego latitud.
                    "geoCoordinates": [longitude, latitude],
                    "country": "PER",
                },
                headers=self._auth_headers,
            )
            payload = response.json()
            for logistics in payload.get("logisticsInfo") or []:
                for sla in logistics.get("slas") or []:
                    delivery_channel = sla.get("deliveryChannel")
                    if delivery_channel == "delivery":
                        name = str(sla.get("name") or "Envío a domicilio").strip()
                        estimate = sla.get("shippingEstimate")
                        key = (name, estimate)
                        delivery_options[key] = DeliveryOption(
                            nombre=name,
                            tiempo_entrega=estimate,
                        )
                        continue
                    if delivery_channel != "pickup-in-point":
                        continue
                    store_info = sla.get("pickupStoreInfo") or {}
                    address = store_info.get("address") or {}
                    pickup_id = str(sla.get("pickupPointId") or store_info.get("dockId") or "").strip()
                    name = str(store_info.get("friendlyName") or "").strip()
                    if not pickup_id or not name:
                        continue
                    stores[pickup_id] = PickupStore(
                        id=pickup_id,
                        nombre=name,
                        direccion=self._format_address(address),
                        distrito=address.get("neighborhood"),
                        ciudad=address.get("city"),
                        distancia_km=sla.get("pickupDistance"),
                        tiempo_recojo=sla.get("shippingEstimate"),
                    )
        sorted_stores = sorted(
            stores.values(),
            key=lambda store: store.distancia_km if store.distancia_km is not None else float("inf"),
        )[:limit]
        return LocationAvailability(
            disponible_online=product.disponible,
            opciones_envio=list(delivery_options.values())[:limit],
            tiendas_recojo=sorted_stores,
        )

    async def search_many(self, queries: list[str]) -> list[Product]:
        results = await asyncio.gather(*(self.search(query) for query in queries), return_exceptions=True)
        products: list[Product] = []
        seen: set[str] = set()
        for result in results:
            if isinstance(result, Exception):
                continue
            for product in result:
                key = product.id or product.nombre
                if key not in seen:
                    seen.add(key)
                    products.append(product)
        return products

    def _to_product(self, raw: dict[str, Any]) -> Product:
        offers: list[dict[str, Any]] = []
        sku_offers: list[SkuOffer] = []
        first_image: str | None = None
        for item in raw.get("items") or []:
            images = item.get("images") or []
            if not first_image and images:
                first_image = images[0].get("imageUrl")
            for seller in item.get("sellers") or []:
                offer = seller.get("commertialOffer")
                if isinstance(offer, dict) and _decimal(offer.get("Price")) is not None:
                    offers.append(offer)
                    quantity = offer.get("AvailableQuantity") or 0
                    if item.get("itemId") and seller.get("sellerId") and quantity > 0:
                        sku_offers.append(
                            SkuOffer(
                                sku_id=str(item["itemId"]),
                                seller_id=str(seller["sellerId"]),
                                available_quantity=quantity,
                            )
                        )

        available = [offer for offer in offers if (offer.get("AvailableQuantity") or 0) > 0]
        candidates = available or offers
        best = (
            min(candidates, key=lambda offer: _decimal(offer.get("Price")) or Decimal("Infinity"))
            if candidates
            else {}
        )
        price = _decimal(best.get("Price"))
        stock = best.get("AvailableQuantity")
        categories = raw.get("categories") or []
        specification_names = raw.get("allSpecifications") or []
        specifications = {
            name: [
                str(value)
                for value in (raw.get(name) if isinstance(raw.get(name), list) else [raw.get(name)])
                if value
            ]
            for name in specification_names
            if raw.get(name)
        }
        return Product(
            id=raw.get("productId"),
            nombre=raw.get("productName") or "Producto sin nombre",
            marca=raw.get("brand"),
            categoria=categories[0] if categories else None,
            categorias=categories,
            descripcion=raw.get("description"),
            precio=price,
            moneda="PEN" if price is not None else None,
            stock_disponible=stock,
            disponible=bool(stock and stock > 0),
            caracteristicas=specifications,
            sku_offers=sku_offers,
        )

    @staticmethod
    def _format_address(address: dict[str, Any]) -> str | None:
        parts = [
            str(address.get("street") or "").strip(),
            str(address.get("number") or "").strip(),
        ]
        rendered = " ".join(part for part in parts if part)
        return rendered or None


class CoolboxClient(BaseHttpClient):
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        super().__init__(settings.coolbox_base_url, settings, transport)
        self.username = settings.coolbox_username
        self.password = settings.coolbox_password
        self._token: str | None = None
        self._token_expires_at = datetime.min.replace(tzinfo=UTC)
        self._token_lock = asyncio.Lock()

    async def orders_by_document(self, value: str) -> CoolboxResponse:
        return await self._get("/api/Delivery/WS_CONSULTAPEDIDO_DNI", "docCliente", value)

    async def orders_by_number(self, value: str) -> CoolboxResponse:
        return await self._get("/api/Delivery/WS_CONSULTAPEDIDO_PEDIDO", "pedido", value)

    async def refunds(self, document: str | None, credit_note: str | None) -> CoolboxResponse:
        name, value = ("docCliente", document) if document else ("notaCredito", credit_note)
        return await self._get("/api/Administracion/WS_LISTA_DEVOLUCION", name, value)

    async def chargebacks(self, document: str | None, order: str | None) -> CoolboxResponse:
        name, value = ("docCliente", document) if document else ("pedido", order)
        return await self._get("/api/Delivery/WS_LISTA_EXTORNO", name, value)

    async def service_orders(self, document: str | None, service_order: str | None) -> CoolboxResponse:
        if document:
            return await self._get("/api/CIC/WS_CONSULTAODS_DNI", "docCliente", document)
        return await self._get("/api/CIC/WS_CONSULTAODS_ODS", "ods", service_order)

    async def _get(self, path: str, name: str, value: str | None) -> CoolboxResponse:
        if not value:
            raise ExternalServiceError("Falta el parámetro requerido para consultar Web API Coolbox")
        for attempt in range(2):
            token = await self._get_token()
            try:
                response = await self._request(
                    "GET",
                    path,
                    params={name: value.strip()},
                    headers={"Authorization": f"Bearer {token}"},
                )
                return CoolboxResponse.model_validate(response.json())
            except ExternalServiceError as error:
                cause = error.__cause__
                unauthorized = isinstance(cause, httpx.HTTPStatusError) and cause.response.status_code == 401
                if not unauthorized or attempt == 1:
                    raise
                async with self._token_lock:
                    if self._token == token:
                        self._token = None
                        self._token_expires_at = datetime.min.replace(tzinfo=UTC)
        raise ExternalServiceError("No fue posible renovar la autenticación de Web API Coolbox")

    async def _get_token(self) -> str:
        if self._token and datetime.now(UTC) < self._token_expires_at:
            return self._token
        async with self._token_lock:
            if self._token and datetime.now(UTC) < self._token_expires_at:
                return self._token
            if not self.username or not self.password:
                raise ExternalServiceError("No están configuradas las credenciales de Web API Coolbox")
            response = await self._request(
                "POST",
                "/api/Auth/login",
                json={"Username": self.username, "Password": self.password},
            )
            token = response.json().get("token")
            if not token:
                raise ExternalServiceError("Web API Coolbox no devolvió un token")
            self._token = token
            self._token_expires_at = datetime.now(UTC) + timedelta(hours=23)
            return token
