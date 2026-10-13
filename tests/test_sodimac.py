import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.errors import ExternalServiceError
from app.main import app
from app.sodimac import OFFER, SodimacAnalysis, SodimacChatService, SodimacRequest
from app.sodimac_catalog import BASE_URL, SodimacCatalog, StoreProduct, read_products
from app.sodimac_vectors import QdrantDocuments


def page(rows):
    return (
        '<script id="__NEXT_DATA__" type="application/json">'
        + json.dumps({"props": {"pageProps": {"results": rows}}})
        + "</script>"
    )


def product_row():
    return {
        "displayName": "Taladro Bosch 500W",
        "url": "/sodimac-pe/articulo/123/taladro",
        "brand": "Bosch",
        "sellerName": "Sodimac",
        "mediaUrls": ["https://media.sodimac.com.pe/sodimacPE/123/public"],
        "prices": [
            {"type": "cmrPrice", "symbol": "S/ ", "price": ["99.90"]},
            {"type": "internetPrice", "symbol": "S/ ", "price": ["129.90"]},
        ],
    }


@pytest.fixture
def service():
    catalog = SimpleNamespace(
        search=AsyncMock(
            return_value=[
                StoreProduct(
                    nombre="Taladro Bosch 500W",
                    url=BASE_URL + "/sodimac-pe/articulo/123/taladro",
                    precios=["Internet: S/ 129.90"],
                    imagen="https://media.sodimac.com.pe/test",
                )
            ]
        ),
        details=AsyncMock(side_effect=ExternalServiceError("unavailable")),
    )
    documents = SimpleNamespace(
        search=AsyncMock(
            return_value=[
                {
                    "id": 0,
                    "pagina": 7,
                    "texto": "Alquiler de herramientas: Black 30%. Excepto subarriendos Tecnofast.",
                }
            ]
        ),
        render=QdrantDocuments.render,
        close=lambda: None,
    )
    return SodimacChatService(catalog, SimpleNamespace(enabled=False), documents)


def test_parser_keeps_conditional_prices_and_deduplicates():
    products = read_products(page([product_row(), product_row()]))
    assert len(products) == 1
    assert products[0].precios == ["Con tarjeta CMR: S/ 99.90", "Internet: S/ 129.90"]
    assert products[0].imagen.startswith("https://media.sodimac.com.pe/")


def test_parser_does_not_invent_missing_price_or_accept_external_product():
    row = product_row()
    row["prices"] = []
    assert read_products(page([row]))[0].precios == []
    row["url"] = "https://example.com/product"
    assert read_products(page([row])) == []
    with pytest.raises(ExternalServiceError):
        read_products("<html>Access denied</html>")


@respx.mock
async def test_redirect_cannot_leave_sodimac():
    respx.get(BASE_URL + "/sodimac-pe/buscar").mock(
        return_value=httpx.Response(302, headers={"Location": "https://example.com/stolen"})
    )
    external = respx.get("https://example.com/stolen").mock(return_value=httpx.Response(200))
    client = SodimacCatalog()
    try:
        with pytest.raises(ExternalServiceError):
            await client.search("taladro")
        assert not external.called
    finally:
        await client.close()


@respx.mock
async def test_actual_listing_shape_and_same_host_redirect():
    respx.get(BASE_URL + "/sodimac-pe/buscar").mock(
        return_value=httpx.Response(302, headers={"Location": "/sodimac-pe/lista/taladros"})
    )
    respx.get(BASE_URL + "/sodimac-pe/lista/taladros").mock(
        return_value=httpx.Response(200, text=page([product_row()]))
    )
    client = SodimacCatalog()
    try:
        assert (await client.search("taladro"))[0].nombre == "Taladro Bosch 500W"
    finally:
        await client.close()


@respx.mock
async def test_details_use_only_product_page():
    p = StoreProduct(nombre="Taladro", url=BASE_URL + "/sodimac-pe/articulo/123/taladro")
    data = {
        "props": {
            "pageProps": {
                "productData": {"attributes": {"specifications": [{"name": "Modelo", "value": "GSB 500"}]}}
            }
        }
    }
    respx.get(p.url).mock(
        return_value=httpx.Response(
            200, text=('<script id="__NEXT_DATA__">' + json.dumps(data) + "</script>")
        )
    )
    client = SodimacCatalog()
    try:
        assert (await client.details(p)).detalles == ["Modelo: GSB 500"]
    finally:
        await client.close()


async def test_product_offers_transfer_but_does_not_activate_it(service):
    response = await service.process(SodimacRequest(mensaje="Busco un taladro"))
    assert OFFER in response.respuesta
    assert not response.es_transferencia
    assert response.imagen
    assert "129.90" in response.respuesta
    assert "Coolbox" not in response.respuesta


async def test_transfer_acceptance_needs_offer_in_latest_assistant_message(service):
    yes = await service.process(
        SodimacRequest(mensaje="sí", historial=[{"rol": "asistente", "contenido": OFFER}])
    )
    assert yes.es_transferencia
    no_offer = await service.process(SodimacRequest(mensaje="sí"))
    assert not no_offer.es_transferencia
    stale = await service.process(
        SodimacRequest(
            mensaje="sí",
            historial=[
                {"rol": "asistente", "contenido": OFFER},
                {"rol": "asistente", "contenido": "¿Otra consulta?"},
            ],
        )
    )
    assert not stale.es_transferencia
    assert not service.catalog.search.called


async def test_second_product_followup_refreshes_product_data(service):
    await service.process(
        SodimacRequest(
            mensaje="precio de la segunda",
            historial=[{"rol": "asistente", "contenido": "• Taladro Bosch 500W\n• Taladro Dewalt 20V"}],
        )
    )
    assert service.catalog.search.call_args.args[0] == "taladro dewalt 20v"


async def test_new_product_does_not_reuse_document_history(service):
    await service.process(
        SodimacRequest(mensaje="taladro", historial=[{"rol": "usuario", "contenido": "beneficios CES"}])
    )
    assert service.catalog.search.call_args.args[0] == "taladro"


async def test_unavailable_catalog_never_fabricates_data(service):
    service.catalog.search.side_effect = ExternalServiceError("offline")
    result = await service.process(SodimacRequest(mensaje="Busco taladro"))
    assert "No pude verificar" in result.respuesta
    assert result.imagen is None
    assert "S/" not in result.respuesta


async def test_stock_does_not_claim_local_inventory(service):
    result = await service.process(SodimacRequest(mensaje="stock taladro"))
    assert "No puedo confirmar stock" in result.respuesta


async def test_document_answers_without_page_source_or_catalog(service):
    result = await service.process(SodimacRequest(mensaje="descuentos alquiler herramientas CES"))
    assert result.intencion == "CONSULTAR_DOCUMENTO"
    assert "página 7" not in result.respuesta
    assert "Fuente:" not in result.respuesta
    assert "Tecnofast" in result.respuesta
    assert "30%" in result.respuesta
    assert not service.catalog.search.called


def test_document_render_does_not_publish_internal_pages():
    result = QdrantDocuments.render([{"texto": "Categoría Black: S/11.000", "pagina": 5}])
    assert "S/11.000" in result
    assert "página" not in result
    assert "No encontré" in QdrantDocuments.render([])


async def test_ai_cannot_inject_unverified_document_text(service):
    service.ai = SimpleNamespace(
        enabled=True,
        model="configured-model",
        _history=lambda h: [],
        client=SimpleNamespace(
            responses=SimpleNamespace(
                parse=AsyncMock(
                    return_value=SimpleNamespace(
                        output_parsed=SodimacAnalysis(
                            destino="documento",
                            consulta="requisitos inscripción CES",
                            intencion="CONSULTAR_DOCUMENTO",
                            fragmentos=[999999],
                        )
                    )
                )
            )
        ),
    )
    result = await service.process(SodimacRequest(mensaje="requisitos inscripción CES"))
    assert "No encontré" in result.respuesta
    assert not service.catalog.search.called


def test_endpoint_contract_and_original_route_remain(service):
    # No external calls: stub both services after lifespan creates them.
    with TestClient(app) as client:
        app.state.sodimac_service = service
        old = SimpleNamespace(
            process=AsyncMock(
                return_value={
                    "respuesta": "Coolbox",
                    "intencion": "SALUDO",
                    "esTransferencia": False,
                    "esDespedida": False,
                }
            )
        )
        app.state.chat_service = old
        r = client.post("/api/chatsodimac", json={"mensaje": "hola"})
        assert r.status_code == 200
        assert r.json()["esTransferencia"] is False
        assert r.json()["esDespedida"] is False
        assert "Sodimac" in r.json()["respuesta"]
        assert client.post("/api/chat", json={"mensaje": "hola"}).json()["respuesta"] == "Coolbox"
        assert client.post("/api/chatsodimac", json={"mensaje": " "}).status_code == 422
        assert (
            client.post(
                "/api/chatsodimac",
                json={"mensaje": "hola", "historial": [{"rol": "asistente", "contenido": "x" * 5000}]},
            ).status_code
            == 200
        )


def test_rate_limit_covers_sodimac():
    from fastapi import FastAPI

    from app.rate_limit import ChatRateLimitMiddleware

    limited = FastAPI()
    limited.add_middleware(ChatRateLimitMiddleware, max_requests=1, window_seconds=60)

    @limited.post("/api/chatsodimac")
    def handler():
        return {"ok": True}

    with TestClient(limited) as client:
        assert client.post("/api/chatsodimac").status_code == 200
        assert client.post("/api/chatsodimac").status_code == 429


def test_ai_history_does_not_resend_pdf_text(service):
    service.ai._history = lambda items: [{"role": h.openai_role(), "content": h.contenido} for h in items]
    request = SodimacRequest(
        mensaje="y para oro",
        historial=[{"rol": "asistente", "contenido": "Según el documento proporcionado " + "x" * 10000}],
    )
    history = service._ai_history(request)
    assert len(history[0]["content"]) < 100
    assert "x" * 100 not in history[0]["content"]


async def test_document_followup_without_page_reference(service):
    request = SodimacRequest(
        mensaje="y para oro",
        historial=[
            {"rol": "usuario", "contenido": "descuentos alquiler CES"},
            {"rol": "asistente", "contenido": "Según el documento proporcionado: descuentos de alquiler"},
        ],
    )
    result = await service.process(request)
    assert result.intencion == "CONSULTAR_DOCUMENTO"
    assert not service.catalog.search.called


async def test_qdrant_failure_is_reported_without_old_document_fallback(service):
    service.documents.search.side_effect = ExternalServiceError("Qdrant offline")
    result = await service.process(SodimacRequest(mensaje="inscripción CES"))
    assert "No pude verificar esa información del CES" in result.respuesta
    assert not service.catalog.search.called


async def test_affiliation_synonym_routes_to_vector_document_search(service):
    result = await service.process(SodimacRequest(mensaje="¿Cómo puedo afiliarme?"))
    assert result.intencion == "CONSULTAR_DOCUMENTO"
    assert service.documents.search.called
    assert not service.catalog.search.called


def test_document_summary_requires_matching_evidence_and_preserves_verified_numbers(service):
    from app.sodimac import DocumentPoint

    passages = [{"id": 0, "texto": "La inscripción requiere ser mayor de 18 años y residir en Perú."}]
    analysis = SodimacAnalysis(
        destino="documento", consulta="inscripción", intencion="CONSULTAR_DOCUMENTO",
        puntos=[DocumentPoint(texto="Debes ser mayor de 18 años y residir en Perú.", fragmento=0,
                              evidencia=passages[0]["texto"])],
    )
    result = service._document_reply(passages, analysis, "inscripción", True)
    assert "Debes ser mayor de 18" in result
    assert "Según" not in result
    analysis.puntos[0].texto = "La inscripción cuesta S/ 99."
    assert "S/ 99" not in service._document_reply(passages, analysis, "inscripción", True)
    analysis.puntos[0].evidencia = "Una fuente inventada"
    assert "S/ 99" not in service._document_reply(passages, analysis, "inscripción", True)


def test_document_fallback_is_bounded_and_normalizes_pdf_spacing():
    passages = [{"texto": "Inscripción:  mayores    de 18 años residentes\nen Perú.\n\n" + "Aviso legal largo. " * 300}]
    result = QdrantDocuments.render(passages, "como puedo aplicar al CES")
    assert len(result) < 1200
    assert "mayores de 18 años residentes en Perú" in result
    assert "Aviso legal" not in result
    assert "documento" not in result


def test_enrollment_query_expands_synonyms_without_reindexing():
    from app.sodimac_documents import document_query

    for verb in ["aplicar", "postular", "unirme", "inscribirme"]:
        assert "Inscripción CES requisitos" in document_query(f"¿Cómo puedo {verb} al CES?")
    assert document_query("descuentos alquiler") == "descuentos alquiler"


def test_enrollment_fallback_rewrites_legal_steps_without_section_references():
    passages = [{"texto": """El registro de cada usuario se verificará completando y suscribiendo el formulario que se encuentra publicado en la WEB y su posterior envío o en su caso a través del llenado del formulario y su presentación en Tienda (según la modalidad de inscripción empleada por el usuario).

Los interesados que cumplan con los requisitos indicados en la sección 1) de Inscripción, podrán ingresar a la WEB y dirigirse a la sección de registro donde deberán completar todos los datos requeridos, aceptar los Términos y Condiciones del CES y marcar la opción de envío del formulario a fin de realizar su registro en el Programa.

En cualquiera de las dos modalidades de inscripción indicadas en esta sección, transcurrido 6 días hábiles después de finalizado el registro en el CES, el solicitante tendrá la condición de socio activo del Programa (en adelante, el “Socio”)."""}]
    result = QdrantDocuments.render(passages, "como puedo aplicar para el programa CES?")
    assert "sección" not in result
    assert "completa tus datos" in result
    assert "6 días hábiles" in result
    assert "en tienda" in result
    assert "18 años" not in result  # Requirements cannot be added without evidence.
    assert len(result) < 650


def test_customer_text_preserves_requirements_and_exclusions():
    from app.sodimac_documents import customer_text

    result = customer_text("Cumple los requisitos indicados en la sección 1) de Inscripción. Excepto subarriendos Tecnofast.")
    assert "requisitos de inscripción" in result
    assert "sección" not in result
    assert "Excepto subarriendos Tecnofast" in result
