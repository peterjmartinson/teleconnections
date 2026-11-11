"""ERA5 historical daily weather ingestion (1979–present, including 2026).

Supports two providers:
  1. ``open_meteo`` (default): Queries the Open-Meteo Historical Weather API
     (``https://archive-api.open-meteo.com/v1/era5``), which serves Copernicus ERA5 + ERA5T
     reanalysis daily ``temperature_2m_mean``, ``temperature_2m_min``, ``temperature_2m_max``,
     and ``precipitation_sum`` along with the exact ERA5 grid coordinates in ~3–4 seconds
     without requiring a Copernicus CDS account/queue.
  2. ``cds``: Queries the Copernicus Climate Data Store ``reanalysis-era5-single-levels-timeseries``
     dataset via ``cdsapi`` (requires ``~/.cdsapirc``).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from ..config import TeleclimConfig, load_config
from ..models import WeatherDailyRecord

logger = logging.getLogger(__name__)

OPEN_METEO_ERA5_URL = "https://archive-api.open-meteo.com/v1/era5"


@dataclass
class ERA5DownloadResult:
    """Result of downloading daily ERA5 weather for a location."""
    location_id: str
    requested_latitude: float
    requested_longitude: float
    era5_latitude: Optional[float]
    era5_longitude: Optional[float]
    history_start: str
    history_end: str
    records: List[WeatherDailyRecord]


def parse_open_meteo_era5_json(
    location_id: str,
    requested_lat: float,
    requested_lon: float,
    payload: Dict[str, Any],
) -> ERA5DownloadResult:
    """Parse an Open-Meteo /v1/era5 JSON payload into WeatherDailyRecord instances."""
    era5_lat = float(payload["latitude"]) if payload.get("latitude") is not None else None
    era5_lon = float(payload["longitude"]) if payload.get("longitude") is not None else None

    daily = payload.get("daily") or {}
    dates = daily.get("time") or []
    tmeans = daily.get("temperature_2m_mean") or []
    tmins = daily.get("temperature_2m_min") or []
    tmaxs = daily.get("temperature_2m_max") or []
    precips = daily.get("precipitation_sum") or []

    records: List[WeatherDailyRecord] = []
    for idx, d_str in enumerate(dates):
        tmean = tmeans[idx] if idx < len(tmeans) else None
        tmin = tmins[idx] if idx < len(tmins) else None
        tmax = tmaxs[idx] if idx < len(tmaxs) else None
        precip = precips[idx] if idx < len(precips) else None

        if tmean is None and tmin is None and tmax is None and precip is None:
            continue

        if tmean is None and tmin is not None and tmax is not None:
            tmean = 0.5 * (float(tmin) + float(tmax))

        records.append(
            WeatherDailyRecord(
                location_id=location_id,
                observation_date=str(d_str),
                tmean_c=round(float(tmean), 3) if tmean is not None else None,
                tmin_c=round(float(tmin), 3) if tmin is not None else None,
                tmax_c=round(float(tmax), 3) if tmax is not None else None,
                precip_mm=round(float(precip), 3) if precip is not None else None,
            )
        )

    records.sort(key=lambda r: r.observation_date)
    h_start = records[0].observation_date if records else ""
    h_end = records[-1].observation_date if records else ""

    return ERA5DownloadResult(
        location_id=location_id,
        requested_latitude=requested_lat,
        requested_longitude=requested_lon,
        era5_latitude=era5_lat,
        era5_longitude=era5_lon,
        history_start=h_start,
        history_end=h_end,
        records=records,
    )


def _download_open_meteo_era5(
    location_id: str,
    latitude: float,
    longitude: float,
    start: str,
    end: str,
    raw_cache_dir: Optional[Path] = None,
    timeout: int = 60,
) -> ERA5DownloadResult:
    """Download ERA5 daily weather from Open-Meteo's /v1/era5 endpoint."""
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": start,
        "end_date": end,
        "daily": "temperature_2m_mean,temperature_2m_max,temperature_2m_min,precipitation_sum",
        "timezone": "UTC",
    }
    logger.info(
        "Requesting ERA5 daily series (%s to %s) for %s (%.4f, %.4f) via Open-Meteo",
        start,
        end,
        location_id,
        latitude,
        longitude,
    )
    resp = requests.get(OPEN_METEO_ERA5_URL, params=params, timeout=timeout)
    resp.raise_for_status()
    payload = resp.json()

    if raw_cache_dir is not None:
        raw_cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = raw_cache_dir / f"{location_id}_{start}_{end}_era5.json"
        cache_file.write_text(json.dumps(payload), encoding="utf-8")

    return parse_open_meteo_era5_json(
        location_id=location_id,
        requested_lat=latitude,
        requested_lon=longitude,
        payload=payload,
    )


def _download_cds_era5(
    location_id: str,
    latitude: float,
    longitude: float,
    start: str,
    end: str,
    raw_cache_dir: Optional[Path] = None,
) -> ERA5DownloadResult:
    """Download hourly ERA5 time-series from Copernicus CDS via cdsapi and aggregate to daily."""
    import cdsapi
    import pandas as pd

    target_dir = raw_cache_dir or Path("data/raw/era5")
    target_dir.mkdir(parents=True, exist_ok=True)
    out_file = target_dir / f"{location_id}_{start}_{end}_cds.csv"

    client = cdsapi.Client()
    client.retrieve(
        "reanalysis-era5-single-levels-timeseries",
        {
            "variable": ["2m_temperature", "total_precipitation"],
            "location": {"longitude": longitude, "latitude": latitude},
            "date": [f"{start}/{end}"],
            "data_format": "csv",
        },
        str(out_file),
    )

    df = pd.read_csv(out_file, comment="#")
    time_col = next((c for c in df.columns if "time" in c.lower() or "date" in c.lower()), df.columns[0])
    temp_col = next((c for c in df.columns if "t2m" in c.lower() or "temp" in c.lower()), None)
    precip_col = next((c for c in df.columns if "tp" in c.lower() or "precip" in c.lower()), None)

    if temp_col is None or precip_col is None:
        raise RuntimeError(f"Unexpected CDS ERA5 columns: {list(df.columns)}")

    df["obs_date"] = pd.to_datetime(df[time_col]).dt.strftime("%Y-%m-%d")
    # Convert Kelvin to Celsius if mean > 150, and meters to mm if max < 1.0
    t_series = pd.to_numeric(df[temp_col], errors="coerce")
    if t_series.mean() > 150.0:
        t_series = t_series - 273.15
    p_series = pd.to_numeric(df[precip_col], errors="coerce")
    if p_series.max() < 2.0:
        p_series = p_series * 1000.0

    df["temp_c"] = t_series
    df["precip_mm"] = p_series

    era5_lat = float(df["latitude"].iloc[0]) if "latitude" in df.columns else round(latitude * 4) / 4.0
    era5_lon = float(df["longitude"].iloc[0]) if "longitude" in df.columns else round(longitude * 4) / 4.0

    grouped = df.groupby("obs_date", as_index=False).agg(
        tmean_c=("temp_c", "mean"),
        tmin_c=("temp_c", "min"),
        tmax_c=("temp_c", "max"),
        precip_mm=("precip_mm", "sum"),
    )

    records = [
        WeatherDailyRecord(
            location_id=location_id,
            observation_date=str(row["obs_date"]),
            tmean_c=round(float(row["tmean_c"]), 3),
            tmin_c=round(float(row["tmin_c"]), 3),
            tmax_c=round(float(row["tmax_c"]), 3),
            precip_mm=round(float(row["precip_mm"]), 3),
        )
        for _, row in grouped.iterrows()
    ]
    records.sort(key=lambda r: r.observation_date)
    return ERA5DownloadResult(
        location_id=location_id,
        requested_latitude=latitude,
        requested_longitude=longitude,
        era5_latitude=era5_lat,
        era5_longitude=era5_lon,
        history_start=records[0].observation_date if records else start,
        history_end=records[-1].observation_date if records else end,
        records=records,
    )


def download_weather(
    latitude: float,
    longitude: float,
    start: Optional[str] = None,
    end: Optional[str] = None,
    location_id: str = "location",
    config: Optional[TeleclimConfig] = None,
    ref_date: Optional[date] = None,
) -> ERA5DownloadResult:
    """Download historical daily ERA5 weather (1979 through 2026+) for a coordinate pair."""
    cfg = config or load_config()
    start_str = start or cfg.era5.history_start
    end_str = end or cfg.era5.resolved_history_end(ref_date=ref_date)
    raw_dir = cfg.resolve_raw_era5_dir()

    if cfg.era5.provider.lower() == "cds":
        return _download_cds_era5(
            location_id=location_id,
            latitude=latitude,
            longitude=longitude,
            start=start_str,
            end=end_str,
            raw_cache_dir=raw_dir,
        )

    return _download_open_meteo_era5(
        location_id=location_id,
        latitude=latitude,
        longitude=longitude,
        start=start_str,
        end=end_str,
        raw_cache_dir=raw_dir,
    )
