import pytest
from pydantic import ValidationError

from app.models import ChatRequest


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
