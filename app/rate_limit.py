from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


class ChatRateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, max_requests: int, window_seconds: int):
        super().__init__(app)
        self.max_requests = max(1, max_requests)
        self.window_seconds = max(1, window_seconds)
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path not in {"/api/chat", "/api/chatsodimac"}:
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        cutoff = now - self.window_seconds
        async with self._lock:
            timestamps = self._requests[client_ip]
            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()
            if len(timestamps) >= self.max_requests:
                retry_after = max(1, int(timestamps[0] + self.window_seconds - now) + 1)
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": "rate_limit_exceeded",
                        "message": "Has enviado demasiadas consultas. Intenta nuevamente en un momento.",
                    },
                    headers={"Retry-After": str(retry_after)},
                )
            timestamps.append(now)
            if len(self._requests) > 10_000:
                self._requests = defaultdict(
                    deque,
                    {key: values for key, values in self._requests.items() if values},
                )
        return await call_next(request)
