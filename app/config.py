from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEGACY_PROPERTIES = PROJECT_ROOT.parent / "chatbotvtex" / "src/main/resources/application.properties"


def _read_key_values(path: Path, separator: str = "=") -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or separator not in line:
            continue
        key, value = line.split(separator, 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def _environment() -> dict[str, str]:
    values = _read_key_values(PROJECT_ROOT / ".env")
    values.update(os.environ)
    return values


def _normalize_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    if normalized and not normalized.startswith(("http://", "https://")):
        normalized = f"https://{normalized}"
    return normalized


def _normalize_openai_url(value: str) -> str:
    normalized = _normalize_url(value)
    return normalized.removesuffix("/chat/completions")


def _resolve_spring_placeholder(raw_value: str, environment: dict[str, str]) -> str:
    match = re.fullmatch(r"\$\{([^:}]+)(?::(.*))?\}", raw_value.strip())
    if not match:
        return raw_value
    variable_name, default = match.groups()
    return environment.get(variable_name) or default or ""


@dataclass(frozen=True, slots=True)
class Settings:
    port: int
    openai_api_key: str
    openai_model: str
    openai_base_url: str
    vtex_base_url: str
    vtex_app_key: str
    vtex_app_token: str
    coolbox_base_url: str
    coolbox_username: str
    coolbox_password: str
    connect_timeout_seconds: float
    response_timeout_seconds: float
    rate_limit_requests: int = 30
    rate_limit_window_seconds: int = 60

    @classmethod
    def load(cls) -> Settings:
        env = _environment()
        legacy_path = Path(env.get("LEGACY_PROPERTIES_PATH", str(DEFAULT_LEGACY_PROPERTIES)))
        legacy = _read_key_values(legacy_path)

        def value(env_key: str, legacy_key: str, default: str = "") -> str:
            raw_value = env.get(env_key) or legacy.get(legacy_key) or default
            return _resolve_spring_placeholder(raw_value, env)

        connect_ms = float(value("HTTP_CONNECT_TIMEOUT_MS", "http.client.connect-timeout-ms", "5000"))
        response_ms = float(value("HTTP_RESPONSE_TIMEOUT_MS", "http.client.response-timeout-ms", "15000"))
        connect_seconds = float(env.get("HTTP_CONNECT_TIMEOUT_SECONDS", connect_ms / 1000))
        response_seconds = float(env.get("HTTP_RESPONSE_TIMEOUT_SECONDS", response_ms / 1000))

        return cls(
            port=int(value("PORT", "server.port", "8080")),
            openai_api_key=value("OPENAI_API_KEY", "openai.api-key"),
            openai_model=value("OPENAI_MODEL", "openai.model", "gpt-4.1-mini"),
            openai_base_url=_normalize_openai_url(
                value("OPENAI_BASE_URL", "openai.base-url", "https://api.openai.com/v1")
            ),
            vtex_base_url=_normalize_url(
                value(
                    "VTEX_BASE_URL",
                    "vtex.base-url",
                    "https://coolboxpe.vtexcommercestable.com.br",
                )
            ),
            vtex_app_key=value("VTEX_APP_KEY", "vtex.app-key"),
            vtex_app_token=value("VTEX_APP_TOKEN", "vtex.app-token"),
            coolbox_base_url=_normalize_url(value("COOLBOX_WEB_API_BASE_URL", "coolbox.web-api.base-url")),
            coolbox_username=value("COOLBOX_WEB_API_USERNAME", "coolbox.web-api.username"),
            coolbox_password=value("COOLBOX_WEB_API_PASSWORD", "coolbox.web-api.password"),
            connect_timeout_seconds=connect_seconds,
            response_timeout_seconds=response_seconds,
            rate_limit_requests=int(env.get("RATE_LIMIT_REQUESTS", "30")),
            rate_limit_window_seconds=int(env.get("RATE_LIMIT_WINDOW_SECONDS", "60")),
        )
