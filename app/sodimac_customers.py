"""Read-only CES segment lookup; credentials and customer data never enter AI prompts."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import psycopg

from app.config import _environment
from app.errors import ExternalServiceError


@dataclass(frozen=True)
class CustomerSettings:
    host: str = ""
    port: int = 5432
    database: str = ""
    user: str = ""
    password: str = field(default="", repr=False)
    sslmode: str = "prefer"

    @classmethod
    def load(cls):
        env = _environment()
        return cls(
            host=env.get("SODIMAC_DB_HOST", ""),
            port=int(env.get("SODIMAC_DB_PORT", "5432")),
            database=env.get("SODIMAC_DB_NAME", ""),
            user=env.get("SODIMAC_DB_USER", ""),
            password=env.get("SODIMAC_DB_PASSWORD", ""),
            sslmode=env.get("SODIMAC_DB_SSLMODE", "prefer"),
        )


class CesCustomers:
    def __init__(self, settings=None):
        self.settings = settings if settings is not None else CustomerSettings.load()
        self._slots = asyncio.Semaphore(4)

    async def segment(self, document: str) -> str | None:
        config = self.settings
        if not all((config.host, config.database, config.user, config.password)):
            raise ExternalServiceError("CES database is not configured")
        try:
            async with (
                asyncio.timeout(10),
                self._slots,
                await psycopg.AsyncConnection.connect(
                    host=config.host,
                    port=config.port,
                    dbname=config.database,
                    user=config.user,
                    password=config.password,
                    sslmode=config.sslmode,
                    connect_timeout=5,
                    autocommit=True,
                    options="-c default_transaction_read_only=on -c statement_timeout=5000",
                ) as connection,
            ):
                cursor = await connection.execute(
                    "SELECT segmento FROM public.tbl_clientes_ces WHERE numero_documento = %s",
                    (document,),
                )
                row = await cursor.fetchone()
                return row[0] if row else None
        except (psycopg.Error, TimeoutError) as error:
            raise ExternalServiceError("CES segment lookup unavailable") from error
