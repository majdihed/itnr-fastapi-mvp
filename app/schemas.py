from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Period(BaseModel):
    # Début de la période (YYYY-MM-DD) et durée en jours
    start: str
    durationDays: int


class Passengers(BaseModel):
    adults: int = 1
    children: int = 0
    infants: int = 0


class SearchBody(BaseModel):
    # Ville d'origine/destination en clair (ex: "Paris", "Bangkok")
    originCity: str
    destinationCity: str | None = None

    # Soit dates exactes...
    departureDate: str | None = None  # YYYY-MM-DD
    returnDate: str | None = None     # YYYY-MM-DD

    # ...soit une période (start + durationDays)
    period: Period | None = None

    passengers: Passengers = Field(default_factory=Passengers)

    # Contraintes & options
    maxStops: int = 1
    budgetPerPaxEUR: float | None = None
    flexDays: int |
