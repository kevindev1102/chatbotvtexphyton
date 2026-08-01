from app.config import _normalize_openai_url, _normalize_url, _resolve_spring_placeholder


def test_resolves_spring_placeholder_from_environment() -> None:
    assert (
        _resolve_spring_placeholder(
            "${SERVICE_URL:https://default.example}", {"SERVICE_URL": "https://env.example"}
        )
        == "https://env.example"
    )


def test_resolves_spring_placeholder_default_with_url_colon() -> None:
    assert (
        _resolve_spring_placeholder("${SERVICE_URL:https://default.example}", {}) == "https://default.example"
    )


def test_normalizes_legacy_urls() -> None:
    assert _normalize_url("webapi.example.com") == "https://webapi.example.com"
    assert _normalize_openai_url("https://api.openai.com/v1/chat/completions") == "https://api.openai.com/v1"
