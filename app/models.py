from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class IntentType(StrEnum):
    BUSCAR_PRODUCTO = "BUSCAR_PRODUCTO"
    LISTAR_POR_CATEGORIA = "LISTAR_POR_CATEGORIA"
    BUSCAR_POR_MARCA = "BUSCAR_POR_MARCA"
    COMPARAR_PRODUCTOS = "COMPARAR_PRODUCTOS"
    RECOMENDAR_PRODUCTOS = "RECOMENDAR_PRODUCTOS"
    CONSULTAR_PRECIO = "CONSULTAR_PRECIO"
    CONSULTAR_STOCK = "CONSULTAR_STOCK"
    CONSULTAR_CARACTERISTICAS = "CONSULTAR_CARACTERISTICAS"
    CONSULTAR_PEDIDO = "CONSULTAR_PEDIDO"
    CONSULTAR_DEVOLUCION_DINERO = "CONSULTAR_DEVOLUCION_DINERO"
    CONSULTAR_EXTORNO = "CONSULTAR_EXTORNO"
    CONSULTAR_ORDEN_SERVICIO = "CONSULTAR_ORDEN_SERVICIO"
    RANKING_PRODUCTOS = "RANKING_PRODUCTOS"
    PRODUCTOS_DESTACADOS = "PRODUCTOS_DESTACADOS"
    CONSULTA_GENERAL = "CONSULTA_GENERAL"
    ACLARACION_REQUERIDA = "ACLARACION_REQUERIDA"

    @property
    def is_support(self) -> bool:
        return self in {
            self.CONSULTAR_PEDIDO,
            self.CONSULTAR_DEVOLUCION_DINERO,
            self.CONSULTAR_EXTORNO,
            self.CONSULTAR_ORDEN_SERVICIO,
        }

    @property
    def requires_catalog(self) -> bool:
        return self not in {
            self.CONSULTA_GENERAL,
            self.ACLARACION_REQUERIDA,
            self.CONSULTAR_PEDIDO,
            self.CONSULTAR_DEVOLUCION_DINERO,
            self.CONSULTAR_EXTORNO,
            self.CONSULTAR_ORDEN_SERVICIO,
        }


class HistoryMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rol: Literal["usuario", "asistente", "user", "assistant"]
    contenido: str = Field(min_length=1, max_length=2000)

    @field_validator("contenido")
    @classmethod
    def trim_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("El contenido del historial no puede estar vacío")
        return value.strip()

    def openai_role(self) -> Literal["user", "assistant"]:
        return "user" if self.rol in {"usuario", "user"} else "assistant"


class GeoLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latitud: float = Field(ge=-90, le=90)
    longitud: float = Field(ge=-180, le=180)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mensaje: str = Field(min_length=1, max_length=1000)
    historial: list[HistoryMessage] = Field(default_factory=list, max_length=10)
    ubicacion: GeoLocation | None = None

    @field_validator("mensaje")
    @classmethod
    def trim_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("El mensaje no puede estar vacío")
        return value.strip()


class ChatResponse(BaseModel):
    respuesta: str
    intencion: IntentType


class QueryAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intencion: IntentType
    busqueda: str | None = None
    marca: str | None = None
    categoria: str | None = None
    precio_minimo: Decimal | None = Field(default=None, alias="precioMinimo")
    precio_maximo: Decimal | None = Field(default=None, alias="precioMaximo")
    ordenar_por_menor_precio: bool = Field(default=False, alias="ordenarPorMenorPrecio")
    requiere_aclaracion: bool = Field(default=False, alias="requiereAclaracion")
    productos_comparacion: list[str] = Field(default_factory=list, alias="productosComparacion")
    modelos_excluidos: list[str] = Field(default_factory=list, alias="modelosExcluidos")
    cantidad: int | None = Field(default=None, ge=1, le=5)
    almacenamiento_minimo_gb: int | None = Field(default=None, alias="almacenamientoMinimoGb")
    order_id: str | None = Field(default=None, alias="orderId")
    document_number: str | None = Field(default=None, alias="documentNumber")
    credit_note: str | None = Field(default=None, alias="creditNote")
    service_order: str | None = Field(default=None, alias="serviceOrder")


class SkuOffer(BaseModel):
    sku_id: str
    seller_id: str
    available_quantity: int


class PickupStore(BaseModel):
    id: str
    nombre: str
    direccion: str | None = None
    distrito: str | None = None
    ciudad: str | None = None
    distancia_km: float | None = None
    tiempo_recojo: str | None = None


class DeliveryOption(BaseModel):
    nombre: str
    tiempo_entrega: str | None = None


class LocationAvailability(BaseModel):
    disponible_online: bool = False
    opciones_envio: list[DeliveryOption] = Field(default_factory=list)
    tiendas_recojo: list[PickupStore] = Field(default_factory=list)


class Product(BaseModel):
    id: str | None = None
    nombre: str
    marca: str | None = None
    categoria: str | None = None
    descripcion: str | None = None
    precio: Decimal | None = None
    moneda: str | None = None
    stock_disponible: int | None = None
    disponible: bool = False
    categorias: list[str] = Field(default_factory=list)
    caracteristicas: dict[str, list[str]] = Field(default_factory=dict)
    sku_offers: list[SkuOffer] = Field(default_factory=list, exclude=True)

    def prompt_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True, mode="json")


class CoolboxResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    result: bool = False
    mensaje: str | None = None
    pedidos: list[dict[str, Any]] = Field(default_factory=list)
    notas_credito: list[dict[str, Any]] = Field(default_factory=list, alias="notasCredito")
    ordenes_servicio: list[dict[str, Any]] = Field(default_factory=list, alias="ordenesServicio")
