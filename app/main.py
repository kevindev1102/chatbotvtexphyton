from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.ai import OpenAIService
from app.chat import ChatService
from app.clients import CoolboxClient, VtexClient
from app.config import Settings
from app.errors import ExternalServiceError
from app.intent import IntentService
from app.models import ChatRequest, ChatResponse
from app.rate_limit import ChatRateLimitMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
# Evita que httpx escriba URLs con números de pedido o documentos en los logs.
logging.getLogger("httpx").setLevel(logging.WARNING)


settings = Settings.load()


@asynccontextmanager
async def lifespan(app: FastAPI):
    ai = OpenAIService(settings)
    vtex = VtexClient(settings)
    coolbox = CoolboxClient(settings)
    app.state.settings = settings
    app.state.chat_service = ChatService(IntentService(), vtex, coolbox, ai)
    yield
    await ai.close()
    await vtex.close()
    await coolbox.close()


app = FastAPI(
    title="Chatbot VTEX + OpenAI",
    version="1.0.0",
    description="API compatible con el chatbot Spring Boot, migrada a Python/FastAPI.",
    lifespan=lifespan,
)
app.add_middleware(
    ChatRateLimitMiddleware,
    max_requests=settings.rate_limit_requests,
    window_seconds=settings.rate_limit_window_seconds,
)


@app.get("/health")
async def health(request: Request) -> dict[str, object]:
    settings: Settings = request.app.state.settings
    return {
        "status": "ok",
        "integrations": {
            "openai_configured": bool(settings.openai_api_key),
            "vtex_configured": bool(settings.vtex_base_url),
            "vtex_orders_configured": bool(settings.vtex_app_key and settings.vtex_app_token),
            "coolbox_configured": bool(
                settings.coolbox_base_url and settings.coolbox_username and settings.coolbox_password
            ),
        },
    }


@app.post("/api/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    service: ChatService = request.app.state.chat_service
    return await service.process(payload)


@app.exception_handler(ExternalServiceError)
async def external_service_error(_: Request, error: ExternalServiceError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"error": "external_service_error", "message": str(error)})


@app.exception_handler(Exception)
async def unexpected_error(_: Request, error: Exception) -> JSONResponse:
    logging.getLogger(__name__).exception("Error no controlado", exc_info=error)
    return JSONResponse(
        status_code=500,
        content={"error": "internal_error", "message": "Ocurrió un error interno al procesar la consulta."},
    )
