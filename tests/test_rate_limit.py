from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.rate_limit import ChatRateLimitMiddleware


def test_chat_rate_limit_does_not_affect_health_endpoint() -> None:
    app = FastAPI()
    app.add_middleware(ChatRateLimitMiddleware, max_requests=2, window_seconds=60)

    @app.post("/api/chat")
    async def chat() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(app) as client:
        assert client.post("/api/chat").status_code == 200
        assert client.post("/api/chat").status_code == 200
        limited = client.post("/api/chat")
        assert limited.status_code == 429
        assert limited.headers["Retry-After"]
        assert client.get("/health").status_code == 200
