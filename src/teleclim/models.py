"""Typed domain models for the Teleconnection Climatology Builder."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class LocationRecord:
    """Row in the ``location`` table."""
    id: str
    name: str
    latitude: float
    longitude: float
    era5_latitude: Optional[float] = None
    era5_longitude: Optional[float] = None
    bootstrap_status: str = "pending"
    bootstrap_date: Optional[str] = None
    history_start: Optional[str] = None
    history_end: Optional[str] = None


@dataclass
class TeleconnectionMetadataRecord:
    """Row in the ``teleconnection`` metadata table."""
    code: str
    name: str
    source: str
    source_url: Optional[str]
    frequency: str
    analysis_type: str


@dataclass
class TeleconnectionObservationRecord:
    """Row in the ``teleconnection_observation`` table (AO, NAO, PNA, NINO34)."""
    code: str
    observation_date: str
    value: float


@dataclass
class MJOObservationRecord:
    """Row in the ``mjo_observation`` table."""
    observation_date: str
    rmm1: float
    rmm2: float
    phase: int
    amplitude: float
    active: int


@dataclass
class WeatherDailyRecord:
    """Row in the ``weather_daily`` table."""
    location_id: str
    observation_date: str
    tmean_c: Optional[float] = None
    tmin_c: Optional[float] = None
    tmax_c: Optional[float] = None
    precip_mm: Optional[float] = None
    normal_tmean_c: Optional[float] = None
    normal_tmin_c: Optional[float] = None
    normal_tmax_c: Optional[float] = None
    tmean_anomaly_c: Optional[float] = None
    tmin_anomaly_c: Optional[float] = None
    tmax_anomaly_c: Optional[float] = None


@dataclass
class TeleconnectionEffectRecord:
    """Row in the ``teleconnection_effect`` output table."""
    location_id: str
    code: str
    season: str
    state: str
    lag_start_days: int
    lag_end_days: int
    variable: str
    mean_effect: Optional[float]
    median_effect: Optional[float]
    sample_count: int
    p_value: Optional[float]
    confidence: str


@dataclass
class MJOEffectRecord:
    """Row in the ``mjo_effect`` output table."""
    location_id: str
    season: str
    phase: int
    lag_start_days: int
    lag_end_days: int
    variable: str
    mean_effect: Optional[float]
    median_effect: Optional[float]
    sample_count: int
    p_value: Optional[float]
    confidence: str
