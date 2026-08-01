from app.ai import OpenAIService
from app.models import HistoryMessage


def test_history_omits_support_messages_and_personal_identifiers() -> None:
    history = [
        HistoryMessage(rol="usuario", contenido="Mi DNI es 62022494, revisa mi pedido"),
        HistoryMessage(rol="asistente", contenido="Pedido: 1234567890123-01 · Estado: enviado"),
        HistoryMessage(rol="usuario", contenido="También busco un iPhone 15"),
    ]

    assert OpenAIService._history(history) == [{"role": "user", "content": "También busco un iPhone 15"}]


def test_history_redacts_contact_details() -> None:
    history = [
        HistoryMessage(rol="usuario", contenido="Escríbeme a cliente@example.com por información del iPhone")
    ]

    rendered = OpenAIService._history(history)

    assert rendered[0]["content"] == "Escríbeme a [correo omitido] por información del iPhone"


def test_history_omits_phone_numbers() -> None:
    history = [HistoryMessage(rol="usuario", contenido="Mi celular es 987654321")]

    assert OpenAIService._history(history) == []
