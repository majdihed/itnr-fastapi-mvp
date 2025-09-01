from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class Passengers(BaseModel):
    adults: int = 1
    children: int = 0
    infants: int = 0


class Period(BaseModel):
    start: str  # YYYY-MM-DD
    durationDays: int = Field(ge=1)


class SearchBody(BaseModel):
    originCity: str
    destinationCity: Optional[str] = None
    departureDate: Optional[str] = None  # YYYY-MM-DD
    returnDate: Optional[str] = None     # YYYY-MM-DD
    period: Optional[Period] = None
    passengers: Passengers = Passengers()
    cabin: str = "ECONOMY"
    maxStops: int = 1
    budgetPerPaxEUR: Optional[float] = None
    flexDays: Optional[int] = None
