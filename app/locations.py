from __future__ import annotations

import re
from dataclasses import dataclass

from app.intent import normalize


@dataclass(frozen=True, slots=True)
class NamedLocation:
    name: str
    latitude: float
    longitude: float
    matched_name: str


# Coordenadas céntricas usadas para ordenar los puntos de recojo cercanos en VTEX.
PERU_LOCATIONS: dict[str, tuple[str, float, float]] = {
    "lima": ("Lima", -12.0464, -77.0428),
    "arequipa": ("Arequipa", -16.4090, -71.5375),
    "trujillo": ("Trujillo", -8.1116, -79.0287),
    "chiclayo": ("Chiclayo", -6.7714, -79.8409),
    "piura": ("Piura", -5.1945, -80.6328),
    "cusco": ("Cusco", -13.5319, -71.9675),
    "cuzco": ("Cusco", -13.5319, -71.9675),
    "iquitos": ("Iquitos", -3.7437, -73.2516),
    "huancayo": ("Huancayo", -12.0651, -75.2049),
    "chimbote": ("Chimbote", -9.0853, -78.5783),
    "tacna": ("Tacna", -18.0066, -70.2463),
    "cajamarca": ("Cajamarca", -7.1617, -78.5128),
    "ica": ("Ica", -14.0678, -75.7286),
    "pucallpa": ("Pucallpa", -8.3791, -74.5539),
    "juliaca": ("Juliaca", -15.4997, -70.1333),
    "tumbes": ("Tumbes", -3.5669, -80.4515),
    "huanuco": ("Huánuco", -9.9306, -76.2422),
    "ayacucho": ("Ayacucho", -13.1631, -74.2236),
}


def find_named_location(message: str) -> NamedLocation | None:
    normalized = normalize(message)
    for alias in sorted(PERU_LOCATIONS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(alias)}\b", normalized):
            name, latitude, longitude = PERU_LOCATIONS[alias]
            return NamedLocation(name, latitude, longitude, alias)
    return None


def without_named_location(message: str, location: NamedLocation | None) -> str:
    normalized = normalize(message)
    if location is None:
        return normalized
    cleaned = re.sub(
        rf"\b(?:en|cerca de|por)\s+{re.escape(location.matched_name)}\b",
        " ",
        normalized,
    )
    cleaned = re.sub(rf"\b{re.escape(location.matched_name)}\b", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()
