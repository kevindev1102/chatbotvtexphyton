import pytest
from pydantic import ValidationError

from app.models import ChatRequest, ChatResponse, IntentType


def test_accepts_compatible_java_payload() -> None:
    request = ChatRequest.model_validate(
        {
            "mensaje": "¿Tiene stock?",
            "historial": [{"rol": "usuario", "contenido": "Busco una laptop Lenovo"}],
        }
    )

    assert request.mensaje == "¿Tiene stock?"
    assert request.historial[0].openai_role() == "user"


def test_rejects_more_than_ten_history_messages() -> None:
    with pytest.raises(ValidationError):
        ChatRequest.model_validate(
            {
                "mensaje": "hola",
                "historial": [{"rol": "usuario", "contenido": "x"}] * 11,
            }
        )


def test_accepts_optional_geolocation() -> None:
    request = ChatRequest.model_validate(
        {
            "mensaje": "¿En qué tiendas hay stock?",
            "ubicacion": {"latitud": -12.12, "longitud": -77.03},
        }
    )

    assert request.ubicacion is not None
    assert request.ubicacion.longitud == -77.03


def test_optional_image_keeps_original_response_shape_when_absent() -> None:
    response = ChatResponse(
        respuesta="Encontré un producto",
        intencion=IntentType.BUSCAR_PRODUCTO,
    )

    payload = response.model_dump(mode="json", exclude_none=True)

    assert payload == {
        "respuesta": "Encontré un producto",
        "intencion": "BUSCAR_PRODUCTO",
    }
