from __future__ import annotations

import html
import logging
import re
from decimal import Decimal
from typing import Any

from app.ai import OpenAIService
from app.clients import CoolboxClient, VtexClient
from app.errors import ExternalServiceError
from app.intent import IntentService, contains_any, normalize
from app.locations import find_named_location, without_named_location
from app.models import (
    ChatRequest,
    ChatResponse,
    CoolboxResponse,
    IntentType,
    PickupStore,
    Product,
    QueryAnalysis,
)

logger = logging.getLogger(__name__)


class ChatService:
    def __init__(
        self,
        intent_service: IntentService,
        vtex_client: VtexClient,
        coolbox_client: CoolboxClient,
        ai_service: OpenAIService,
    ):
        self.intent = intent_service
        self.vtex = vtex_client
        self.coolbox = coolbox_client
        self.ai = ai_service

    async def process(self, request: ChatRequest) -> ChatResponse:
        message = request.mensaje.strip()
        history = request.historial[-10:]
        asks_store_stock = self._asks_store_availability(message)
        named_location = find_named_location(message) if request.ubicacion is None else None

        if asks_store_stock and request.ubicacion is None and named_location is None:
            return ChatResponse(
                intencion=IntentType.CONSULTAR_STOCK,
                respuesta=(
                    "¿En qué ciudad deseas buscar tiendas con disponibilidad para recojo? "
                    "Por ejemplo: Lima, Piura, Arequipa o Trujillo."
                ),
            )

        analysis_message = without_named_location(message, named_location)
        local_analysis = self.intent.analyze(analysis_message)
        if local_analysis.intencion.is_support:
            return await self._process_support(local_analysis, history, message)

        analysis = local_analysis
        try:
            ai_analysis = await self.ai.analyze(analysis_message, history)
            if not ai_analysis.intencion.is_support:
                analysis = self._merge_analysis(ai_analysis, local_analysis)
        except ExternalServiceError:
            logger.info("Se usará análisis local para la consulta")

        if analysis.requiere_aclaracion or analysis.intencion == IntentType.ACLARACION_REQUERIDA:
            return ChatResponse(
                intencion=IntentType.ACLARACION_REQUERIDA,
                respuesta="¿Podrías indicarme qué producto, marca, categoría o rango de precio quieres consultar?",
            )

        if analysis.intencion == IntentType.CONSULTA_GENERAL:
            try:
                answer = await self.ai.answer(message, history, [])
            except ExternalServiceError:
                answer = "Puedo ayudarte con productos, precios, stock, pedidos y recomendaciones. ¿Qué deseas consultar?"
            return ChatResponse(respuesta=answer, intencion=analysis.intencion)

        try:
            products = await self._catalog_products(analysis)
        except ExternalServiceError:
            return ChatResponse(
                intencion=analysis.intencion,
                respuesta="No puedo consultar el catálogo en este momento 😕. Intenta nuevamente en unos segundos.",
            )

        products = self._filter_products(products, analysis)
        if not products:
            return ChatResponse(
                intencion=analysis.intencion,
                respuesta=self._no_data_message(analysis),
            )

        if asks_store_stock and (request.ubicacion is not None or named_location is not None):
            latitude = request.ubicacion.latitud if request.ubicacion is not None else named_location.latitude
            longitude = (
                request.ubicacion.longitud if request.ubicacion is not None else named_location.longitude
            )
            try:
                selected_product = products[0]
                stores: list[PickupStore] = []
                for candidate in products[:5]:
                    candidate_stores = await self.vtex.pickup_stores(
                        candidate,
                        latitude=latitude,
                        longitude=longitude,
                    )
                    if candidate_stores:
                        selected_product = candidate
                        stores = candidate_stores
                        break
            except ExternalServiceError:
                return ChatResponse(
                    intencion=IntentType.CONSULTAR_STOCK,
                    respuesta=(
                        "No pude consultar las tiendas disponibles en este momento 😕. "
                        "Intenta nuevamente en unos segundos."
                    ),
                )
            return ChatResponse(
                intencion=IntentType.CONSULTAR_STOCK,
                respuesta=self._pickup_stores_answer(
                    selected_product, stores, named_location.name if named_location else None
                ),
            )

        if analysis.intencion in {
            IntentType.CONSULTAR_PRECIO,
            IntentType.CONSULTAR_STOCK,
            IntentType.CONSULTAR_CARACTERISTICAS,
        }:
            answer = self._deterministic_product_answer(analysis.intencion, products)
        else:
            try:
                answer = await self.ai.answer(message, history, products)
            except ExternalServiceError:
                answer = self._local_product_answer(products)
        return ChatResponse(respuesta=answer, intencion=analysis.intencion)

    async def _process_support(
        self, analysis: QueryAnalysis, history: list[Any], message: str
    ) -> ChatResponse:
        asks_other = contains_any(
            message,
            "otro pedido",
            "nuevo pedido",
            "pedido diferente",
            "otra devolucion",
            "otro extorno",
            "otra orden de servicio",
            "otra ods",
        )
        if not asks_other and not any(
            (analysis.order_id, analysis.document_number, analysis.credit_note, analysis.service_order)
        ):
            for item in reversed(history):
                if item.openai_role() != "user":
                    continue
                previous = self.intent.analyze(item.contenido)
                analysis.order_id = analysis.order_id or previous.order_id
                analysis.document_number = analysis.document_number or previous.document_number
                analysis.credit_note = analysis.credit_note or previous.credit_note
                analysis.service_order = analysis.service_order or previous.service_order
                if any(
                    (
                        analysis.order_id,
                        analysis.document_number,
                        analysis.credit_note,
                        analysis.service_order,
                    )
                ):
                    break

        missing = self._missing_support_parameter(analysis)
        if missing:
            return ChatResponse(intencion=analysis.intencion, respuesta=missing)

        try:
            if analysis.intencion == IntentType.CONSULTAR_PEDIDO:
                if analysis.order_id and self.vtex.can_query_orders:
                    order = await self.vtex.get_order(analysis.order_id)
                    return ChatResponse(
                        intencion=analysis.intencion,
                        respuesta=self._vtex_order_answer(order),
                    )
                response = (
                    await self.coolbox.orders_by_number(analysis.order_id)
                    if analysis.order_id
                    else await self.coolbox.orders_by_document(analysis.document_number or "")
                )
            elif analysis.intencion == IntentType.CONSULTAR_DEVOLUCION_DINERO:
                response = await self.coolbox.refunds(analysis.document_number, analysis.credit_note)
            elif analysis.intencion == IntentType.CONSULTAR_EXTORNO:
                response = await self.coolbox.chargebacks(analysis.document_number, analysis.order_id)
            else:
                response = await self.coolbox.service_orders(analysis.document_number, analysis.service_order)
        except ExternalServiceError:
            return ChatResponse(
                intencion=analysis.intencion,
                respuesta="Estoy teniendo problemas para consultar esa información 😕. Intenta nuevamente en unos segundos.",
            )
        return ChatResponse(
            intencion=analysis.intencion, respuesta=self._support_answer(analysis.intencion, response)
        )

    def _vtex_order_answer(self, order: dict[str, Any]) -> str:
        order_id = str(order.get("orderId") or "").strip()
        status = str(order.get("statusDescription") or order.get("status") or "").strip()
        creation_date = str(order.get("creationDate") or "").strip()
        items = [
            str(item.get("name") or "").strip()
            for item in order.get("items") or []
            if isinstance(item, dict) and item.get("name")
        ]
        logistics = (order.get("shippingData") or {}).get("logisticsInfo") or []
        delivery_date = next(
            (
                str(info.get("deliveryWindow") or info.get("shippingEstimateDate") or "").strip()
                for info in logistics
                if isinstance(info, dict) and (info.get("deliveryWindow") or info.get("shippingEstimateDate"))
            ),
            "",
        )

        lines = ["¡Listo! Encontré esta información de tu pedido:"]
        if order_id:
            lines.append(f"• Pedido: {order_id}")
        if status:
            lines.append(f"• Estado: {status}")
        if creation_date:
            lines.append(f"• Fecha de creación: {creation_date}")
        if delivery_date:
            lines.append(f"• Entrega estimada: {delivery_date}")
        if items:
            lines.append(f"• Productos: {', '.join(items[:3])}")
        return "\n".join(lines)

    async def _catalog_products(self, analysis: QueryAnalysis) -> list[Product]:
        if analysis.intencion == IntentType.COMPARAR_PRODUCTOS and analysis.productos_comparacion:
            return await self.vtex.search_many(analysis.productos_comparacion)
        if analysis.intencion in {IntentType.RANKING_PRODUCTOS, IntentType.PRODUCTOS_DESTACADOS}:
            return await self.vtex.search(
                analysis.busqueda,
                limit=10,
                order_by="OrderByTopSaleDESC",
            )
        order = "OrderByPriceASC" if analysis.ordenar_por_menor_precio else None
        return await self.vtex.search(
            analysis.busqueda or analysis.marca or analysis.categoria, order_by=order
        )

    @staticmethod
    def _merge_analysis(ai: QueryAnalysis, local: QueryAnalysis) -> QueryAnalysis:
        # Los extractores deterministas conservan precios y términos claros cuando el modelo los omite.
        ai.busqueda = ai.busqueda or local.busqueda
        ai.marca = ai.marca or local.marca
        ai.categoria = ai.categoria or local.categoria
        ai.precio_minimo = ai.precio_minimo if ai.precio_minimo is not None else local.precio_minimo
        ai.precio_maximo = ai.precio_maximo if ai.precio_maximo is not None else local.precio_maximo
        ai.ordenar_por_menor_precio = ai.ordenar_por_menor_precio or local.ordenar_por_menor_precio
        ai.productos_comparacion = ai.productos_comparacion or local.productos_comparacion
        return ai

    @staticmethod
    def _filter_products(products: list[Product], analysis: QueryAnalysis) -> list[Product]:
        result = products
        if analysis.marca:
            brand = normalize(analysis.marca)
            branded = [product for product in result if normalize(product.marca) == brand]
            result = branded or result
        if analysis.precio_minimo is not None:
            result = [
                product
                for product in result
                if product.precio is not None and product.precio >= analysis.precio_minimo
            ]
        if analysis.precio_maximo is not None:
            result = [
                product
                for product in result
                if product.precio is not None and product.precio <= analysis.precio_maximo
            ]
        if analysis.modelos_excluidos:
            exclusions = [normalize(value) for value in analysis.modelos_excluidos]
            result = [
                product
                for product in result
                if not any(excluded and excluded in normalize(product.nombre) for excluded in exclusions)
            ]
        result.sort(key=lambda product: (not product.disponible, product.precio or Decimal("Infinity")))
        count = min(analysis.cantidad or 5, 5)
        return result[:count]

    @staticmethod
    def _missing_support_parameter(analysis: QueryAnalysis) -> str | None:
        if analysis.intencion == IntentType.CONSULTAR_PEDIDO and not (
            analysis.order_id or analysis.document_number
        ):
            return "Indícame el número de pedido o tu DNI/RUC/CEX para consultar su estado."
        if analysis.intencion == IntentType.CONSULTAR_DEVOLUCION_DINERO and not (
            analysis.credit_note or analysis.document_number
        ):
            return "Indícame la nota de crédito o tu DNI/RUC/CEX para consultar la devolución."
        if analysis.intencion == IntentType.CONSULTAR_EXTORNO and not (
            analysis.order_id or analysis.document_number
        ):
            return "Indícame el número de pedido o tu DNI/RUC/CEX para consultar el extorno."
        if analysis.intencion == IntentType.CONSULTAR_ORDEN_SERVICIO and not (
            analysis.service_order or analysis.document_number
        ):
            return "Indícame la ODS o tu DNI/RUC/CEX para consultar la orden de servicio."
        return None

    def _support_answer(self, intent: IntentType, response: CoolboxResponse) -> str:
        if intent == IntentType.CONSULTAR_PEDIDO:
            if not response.result or not response.pedidos:
                return "Por ahora no encuentro pedidos pendientes con ese dato. Revisa el número e intenta nuevamente."
            lines = ["¡Listo! Encontré esta información de tu pedido:"]
            for item in response.pedidos[:3]:
                details = [
                    self._field(item, "numero", "Pedido"),
                    self._field(item, "estado", "Estado"),
                    self._field(item, "mensajeEstado", "Detalle"),
                    self._field(item, "fechaEntrega", "Entrega"),
                    self._field(item, "productos", "Productos", 180),
                ]
                lines.append(" · ".join(value for value in details if value))
            return "\n".join(lines)
        if intent in {IntentType.CONSULTAR_DEVOLUCION_DINERO, IntentType.CONSULTAR_EXTORNO}:
            items = (
                response.notas_credito
                if intent == IntentType.CONSULTAR_DEVOLUCION_DINERO
                else response.pedidos
            )
            if not response.result or not items:
                return "No encuentro información activa con ese dato. Revísalo e intenta nuevamente."
            title = "devolución" if intent == IntentType.CONSULTAR_DEVOLUCION_DINERO else "extorno"
            lines = [f"¡Listo! Encontré información de tu {title}:"]
            for item in items[:3]:
                rendered = [
                    self._field(item, "notaCredito", "Nota"),
                    self._field(item, "numero", "Pedido"),
                    self._field(item, "estado", "Estado"),
                    self._field(item, "mensajeEstado", "Detalle"),
                    self._field(item, "fechaPagoReal", "Pago"),
                ]
                lines.append(" · ".join(value for value in rendered if value))
            return "\n".join(lines)
        if not response.result or not response.ordenes_servicio:
            return "No encuentro una orden de servicio con ese dato. Revísalo e intenta nuevamente."
        lines = ["¡Listo! Encontré esta información de tu orden de servicio:"]
        for item in response.ordenes_servicio[:3]:
            rendered = [
                self._field(item, "ordenServicio", "ODS"),
                self._field(item, "fecha", "Fecha"),
                self._field(item, "fechaResolucion", "Resolución"),
                self._field(item, "mensaje", "Estado"),
            ]
            lines.append(" · ".join(value for value in rendered if value))
        return "\n".join(lines)

    @staticmethod
    def _field(item: dict[str, Any], key: str, label: str, limit: int = 120) -> str:
        value = str(item.get(key) or "").strip()
        value = re.sub(r"\s+", " ", html.unescape(value))
        if not value:
            return ""
        return f"{label}: {value[:limit]}"

    def _deterministic_product_answer(self, intent: IntentType, products: list[Product]) -> str:
        if intent == IntentType.CONSULTAR_PRECIO:
            lines = ["Estos son los precios que encontré:"]
            for product in products[:3]:
                price = self._price(product.precio) if product.precio is not None else "precio no disponible"
                lines.append(f"• {product.nombre}: {price}")
            return "\n".join(lines)
        if intent == IntentType.CONSULTAR_STOCK:
            lines = ["Disponibilidad general del catálogo:"]
            for product in products[:3]:
                status = (
                    f"{product.stock_disponible} unidades"
                    if product.disponible and product.stock_disponible is not None
                    else "sin stock disponible"
                )
                lines.append(f"• {product.nombre}: {status}")
            return "\n".join(lines)
        product = products[0]
        lines = [f"Características principales de {product.nombre}:"]
        for name, values in list(product.caracteristicas.items())[:6]:
            lines.append(f"• {name}: {', '.join(values[:3])}")
        if len(lines) == 1 and product.descripcion:
            clean_description = re.sub(r"<[^>]+>", " ", product.descripcion)
            lines.append(re.sub(r"\s+", " ", html.unescape(clean_description)).strip()[:500])
        return "\n".join(lines)

    def _local_product_answer(self, products: list[Product]) -> str:
        lines = ["Encontré estas opciones:"]
        for product in products[:3]:
            price = self._price(product.precio) if product.precio is not None else "precio no disponible"
            stock = "disponible" if product.disponible else "agotado"
            lines.append(f"• {product.nombre} — {price} — {stock}")
        return "\n".join(lines)

    def _pickup_stores_answer(
        self, product: Product, stores: list[PickupStore], searched_location: str | None = None
    ) -> str:
        if not stores:
            location_text = f" cerca de {searched_location}" if searched_location else " cercanas"
            return (
                f"No encontré tiendas{location_text} con {product.nombre} disponible para recojo. "
                "Puedes probar con otra ubicación o producto."
            )

        location_text = f" cerca de {searched_location}" if searched_location else ""
        lines = [f"Encontré {product.nombre} disponible para recojo{location_text} en:"]
        for store in stores[:5]:
            location = ", ".join(value for value in (store.direccion, store.distrito, store.ciudad) if value)
            distance = f" · {store.distancia_km:.1f} km" if store.distancia_km is not None else ""
            estimate = (
                f" · recojo estimado: {self._humanize_shipping_estimate(store.tiempo_recojo)}"
                if store.tiempo_recojo
                else ""
            )
            lines.append(f"• {store.nombre} — {location}{distance}{estimate}")
        lines.append("La disponibilidad puede cambiar antes de completar la compra.")
        return "\n".join(lines)

    @staticmethod
    def _humanize_shipping_estimate(value: str) -> str:
        match = re.fullmatch(r"(\d+)bd", value)
        if match:
            days = int(match.group(1))
            return f"{days} día hábil" if days == 1 else f"{days} días hábiles"
        match = re.fullmatch(r"(\d+)h", value)
        if match:
            hours = int(match.group(1))
            return f"{hours} hora" if hours == 1 else f"{hours} horas"
        return value

    @staticmethod
    def _price(value: Decimal) -> str:
        return f"S/ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    @staticmethod
    def _no_data_message(analysis: QueryAnalysis) -> str:
        query = analysis.busqueda or analysis.marca or analysis.categoria or "esa búsqueda"
        return (
            f"No encontré productos para “{query}” con los filtros solicitados. ¿Quieres probar otra opción?"
        )

    @staticmethod
    def _asks_store_availability(message: str) -> bool:
        return contains_any(
            message,
            "en que tienda",
            "en que tiendas",
            "que tiendas tienen",
            "donde hay stock",
            "en que local",
            "stock por tienda",
            "stock por local",
            "disponibilidad por tienda",
        )
