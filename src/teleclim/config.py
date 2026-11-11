"""Configuration loading and database path resolution for teleclim.

Follows the exact DB path resolution convention used in Screamsheet:
    1. TELECLIM_DB environment variable (if set)
    2. Explicit config path in config/settings.yaml (if set and not __DB_PATH__)
    3. Platform default:
         - ~/database/climate.db  (Linux / macOS)
         - C:\\database\\climate.db (Windows)
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_SETTINGS_PATH = _PROJECT_ROOT / "config" / "settings.yaml"


def get_default_db_path() -> Path:
    """Return platform default path for climate.db alongside screamsheet.db."""
    if sys.platform.startswith("win"):
        return Path("C:/database/climate.db")
    return Path.home() / "database" / "climate.db"


@dataclass
class DatabaseConfig:
    path: str = ""


@dataclass
class ERA5Config:
    provider: str = "open_meteo"
    history_start: str = "1979-01-01"
    history_end: str = ""
    finalization_lag_days: int = 5
    raw_cache_dir: str = "data/raw/era5"

    def resolved_history_end(self, ref_date: Optional[date] = None) -> str:
        """Return explicit history_end or (today - finalization_lag_days) to include 2026+ data."""
        if self.history_end and self.history_end.strip():
            return self.history_end.strip()
        today = ref_date or date.today()
        return (today - timedelta(days=self.finalization_lag_days)).isoformat()


@dataclass
class IndicesConfig:
    raw_cache_dir: str = "data/raw/indices"
    ao_url: str = "https://ftp.cpc.ncep.noaa.gov/cwlinks/norm.daily.ao.cdas.z1000.19500101_current.csv"
    nao_url: str = "https://ftp.cpc.ncep.noaa.gov/cwlinks/norm.daily.nao.cdas.z500.19500101_current.csv"
    pna_url: str = "https://ftp.cpc.ncep.noaa.gov/cwlinks/norm.daily.pna.cdas.z500.19500101_current.csv"
    nino34_url: str = "https://www.cpc.ncep.noaa.gov/data/indices/detrend.nino34.ascii.txt"
    mjo_bom_url: str = "http://www.bom.gov.au/clim_data/IDCKGEM000/rmm.74toRealtime.txt"
    mjo_psl_fallback_url: str = "https://psl.noaa.gov/mjo/mjoindex/romi.cpcolr.1x.txt"


@dataclass
class AnalysisConfig:
    climatology_window_days: int = 15
    mjo_active_amplitude: float = 1.0
    lag_windows: List[Tuple[int, int]] = field(
        default_factory=lambda: [(0, 5), (6, 10), (11, 15), (16, 20)]
    )


@dataclass
class TeleclimConfig:
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    era5: ERA5Config = field(default_factory=ERA5Config)
    indices: IndicesConfig = field(default_factory=IndicesConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)

    def resolve_raw_era5_dir(self) -> Path:
        p = Path(self.era5.raw_cache_dir)
        return p if p.is_absolute() else (_PROJECT_ROOT / p)

    def resolve_raw_indices_dir(self) -> Path:
        p = Path(self.indices.raw_cache_dir)
        return p if p.is_absolute() else (_PROJECT_ROOT / p)


def load_config(settings_path: Optional[Path] = None) -> TeleclimConfig:
    """Load config/settings.yaml, falling back to defaults if absent."""
    path = settings_path or _DEFAULT_SETTINGS_PATH
    raw = {}
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
            if isinstance(loaded, dict):
                raw = loaded

    db_raw = raw.get("database") or {}
    era5_raw = raw.get("era5") or {}
    idx_raw = raw.get("indices") or {}
    ana_raw = raw.get("analysis") or {}

    lag_raw = ana_raw.get("lag_windows")
    lag_windows: List[Tuple[int, int]] = [(0, 5), (6, 10), (11, 15), (16, 20)]
    if isinstance(lag_raw, list) and lag_raw:
        parsed_lags: List[Tuple[int, int]] = []
        for item in lag_raw:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                parsed_lags.append((int(item[0]), int(item[1])))
        if parsed_lags:
            lag_windows = parsed_lags

    return TeleclimConfig(
        database=DatabaseConfig(path=str(db_raw.get("path", "") or "")),
        era5=ERA5Config(
            provider=str(era5_raw.get("provider", "open_meteo")),
            history_start=str(era5_raw.get("history_start", "1979-01-01")),
            history_end=str(era5_raw.get("history_end", "") or ""),
            finalization_lag_days=int(era5_raw.get("finalization_lag_days", 5)),
            raw_cache_dir=str(era5_raw.get("raw_cache_dir", "data/raw/era5")),
        ),
        indices=IndicesConfig(
            raw_cache_dir=str(idx_raw.get("raw_cache_dir", "data/raw/indices")),
            ao_url=str(idx_raw.get("ao_url", IndicesConfig.ao_url)),
            nao_url=str(idx_raw.get("nao_url", IndicesConfig.nao_url)),
            pna_url=str(idx_raw.get("pna_url", IndicesConfig.pna_url)),
            nino34_url=str(idx_raw.get("nino34_url", IndicesConfig.nino34_url)),
            mjo_bom_url=str(idx_raw.get("mjo_bom_url", IndicesConfig.mjo_bom_url)),
            mjo_psl_fallback_url=str(
                idx_raw.get("mjo_psl_fallback_url", IndicesConfig.mjo_psl_fallback_url)
            ),
        ),
        analysis=AnalysisConfig(
            climatology_window_days=int(ana_raw.get("climatology_window_days", 15)),
            mjo_active_amplitude=float(ana_raw.get("mjo_active_amplitude", 1.0)),
            lag_windows=lag_windows,
        ),
    )


def get_db_path(
    explicit_path: Optional[Path | str] = None,
    config: Optional[TeleclimConfig] = None,
) -> Path:
    """Resolve the path to the teleconnection climatology SQLite database.

    Resolution order:
        1. ``explicit_path`` argument (if provided)
        2. ``TELECLIM_DB`` environment variable (if set)
        3. ``database.path`` in ``config/settings.yaml`` (if set and not ``__DB_PATH__``)
        4. Platform default: ``~/database/climate.db`` (or ``C:/database/climate.db``)
    """
    if explicit_path:
        p = Path(explicit_path)
        return p if p.is_absolute() else (_PROJECT_ROOT / p)

    env_val = os.environ.get("TELECLIM_DB")
    if env_val and env_val.strip():
        return Path(env_val.strip()).expanduser()

    cfg = config or load_config()
    cfg_path = cfg.database.path.strip()
    if cfg_path and cfg_path != "__DB_PATH__":
        p = Path(cfg_path).expanduser()
        return p if p.is_absolute() else (_PROJECT_ROOT / p)

    return get_default_db_path()
