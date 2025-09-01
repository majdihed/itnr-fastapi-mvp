from __future__ import annotations

import datetime as dt
import re
from typing import Any

import dateparser
import httpx
from fastapi import APIRouter, HTTPException

from .core import AMADEUS_HOST, CURRENCY, amadeus_token, city_to_iata
from .discover_router import discover as discover_fn
from .utils import count_stops, rank_offers, to_lite

router = APIRouter(prefix="/chat", tags=["chat"])

MONTHS_FR = {
    "janvier": 1,
    "février": 2,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "août": 8,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "décembre": 12,
    "decembre": 12,
}

# … (le reste du fichier inchangé)
