from __future__ import annotations

import asyncio
import html
import logging
import re
from decimal import Decimal
from typing import Any

from app.ai import OpenAIService
from app.clients import CoolboxClient, VtexClient
from app.errors import ExternalServiceError
from app.intent import IntentService, contains_any, is_purchase_location_request, normalize
from app.locations import find_named_location, without_named_location
from app.models import (
    ChatRequest,
    ChatResponse,
    CoolboxResponse,
    IntentType,
    LocationAvailability,
    Product,
    QueryAnalysis,
)
from app.relevance import ACCESSORY_TERMS, rank_catalog_products

logger = logging.getLogger(__name__)


class ChatService:
    AI_STEP_TIMEOUT_SECONDS = 5

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
        if self._accepts_transfer_offer(message, history):
            return self._transfer_response()
        asks_store_stock = self._asks_store_availability(message)
        named_location = find_named_location(message) if request.ubicacion is None else None

        analysis_message = without_named_location(message, named_location)
        current_analysis = self.intent.analyze(analysis_message)
        referenced_product = self._referenced_product_from_history(message, history)
        if not referenced_product and self._is_implicit_product_followup(message, current_analysis):
            referenced_product = self._latest_unambiguous_product(history)
        if referenced_product:
            intent_prefix = self._referenced_product_intent_prefix(message, current_analysis)
            analysis_message = f"{intent_prefix} {referenced_product}"
            current_analysis = self.intent.analyze(analysis_message)
        if named_location is not None and self._is_location_only_followup(current_analysis):
            prior_product = self._latest_unambiguous_product(history)
            prior_query = prior_product or self._prior_catalog_query(history)
            if prior_query:
                analysis_message = f"{prior_query} stock"
                asks_store_stock = True
                referenced_product = prior_product or referenced_product
        if named_location is not None and current_analysis.intencion == IntentType.CONSULTAR_STOCK:
            asks_store_stock = True

        if asks_store_stock and request.ubicacion is None and named_location is None:
            return ChatResponse(
                intencion=IntentType.CONSULTAR_STOCK,
                respuesta=(
                    "¿En qué ciudad deseas buscar tiendas con disponibilidad para recojo? "
                    "Por ejemplo: Lima, Piura, Arequipa o Trujillo."
                ),
            )

        local_analysis = self.intent.analyze(analysis_message)
        if referenced_product:
            local_analysis.cantidad = 1
            # El nombre recuperado del historial puede contener especificaciones como
            # "máx. 5 kg". No deben convertirse en presupuestos que excluyan el producto.
            local_analysis.precio_minimo = None
            local_analysis.precio_maximo = None
        if local_analysis.intencion == IntentType.SALUDO:
            return ChatResponse(
                intencion=IntentType.SALUDO,
                respuesta=(
                    "¡Hola! 👋 Soy Coolbot, el asistente virtual de Coolbox Perú. "
                    "Puedo ayudarte con productos, precios, stock, tiendas y pedidos. "
                    "¿Qué deseas consultar?"
                ),
            )
        if local_analysis.intencion == IntentType.DESPEDIDA:
            return ChatResponse(
                intencion=IntentType.DESPEDIDA,
                respuesta="¡Gracias por comunicarte con Coolbox! 😊 Que tengas un excelente día.",
                es_despedida=True,
            )
        if local_analysis.intencion == IntentType.TRANSFERENCIA:
            return self._transfer_response()
        if local_analysis.intencion.is_support:
            return await self._process_support(local_analysis, history, message)

        analysis = local_analysis
        used_ai_analysis = False
        if self._needs_ai_analysis(local_analysis):
            try:
                async with asyncio.timeout(self.AI_STEP_TIMEOUT_SECONDS):
                    ai_analysis = await self.ai.analyze(analysis_message, history)
                if not ai_analysis.intencion.is_support:
                    analysis = self._merge_analysis(ai_analysis, local_analysis)
                    used_ai_analysis = True
            except (ExternalServiceError, TimeoutError):
                logger.info("Se usará análisis local para la consulta")

        if analysis.requiere_aclaracion or analysis.intencion == IntentType.ACLARACION_REQUERIDA:
            return ChatResponse(
                intencion=IntentType.ACLARACION_REQUERIDA,
                respuesta="¿Podrías indicarme qué producto, marca, categoría o rango de precio quieres consultar?",
            )

        if analysis.intencion == IntentType.CONSULTA_GENERAL:
            try:
                async with asyncio.timeout(self.AI_STEP_TIMEOUT_SECONDS):
                    answer = await self.ai.answer(message, history, [])
            except (ExternalServiceError, TimeoutError):
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
                availability = LocationAvailability(disponible_online=selected_product.disponible)
                for candidate in products[:5]:
                    candidate_availability = await self.vtex.location_availability(
                        candidate,
                        latitude=latitude,
                        longitude=longitude,
                    )
                    if candidate_availability.opciones_envio or candidate_availability.tiendas_recojo:
                        selected_product = candidate
                        availability = candidate_availability
                        break
                    if candidate is selected_product:
                        availability = candidate_availability
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
                respuesta=self._location_availability_answer(
                    selected_product,
                    availability,
                    named_location.name if named_location else None,
                ),
                imagen=self._single_product_image([selected_product], analysis, history),
            )

        if analysis.intencion == IntentType.COMPARAR_PRODUCTOS:
            answer = self._comparison_answer(products, message)
        elif analysis.intencion in {
            IntentType.BUSCAR_PRODUCTO,
            IntentType.LISTAR_POR_CATEGORIA,
            IntentType.BUSCAR_POR_MARCA,
            IntentType.CONSULTAR_PRECIO,
            IntentType.CONSULTAR_STOCK,
            IntentType.CONSULTAR_CARACTERISTICAS,
        }:
            if analysis.intencion in {
                IntentType.BUSCAR_PRODUCTO,
                IntentType.LISTAR_POR_CATEGORIA,
                IntentType.BUSCAR_POR_MARCA,
            }:
                answer = self._local_product_answer(products)
            else:
                answer = self._deterministic_product_answer(analysis.intencion, products)
                if analysis.intencion == IntentType.CONSULTAR_CARACTERISTICAS:
                    answer = self._with_transfer_offer(answer)
        else:
            try:
                if used_ai_analysis:
                    raise ExternalServiceError("Ya se utilizó IA para analizar la consulta")
                async with asyncio.timeout(self.AI_STEP_TIMEOUT_SECONDS):
                    answer = await self.ai.answer(message, history, products)
            except (ExternalServiceError, TimeoutError):
                answer = self._local_product_answer(products)
        return ChatResponse(
            respuesta=answer,
            intencion=analysis.intencion,
            imagen=self._single_product_image(products, analysis, history),
        )

    @staticmethod
    def _transfer_response() -> ChatResponse:
        return ChatResponse(
            intencion=IntentType.TRANSFERENCIA,
            respuesta=(
                "Claro, te comunicaré con un asesor de Coolbox para que continúe ayudándote. 👩‍💻"
            ),
            es_transferencia=True,
        )

    @staticmethod
    def _with_transfer_offer(answer: str) -> str:
        return (
            f"{answer}\n\nSi deseas asesoría personalizada sobre este producto, "
            "puedo transferirte con un asesor. ¿Quieres que te comunique?"
        )

    @staticmethod
    def _accepts_transfer_offer(message: str, history: list[Any]) -> bool:
        affirmation = normalize(message) in {
            "si",
            "si por favor",
            "claro",
            "ok",
            "okay",
            "de acuerdo",
            "quiero",
            "acepto",
        }
        if not affirmation:
            return False
        for item in reversed(history):
            if getattr(item, "rol", "") not in {"asistente", "assistant"}:
                continue
            previous = getattr(item, "contenido", "")
            return contains_any(
                previous,
                "puedo transferirte con un asesor",
                "quieres que te comunique",
                "hablar con un asesor",
            )
        return False

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
                response = await self.coolbox.refunds(analysis.document_number, None)
                response.notas_credito = self._matching_items(
                    response.notas_credito,
                    analysis.credit_note,
                    "notaCredito",
                    "numeroNotaCredito",
                    "ncr",
                )
            elif analysis.intencion == IntentType.CONSULTAR_EXTORNO:
                response = await self.coolbox.chargebacks(analysis.document_number, None)
                response.pedidos = self._matching_items(
                    response.pedidos,
                    analysis.order_id,
                    "numero",
                    "pedido",
                    "orderId",
                )
            else:
                response = await self.coolbox.service_orders(analysis.document_number, None)
                response.ordenes_servicio = self._matching_items(
                    response.ordenes_servicio,
                    analysis.service_order,
                    "ordenServicio",
                    "ods",
                )
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
        order = "OrderByPriceASC" if analysis.ordenar_por_menor_precio else "OrderByPriceDESC"
        if analysis.intencion == IntentType.COMPARAR_PRODUCTOS and analysis.productos_comparacion:
            queries = list(analysis.productos_comparacion)
            if analysis.categoria:
                for comparison in analysis.productos_comparacion:
                    comparison_analysis = self.intent.analyze(comparison)
                    if comparison_analysis.marca:
                        queries.append(f"{comparison_analysis.marca} {analysis.categoria}")
            return await self.vtex.search_many(list(dict.fromkeys(queries)), order_by=order)
        if analysis.intencion in {IntentType.RANKING_PRODUCTOS, IntentType.PRODUCTOS_DESTACADOS}:
            return await self.vtex.search(
                analysis.busqueda,
                limit=10,
                order_by="OrderByTopSaleDESC",
            )
        if analysis.marca and analysis.categoria:
            queries = [
                value
                for value in dict.fromkeys(
                    [
                        analysis.busqueda,
                        f"{analysis.marca} {analysis.categoria}",
                        f"{analysis.categoria} {analysis.marca}",
                        analysis.marca,
                    ]
                )
                if value
            ]
            return await self.vtex.search_many(queries, order_by=order)
        normalized_query = normalize(analysis.busqueda)
        requested_accessories = [term for term in ACCESSORY_TERMS if contains_any(normalized_query, term)]
        if requested_accessories:
            queries = [value for value in dict.fromkeys([analysis.busqueda, *requested_accessories]) if value]
            return await self.vtex.search_many(queries, order_by=order)
        if analysis.categoria == "consola" and analysis.busqueda != analysis.categoria:
            queries = [
                value
                for value in dict.fromkeys(
                    [
                        analysis.busqueda,
                        f"{analysis.busqueda} {analysis.categoria}",
                        analysis.categoria,
                    ]
                )
                if value
            ]
            return await self.vtex.search_many(queries, order_by=order)
        return await self.vtex.search(
            analysis.busqueda or analysis.marca or analysis.categoria, order_by=order
        )

    @staticmethod
    def _needs_ai_analysis(analysis: QueryAnalysis) -> bool:
        return analysis.requiere_aclaracion or analysis.intencion == IntentType.ACLARACION_REQUERIDA

    @staticmethod
    def _merge_analysis(ai: QueryAnalysis, local: QueryAnalysis) -> QueryAnalysis:
        # Los extractores deterministas conservan precios y términos claros cuando el modelo los omite.
        local_query = normalize(local.busqueda)
        reference_words = {
            "anterior",
            "ese",
            "esa",
            "este",
            "esta",
            "primero",
            "primera",
            "segundo",
            "segunda",
            "tercero",
            "tercera",
            "otro",
            "otra",
        }
        has_explicit_local_query = bool(local_query) and not set(local_query.split()).issubset(
            reference_words
        )
        ai.busqueda = local.busqueda if has_explicit_local_query else (ai.busqueda or local.busqueda)
        # Solo se aplica una marca estricta cuando el usuario la escribió explícitamente.
        # Inferir Apple desde "iPhone" excluiría accesorios compatibles de otras marcas.
        ai.marca = local.marca
        ai.categoria = local.categoria or ai.categoria
        ai.precio_minimo = ai.precio_minimo if ai.precio_minimo is not None else local.precio_minimo
        ai.precio_maximo = ai.precio_maximo if ai.precio_maximo is not None else local.precio_maximo
        ai.ordenar_por_menor_precio = ai.ordenar_por_menor_precio or local.ordenar_por_menor_precio
        ai.productos_comparacion = ai.productos_comparacion or local.productos_comparacion
        ai.cantidad = local.cantidad or ai.cantidad
        return ai

    @staticmethod
    def _filter_products(products: list[Product], analysis: QueryAnalysis) -> list[Product]:
        if analysis.intencion == IntentType.COMPARAR_PRODUCTOS and analysis.productos_comparacion:
            return ChatService._filter_comparison_products(products, analysis)
        result = rank_catalog_products(products, analysis)
        if analysis.marca:
            brand = normalize(analysis.marca)
            result = [product for product in result if normalize(product.marca) == brand]
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
        count = min(analysis.cantidad or 5, 5)
        return result[:count]

    @staticmethod
    def _filter_comparison_products(
        products: list[Product], analysis: QueryAnalysis
    ) -> list[Product]:
        selected: list[Product] = []
        seen: set[str] = set()
        intent_service = IntentService()
        for comparison in analysis.productos_comparacion:
            comparison_analysis = intent_service.analyze(comparison)
            comparison_analysis.categoria = analysis.categoria or comparison_analysis.categoria
            exact_matches = rank_catalog_products(products, comparison_analysis)
            candidates = exact_matches
            if not candidates and comparison_analysis.marca and comparison_analysis.categoria:
                fallback_analysis = QueryAnalysis(
                    intencion=IntentType.BUSCAR_POR_MARCA,
                    busqueda=f"{comparison_analysis.marca} {comparison_analysis.categoria}",
                    marca=comparison_analysis.marca,
                    categoria=comparison_analysis.categoria,
                )
                candidates = rank_catalog_products(products, fallback_analysis)
            if not candidates:
                continue
            product = candidates[0]
            key = product.id or product.nombre
            if key not in seen:
                seen.add(key)
                selected.append(product)
        return selected[:5]

    def _prior_catalog_query(self, history: list[Any]) -> str | None:
        for item in reversed(history):
            role = getattr(item, "rol", "")
            if role not in {"usuario", "user"}:
                continue
            content = getattr(item, "contenido", "")
            previous_location = find_named_location(content)
            analysis = self.intent.analyze(without_named_location(content, previous_location))
            if analysis.intencion.is_support:
                continue
            query = normalize(analysis.busqueda)
            if query and not self._is_location_only_followup(analysis):
                return query
        return None

    @staticmethod
    def _referenced_product_from_history(message: str, history: list[Any]) -> str | None:
        normalized_message = normalize(message)
        ordinal_positions = {
            "primer": 0,
            "primero": 0,
            "primera": 0,
            "segundo": 1,
            "segunda": 1,
            "tercer": 2,
            "tercero": 2,
            "tercera": 2,
            "cuarto": 3,
            "cuarta": 3,
            "quinto": 4,
            "quinta": 4,
            "ultimo": -1,
            "ultima": -1,
        }
        position = next(
            (index for word, index in ordinal_positions.items() if contains_any(normalized_message, word)),
            None,
        )
        numbered_reference = re.search(
            r"\b(?:opcion|modelo|producto|numero)\s*(?:nro\s*)?([1-5])\b",
            normalized_message,
        )
        if position is None and numbered_reference:
            position = int(numbered_reference.group(1)) - 1
        if position is None and contains_any(normalized_message, "el de arriba", "la de arriba"):
            position = 0
        if position is None and contains_any(normalized_message, "el de abajo", "la de abajo"):
            position = -1
        refers_to_single_product = contains_any(
            normalized_message,
            "este iphone",
            "ese iphone",
            "este celular",
            "ese celular",
            "este producto",
            "ese producto",
        )
        if position is None and not refers_to_single_product:
            return None

        for item in reversed(history):
            if getattr(item, "rol", "") not in {"asistente", "assistant"}:
                continue
            content = getattr(item, "contenido", "")
            names = ChatService._product_names_from_assistant_content(content)
            if refers_to_single_product and position is None:
                if len(names) == 1:
                    return names[0]
                if names:
                    return None
            elif position is not None and names and -len(names) <= position < len(names):
                return names[position]
        return None

    @staticmethod
    def _latest_unambiguous_product(history: list[Any]) -> str | None:
        for item in reversed(history):
            if getattr(item, "rol", "") not in {"asistente", "assistant"}:
                continue
            names = ChatService._product_names_from_assistant_content(
                getattr(item, "contenido", "")
            )
            if names:
                return names[0] if len(names) == 1 else None
        return None

    @staticmethod
    def _product_names_from_assistant_content(content: str) -> list[str]:
        names = [value.strip() for value in re.findall(r"\*\*([^*\n]{3,180})\*\*", content)]
        return names or ChatService._unformatted_product_names(content)

    @staticmethod
    def _unformatted_product_names(content: str) -> list[str]:
        names: list[str] = []
        for line in content.splitlines():
            match = re.match(r"\s*[•-]\s*(.{3,300})$", line)
            if not match:
                continue
            candidate = match.group(1).strip()
            # El modelo suele añadir precio al final de la viñeta. Ese texto nunca
            # debe volver a VTEX como parte de la búsqueda del producto.
            deterministic_name = re.split(r"\s+—\s+S/\s*", candidate, maxsplit=1)
            if len(deterministic_name) == 2:
                candidate = deterministic_name[0].strip()
                if len(candidate) >= 3:
                    names.append(candidate)
                continue
            candidate = re.split(r"\s+-\s*S/\s*", candidate, maxsplit=1)[0]
            candidate = candidate.split("—", 1)[0].split(":", 1)[0].strip()
            # En respuestas antiguas las variantes se agrupaban entre paréntesis y
            # luego venían características separadas por comas.
            candidate = re.sub(r"\s*\([^)]*\)", "", candidate).split(",", 1)[0].strip()
            if len(candidate) >= 3:
                names.append(candidate)
        return names

    @staticmethod
    def _is_implicit_product_followup(message: str, analysis: QueryAnalysis) -> bool:
        normalized_message = normalize(message)
        query = normalize(analysis.busqueda)
        generic_query_tokens = {
            "comprar",
            "comprarla",
            "comprarlo",
            "conseguir",
            "consigo",
            "detall",
            "detalle",
            "detalles",
            "informacion",
            "puedo",
            "precio",
            "stock",
            "sus",
            "vende",
            "venden",
        }
        has_no_explicit_product = not query or set(query.split()).issubset(generic_query_tokens)
        if not has_no_explicit_product:
            return False
        return contains_any(
            normalized_message,
            "sus caracteristicas",
            "sus especificaciones",
            "mas detalles",
            "mas informacion",
            "cuanto cuesta",
            "que precio",
            "el precio",
            "tiene stock",
            "hay stock",
            "esta disponible",
            "donde la puedo comprar",
            "donde lo puedo comprar",
            "donde puedo comprarla",
            "donde puedo comprarlo",
            "donde la consigo",
            "donde lo consigo",
            "en que tienda la venden",
            "en que tienda lo venden",
        )

    @staticmethod
    def _referenced_product_intent_prefix(message: str, analysis: QueryAnalysis) -> str:
        prefix = {
            IntentType.CONSULTAR_PRECIO: "precio",
            IntentType.CONSULTAR_STOCK: "stock",
            IntentType.CONSULTAR_CARACTERISTICAS: "caracteristicas",
        }.get(analysis.intencion)
        if prefix:
            return prefix
        normalized_message = normalize(message)
        if contains_any(normalized_message, "precio", "cuanto cuesta", "costo"):
            return "precio"
        if contains_any(normalized_message, "stock", "disponible", "disponibilidad"):
            return "stock"
        if contains_any(normalized_message, "caracteristicas", "especificaciones"):
            return "caracteristicas"
        return "buscar"

    @staticmethod
    def _is_location_only_followup(analysis: QueryAnalysis) -> bool:
        query = normalize(analysis.busqueda)
        location_residue = {
            "aqui",
            "de",
            "encuentro",
            "estoy",
            "me",
            "soy",
            "ubicada",
            "ubicado",
            "vivo",
        }
        return (
            not query
            or query in {"si", "tiene", "producto", "productos"}
            or set(query.split()).issubset(location_residue)
        )

    @staticmethod
    def _missing_support_parameter(analysis: QueryAnalysis) -> str | None:
        if analysis.intencion == IntentType.CONSULTAR_PEDIDO:
            if not (analysis.order_id or analysis.document_number):
                return "Indícame el número de pedido o tu DNI/RUC/CEX para consultar su estado."
            return None
        requirements = {
            IntentType.CONSULTAR_DEVOLUCION_DINERO: (
                analysis.credit_note,
                "nota de crédito",
                analysis.document_number,
            ),
            IntentType.CONSULTAR_EXTORNO: (
                analysis.order_id,
                "número de pedido",
                analysis.document_number,
            ),
            IntentType.CONSULTAR_ORDEN_SERVICIO: (
                analysis.service_order,
                "ODS",
                analysis.document_number,
            ),
        }
        if analysis.intencion in requirements:
            reference, reference_name, document = requirements[analysis.intencion]
            if not reference and not document:
                return f"Indícame el {reference_name} y tu DNI/RUC/CEX para validar la consulta."
            if not reference:
                return f"Indícame también el {reference_name} para validar la consulta."
            if not document:
                return "Indícame también tu DNI/RUC/CEX para validar la consulta."
        return None

    @staticmethod
    def _same_identifier(first: Any, second: Any) -> bool:
        left = re.sub(r"[^a-z0-9]", "", normalize(str(first or "")))
        right = re.sub(r"[^a-z0-9]", "", normalize(str(second or "")))
        return bool(left and right and left == right)

    @classmethod
    def _matching_items(
        cls, items: list[dict[str, Any]], expected: str | None, *keys: str
    ) -> list[dict[str, Any]]:
        if not expected:
            return []
        return [item for item in items if any(cls._same_identifier(item.get(key), expected) for key in keys)]

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
            lines = ["💰 Estos son los precios que encontré:"]
            for product in products[:5]:
                price = self._price(product.precio) if product.precio is not None else "precio no disponible"
                lines.append(f"• {product.nombre}: {price}")
            return "\n".join(lines)
        if intent == IntentType.CONSULTAR_STOCK:
            lines = ["📦 Disponibilidad general del catálogo:"]
            for product in products[:5]:
                status = "disponible en tienda online" if product.disponible else "sin stock disponible"
                lines.append(f"• {product.nombre}: {status}")
            return "\n".join(lines)
        product = products[0]
        lines = [f"✨ Características principales de {product.nombre}:"]
        for name, values in list(product.caracteristicas.items())[:6]:
            lines.append(f"• {name}: {', '.join(values[:3])}")
        if len(lines) == 1 and product.descripcion:
            clean_description = re.sub(r"<[^>]+>", " ", product.descripcion)
            lines.append(re.sub(r"\s+", " ", html.unescape(clean_description)).strip()[:500])
        return "\n".join(lines)

    def _local_product_answer(self, products: list[Product]) -> str:
        lines = ["🔎 Encontré estas opciones:"]
        for product in products[:5]:
            price = self._price(product.precio) if product.precio is not None else "precio no disponible"
            stock = "disponible" if product.disponible else "agotado"
            lines.append(f"• {product.nombre} — {price} — {stock}")
        return "\n".join(lines)

    def _comparison_answer(self, products: list[Product], message: str) -> str:
        similarity_request = contains_any(
            message,
            "caracteristicas iguales",
            "caracteristicas similares",
            "mismas caracteristicas",
            "similar a",
            "parecido a",
            "parecida a",
            "equivalente a",
            "alternativa a",
        )
        title = (
            "📊 Esta es la alternativa más cercana que encontré en el catálogo:"
            if similarity_request
            else "📊 Comparación con datos actuales del catálogo:"
        )
        lines = [title]
        for product in products[:2]:
            price = self._price(product.precio) if product.precio is not None else "precio no disponible"
            lines.append(f"• {product.nombre} — {price}")
            facts = self._comparison_facts(product)
            if facts:
                lines.append(f"  {' · '.join(facts)}")

        priced = [product for product in products[:2] if product.precio is not None]
        if len(priced) == 2 and priced[0].precio != priced[1].precio:
            cheaper = min(priced, key=lambda product: product.precio or Decimal("Infinity"))
            difference = abs(priced[0].precio - priced[1].precio)
            lines.append(
                f"💰 {cheaper.nombre.split(',', 1)[0]} cuesta {self._price(difference)} menos."
            )
        if len(products) < 2:
            lines.append("No pude confirmar un segundo producto disponible para completar la comparación.")
        else:
            lines.append("La mejor opción depende de qué priorices: precio, cámara, batería o sistema operativo.")
        return "\n".join(lines)

    @staticmethod
    def _comparison_facts(product: Product) -> list[str]:
        wanted = (
            "Procesador",
            "Memoria RAM",
            "Memoria Interna",
            "Tamaño de pantalla",
            "Cámara trasera",
            "Cámara frontal",
            "Batería",
            "Sistema operativo",
        )
        normalized_specs = {
            normalize(name): (name, values)
            for name, values in product.caracteristicas.items()
            if values
        }
        facts: list[str] = []
        for label in wanted:
            entry = normalized_specs.get(normalize(label))
            if not entry:
                continue
            name, values = entry
            facts.append(f"{name}: {', '.join(values[:2])}")
            if len(facts) == 6:
                break
        return facts

    @staticmethod
    def _single_product_image(
        products: list[Product], analysis: QueryAnalysis, history: list[Any]
    ) -> str | None:
        if not products or not products[0].imagen_url:
            return None

        query = normalize(analysis.busqueda)
        image_query = " ".join(
            token
            for token in query.split()
            if token not in {"dame", "informacion", "mostrar", "muestra", "quiero", "quisiera", "sobre"}
        )
        query_tokens = image_query.split()
        product_name = normalize(products[0].nombre)
        product_identity = normalize(products[0].nombre.split(",", 1)[0])
        query_identity = normalize((analysis.busqueda or "").split(",", 1)[0])
        is_specific = (
            any(character.isdigit() for character in image_query)
            or len(query_tokens) >= 3
            or (len(query_tokens) >= 2 and image_query in product_name)
        )
        if not is_specific:
            return None

        for item in history:
            if getattr(item, "rol", "") not in {"asistente", "assistant"}:
                continue
            previous_answer = normalize(getattr(item, "contenido", ""))
            if (
                product_name in previous_answer
                or (query and query in previous_answer)
                or (product_identity and product_identity in previous_answer)
                or (query_identity and query_identity in previous_answer)
            ):
                return None
        return products[0].imagen_url

    def _location_availability_answer(
        self,
        product: Product,
        availability: LocationAvailability,
        searched_location: str | None = None,
    ) -> str:
        stores = availability.tiendas_recojo
        delivery_options = availability.opciones_envio
        location_text = f" en {searched_location}" if searched_location else " para tu ubicación"

        if not stores and not delivery_options:
            if availability.disponible_online:
                return (
                    f"🛒 {product.nombre} sí figura con stock para compra online. "
                    f"No encontré recojo en tienda ni pude confirmar el envío a domicilio{location_text} "
                    "usando la ubicación de referencia. La cobertura de delivery se calcula con la "
                    "dirección exacta; confírmala en el checkout de Coolbox."
                )
            return (
                f"😕 No encontré {product.nombre} disponible para envío a domicilio ni recojo en tienda"
                f"{location_text}. Puedes probar con otra ubicación o producto."
            )

        lines: list[str] = []
        if delivery_options:
            estimates = {
                self._humanize_shipping_estimate(option.tiempo_entrega)
                for option in delivery_options
                if option.tiempo_entrega
            }
            estimate_text = f" (estimado: {', '.join(sorted(estimates))})" if estimates else ""
            lines.append(
                f"🚚 Sí, {product.nombre} está disponible con envío a domicilio"
                f"{location_text}{estimate_text}."
            )

        if not stores:
            lines.append("No encontré disponibilidad para recojo en una tienda física cercana.")
            lines.append("La disponibilidad puede cambiar antes de completar la compra.")
            return "\n".join(lines)

        pickup_location = f" cerca de {searched_location}" if searched_location else ""
        pickup_prefix = "También está" if delivery_options else f"Encontré {product.nombre}"
        lines.append(f"📍 {pickup_prefix} disponible para recojo{pickup_location} en:")
        for index, store in enumerate(stores[:5]):
            location = ", ".join(value for value in (store.direccion, store.distrito, store.ciudad) if value)
            details: list[str] = []
            if location:
                details.append(f"📍 {location}")
            if store.distancia_km is not None:
                details.append(f"📏 {store.distancia_km:.1f} km")
            if store.tiempo_recojo:
                estimate = self._humanize_shipping_estimate(store.tiempo_recojo)
                details.append(f"⏳ Recojo estimado: {estimate}")
            if index:
                lines.append("")
            lines.append(f"🏬 {store.nombre}")
            if details:
                lines.append("   " + " | ".join(details))
        lines.append("")
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
        return is_purchase_location_request(message) or contains_any(
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
