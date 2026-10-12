"""Read public Sodimac HTML; never search or follow redirects outside its host."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin, urlsplit

import httpx
from pydantic import BaseModel, Field

from app.errors import ExternalServiceError

BASE_URL = "https://www.sodimac.com.pe"


def store_url(value: str) -> str:
    url = urljoin(BASE_URL, value)
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.hostname != "www.sodimac.com.pe"
        or parts.port not in {None, 443}
        or parts.username
        or parts.password
        or not parts.path.startswith("/sodimac-pe/")
    ):
        raise ExternalServiceError("La URL no pertenece a Sodimac Perú")
    return url


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.active = False
        self.buffer: list[str] = []
        self.documents: list[dict] = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            attributes = dict(attrs)
            self.active = attributes.get("id") == "__NEXT_DATA__"
            self.buffer = []

    def handle_data(self, data):
        if self.active:
            self.buffer.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.active:
            try:
                self.documents.append(json.loads(unescape("".join(self.buffer))))
            except (ValueError, TypeError):
                pass
            self.active = False


class StoreProduct(BaseModel):
    nombre: str
    url: str
    marca: str = ""
    vendedor: str = ""
    precios: list[str] = Field(default_factory=list)
    imagen: str | None = None
    detalles: list[str] = Field(default_factory=list)


def read_products(html: str) -> list[StoreProduct]:
    parser = Scripts()
    parser.feed(html)
    products = []
    seen = set()
    recognized = False
    for document in parser.documents:
        page = document.get("props", {}).get("pageProps", {})
        results = page.get("results")
        if not isinstance(results, list):
            continue
        recognized = True
        for row in results:
            if not isinstance(row, dict) or not row.get("displayName") or not row.get("url"):
                continue
            try:
                url = store_url(row["url"])
            except (ExternalServiceError, ValueError):
                continue
            identity = urlsplit(url).path
            if identity in seen:
                continue
            seen.add(identity)
            prices = []
            for price in row.get("prices", []):
                labels = {
                    "internetPrice": "Internet",
                    "eventPrice": "Oferta",
                    "normalPrice": "Regular",
                    "cmrPrice": "Con tarjeta CMR",
                }
                label = labels.get(price.get("type"))
                # Unknown conditions or ranges are not represented as a single price.
                amounts = price.get("price", [])
                if not label or len(amounts) != 1 or price.get("symbol", "").strip() != "S/":
                    continue
                try:
                    amount = Decimal(str(amounts[0]).replace(",", ""))
                    if not amount.is_finite() or amount < 0:
                        continue
                except InvalidOperation:
                    continue
                if price.get("crossed"):
                    label += " (tachado en la web)"
                prices.append(f"{label}: S/ {amount:,.2f}")
            images = row.get("mediaUrls") or []
            image = next(
                (
                    x
                    for x in images
                    if isinstance(x, str)
                    and urlsplit(x).scheme == "https"
                    and urlsplit(x).hostname == "media.sodimac.com.pe"
                ),
                None,
            )
            details = [x for x in row.get("topSpecifications", []) if isinstance(x, str)]
            products.append(
                StoreProduct(
                    nombre=row["displayName"],
                    url=url,
                    marca=row.get("brand") or "",
                    vendedor=row.get("sellerName") or "",
                    precios=prices,
                    imagen=image,
                    detalles=details[:6],
                )
            )
    if not recognized:
        raise ExternalServiceError("Sodimac no entregó una lista verificable de productos")
    return products


class SodimacCatalog:
    def __init__(self, timeout: float = 15):
        self.client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            headers={"User-Agent": "SodimacChatbot/1.0", "Accept": "text/html"},
        )

    async def close(self):
        await self.client.aclose()

    async def _html(self, url: str) -> str:
        try:
            for _ in range(6):
                store_url(url)
                async with self.client.stream("GET", url) as response:
                    if response.is_redirect:
                        url = store_url(urljoin(url, response.headers.get("location", "")))
                        continue
                    response.raise_for_status()
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > 10_000_000:
                            raise ExternalServiceError("La respuesta de Sodimac es demasiado grande")
                    return content.decode("utf-8", errors="replace")
            raise ExternalServiceError("Demasiadas redirecciones de Sodimac")
        except (httpx.HTTPError, ValueError) as error:
            raise ExternalServiceError("No fue posible consultar Sodimac") from error

    async def search(self, query: str) -> list[StoreProduct]:
        url = BASE_URL + "/sodimac-pe/buscar?" + urlencode({"Ntt": query[:200]})
        products = read_products(await self._html(url))
        from app.sodimac_documents import terms

        wanted = terms(query)
        ranked = sorted(products, key=lambda p: len(wanted & terms(p.nombre)), reverse=True)
        return [p for p in ranked if wanted & terms(p.nombre)][:5]

    async def details(self, product: StoreProduct) -> StoreProduct:
        parser = Scripts()
        parser.feed(await self._html(product.url))
        for document in parser.documents:
            data = document.get("props", {}).get("pageProps", {}).get("productData", {})
            specs = data.get("attributes", {}).get("specifications", [])
            product.detalles = [
                f"{x['name']}: {x['value']}"
                for x in specs
                if isinstance(x, dict) and x.get("name") and x.get("value")
            ][:20]
            if product.detalles:
                return product
        raise ExternalServiceError("No se pudieron verificar las características")
