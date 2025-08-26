from pydantic import BaseModel, Field
from typing import Optional

class Pax(BaseModel):
    adults: int = 1
    children: int = 0
    infants: int = 0

class Period(BaseModel):
    start: str  # YYYY-MM-DD
    durationDays: int = Field(..., ge=1, le=60)

class SearchBody(BaseModel):
    originCity: str
    destinationCity: str
    departureDate: Optional[str] = None
    returnDate: Optional[str] = None
    period: Optional[Period] = None
    passengers: Pax = Pax()
    cabin: str = "ECONOMY"
    maxStops: int = 1
    budgetPerPaxEUR: Optional[float] = None
