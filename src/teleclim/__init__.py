"""Teleconnection Climatology Builder (teleclim).

Downloads and analyzes historical teleconnection indices (AO, NAO, PNA, Niño 3.4, MJO)
and local ERA5 daily weather records (1979–present) to precompute seasonal lagged
teleconnection and MJO effects in SQLite for downstream consumption by Screamsheet.
"""
from __future__ import annotations

from .config import TeleclimConfig, get_db_path, load_config
from .db import get_connection, init_db
from .models import (
    LocationRecord,
    MJOEffectRecord,
    MJOObservationRecord,
    TeleconnectionEffectRecord,
    TeleconnectionMetadataRecord,
    TeleconnectionObservationRecord,
    WeatherDailyRecord,
)

__all__ = [
    "TeleclimConfig",
    "get_db_path",
    "load_config",
    "get_connection",
    "init_db",
    "LocationRecord",
    "TeleconnectionMetadataRecord",
    "TeleconnectionObservationRecord",
    "MJOObservationRecord",
    "WeatherDailyRecord",
    "TeleconnectionEffectRecord",
    "MJOEffectRecord",
]
