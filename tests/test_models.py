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


def test_keeps_last_ten_when_widget_sends_full_history() -> None:
    request = ChatRequest.model_validate(
        {
            "mensaje": "hola",
            "historial": [
                {"rol": "usuario", "contenido": f"mensaje {index}"}
                for index in range(15)
            ],
        }
    )

    assert len(request.historial) == 10
    assert request.historial[0].contenido == "mensaje 5"
    assert request.historial[-1].contenido == "mensaje 14"


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
