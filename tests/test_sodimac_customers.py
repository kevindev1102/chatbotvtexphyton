from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import psycopg
import pytest

from app.errors import ExternalServiceError
from app.sodimac import SEGMENT_PROMPT, SodimacChatService, SodimacRequest
from app.sodimac_customers import CesCustomers, CustomerSettings


def service(segment="ORO"):
    customers = SimpleNamespace(segment=AsyncMock(return_value=segment))
    chat = SodimacChatService(
        SimpleNamespace(search=AsyncMock(return_value=[])),
        SimpleNamespace(enabled=False),
        SimpleNamespace(search=AsyncMock(return_value=[])),
        customers,
    )
    return chat, customers


async def test_segment_with_document_preserves_leading_zeroes():
    chat, customers = service()
    response = await chat.process(SodimacRequest(mensaje="¿Cuál es mi segmento? Mi DNI es 00123456"))
    assert response.intencion == "CONSULTAR_SEGMENTO"
    assert "*ORO*" in response.respuesta
    customers.segment.assert_awaited_once_with("00123456")
    chat.catalog.search.assert_not_called()
    chat.documents.search.assert_not_called()


async def test_segment_followup_and_missing_customer():
    chat, customers = service(None)
    first = await chat.process(SodimacRequest(mensaje="Quiero saber mi segmento"))
    assert first.respuesta == SEGMENT_PROMPT
    customers.segment.assert_not_called()
    response = await chat.process(
        SodimacRequest(
            mensaje="00123456",
            historial=[{"rol": "asistente", "contenido": first.respuesta}],
        )
    )
    assert response.respuesta == "No cuentas con segmento."


async def test_segment_connection_error_is_not_missing_customer():
    chat, customers = service()
    customers.segment.side_effect = ExternalServiceError("offline")
    response = await chat.process(SodimacRequest(mensaje="Mi segmento DNI 12345678"))
    assert "No pude consultar" in response.respuesta
    assert "No cuentas" not in response.respuesta


async def test_segment_does_not_capture_new_product_question():
    chat, customers = service()
    response = await chat.process(
        SodimacRequest(
            mensaje="Busco taladro 20V",
            historial=[{"rol": "asistente", "contenido": SEGMENT_PROMPT}],
        )
    )
    customers.segment.assert_not_called()
    assert response.intencion == "BUSCAR_PRODUCTO"


async def test_segment_supports_ce():
    chat, customers = service()
    await chat.process(SodimacRequest(mensaje="Mi segmento, CE 000123456"))
    customers.segment.assert_awaited_once_with("000123456")


async def test_segment_does_not_call_ai():
    chat, _ = service()
    chat.ai = SimpleNamespace(enabled=True)
    await chat.process(SodimacRequest(mensaje="Mi segmento DNI 12345678"))


async def test_lookup_uses_read_only_parameterized_query_and_closes_connection():
    connection = AsyncMock()
    connection.__aenter__.return_value = connection
    connection.execute.return_value.fetchone.return_value = ("BLACK",)
    config = CustomerSettings(host="localhost", database="test", user="test", password="secret")
    with patch("app.sodimac_customers.psycopg.AsyncConnection.connect", new_callable=AsyncMock) as connect:
        connect.return_value = connection
        assert await CesCustomers(config).segment("00123456") == "BLACK"
    query, parameters = connection.execute.call_args.args
    assert "%s" in query and "00123456" not in query
    assert parameters == ("00123456",)
    assert "default_transaction_read_only=on" in connect.call_args.kwargs["options"]
    connection.__aexit__.assert_awaited_once()


async def test_lookup_missing_row_and_db_error():
    connection = AsyncMock()
    connection.__aenter__.return_value = connection
    connection.execute.return_value.fetchone.return_value = None
    config = CustomerSettings(host="localhost", database="test", user="test", password="secret")
    with patch("app.sodimac_customers.psycopg.AsyncConnection.connect", new_callable=AsyncMock) as connect:
        connect.return_value = connection
        assert await CesCustomers(config).segment("00123456") is None
        connect.side_effect = psycopg.OperationalError("offline")
        with pytest.raises(ExternalServiceError):
            await CesCustomers(config).segment("00123456")


async def test_unconfigured_db_is_unavailable():
    with pytest.raises(ExternalServiceError):
        await CesCustomers(CustomerSettings()).segment("12345678")
