from __future__ import annotations

from typing import Optional
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
    destinationCity: Optional[str] = None

    # Soit dates exactes...
    departureDate: Optional[str] = None  # YYYY-MM-DD
    returnDate: Optional[str] = None     # YYYY-MM-DD

    # ...soit une période (start + durationDays)
    period: Optional[Period] = None

    passengers: Passengers = Field(default_factory=Passengers)

    # Contraintes & options
    maxStops: int = 1
    budgetPerPaxEUR: Optional[float] = None
    flexDays: Optional[int] = None
