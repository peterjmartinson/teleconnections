"""SQLite database schema initialization and data access layer for teleclim.

Implements the exact v1 seven-table schema specified in Issue #1:
  1. location
  2. teleconnection
  3. teleconnection_observation
  4. mjo_observation
  5. weather_daily
  6. teleconnection_effect
  7. mjo_effect
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable, List, Optional

from .config import get_db_path
from .models import (
    LocationRecord,
    MJOEffectRecord,
    MJOObservationRecord,
    TeleconnectionEffectRecord,
    TeleconnectionMetadataRecord,
    TeleconnectionObservationRecord,
    WeatherDailyRecord,
)

SCHEMA_DDL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS location (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,

    latitude         REAL NOT NULL,
    longitude        REAL NOT NULL,

    era5_latitude    REAL,
    era5_longitude   REAL,

    bootstrap_status TEXT NOT NULL DEFAULT 'pending',
    bootstrap_date   TEXT,

    history_start    TEXT,
    history_end      TEXT
);

CREATE TABLE IF NOT EXISTS teleconnection (
    code             TEXT PRIMARY KEY,
    name             TEXT NOT NULL,

    source           TEXT NOT NULL,
    source_url       TEXT,

    frequency        TEXT NOT NULL,
    analysis_type    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS teleconnection_observation (
    code             TEXT NOT NULL,
    observation_date TEXT NOT NULL,
    value            REAL NOT NULL,

    PRIMARY KEY (code, observation_date),

    FOREIGN KEY (code)
        REFERENCES teleconnection(code)
);

CREATE TABLE IF NOT EXISTS mjo_observation (
    observation_date TEXT PRIMARY KEY,

    rmm1             REAL NOT NULL,
    rmm2             REAL NOT NULL,

    phase            INTEGER NOT NULL,
    amplitude        REAL NOT NULL,
    active           INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS weather_daily (
    location_id       TEXT NOT NULL,
    observation_date  TEXT NOT NULL,

    tmean_c           REAL,
    tmin_c            REAL,
    tmax_c            REAL,
    precip_mm         REAL,

    normal_tmean_c    REAL,
    normal_tmin_c     REAL,
    normal_tmax_c     REAL,

    tmean_anomaly_c   REAL,
    tmin_anomaly_c    REAL,
    tmax_anomaly_c    REAL,

    PRIMARY KEY (
        location_id,
        observation_date
    ),

    FOREIGN KEY (location_id)
        REFERENCES location(id)
);

CREATE TABLE IF NOT EXISTS teleconnection_effect (
    location_id       TEXT NOT NULL,

    code              TEXT NOT NULL,
    season            TEXT NOT NULL,
    state             TEXT NOT NULL,

    lag_start_days    INTEGER NOT NULL,
    lag_end_days      INTEGER NOT NULL,

    variable          TEXT NOT NULL,

    mean_effect       REAL,
    median_effect     REAL,

    sample_count      INTEGER NOT NULL,
    p_value           REAL,

    confidence        TEXT NOT NULL,

    PRIMARY KEY (
        location_id,
        code,
        season,
        state,
        lag_start_days,
        lag_end_days,
        variable
    ),

    FOREIGN KEY (location_id)
        REFERENCES location(id),

    FOREIGN KEY (code)
        REFERENCES teleconnection(code)
);

CREATE TABLE IF NOT EXISTS mjo_effect (
    location_id       TEXT NOT NULL,

    season            TEXT NOT NULL,
    phase             INTEGER NOT NULL,

    lag_start_days    INTEGER NOT NULL,
    lag_end_days      INTEGER NOT NULL,

    variable          TEXT NOT NULL,

    mean_effect       REAL,
    median_effect     REAL,

    sample_count      INTEGER NOT NULL,
    p_value           REAL,

    confidence        TEXT NOT NULL,

    PRIMARY KEY (
        location_id,
        season,
        phase,
        lag_start_days,
        lag_end_days,
        variable
    ),

    FOREIGN KEY (location_id)
        REFERENCES location(id)
);

CREATE INDEX IF NOT EXISTS ix_tele_obs_code_date
    ON teleconnection_observation(code, observation_date);

CREATE INDEX IF NOT EXISTS ix_weather_daily_loc_date
    ON weather_daily(location_id, observation_date);

CREATE INDEX IF NOT EXISTS ix_tele_effect_lookup
    ON teleconnection_effect(location_id, season, code, state);

CREATE INDEX IF NOT EXISTS ix_mjo_effect_lookup
    ON mjo_effect(location_id, season, phase);
"""

INITIAL_TELECONNECTIONS: List[TeleconnectionMetadataRecord] = [
    TeleconnectionMetadataRecord(
        code="AO",
        name="Arctic Oscillation",
        source="NOAA CPC",
        source_url="https://ftp.cpc.ncep.noaa.gov/cwlinks/norm.daily.ao.cdas.z1000.19500101_current.csv",
        frequency="daily",
        analysis_type="numeric",
    ),
    TeleconnectionMetadataRecord(
        code="NAO",
        name="North Atlantic Oscillation",
        source="NOAA CPC",
        source_url="https://ftp.cpc.ncep.noaa.gov/cwlinks/norm.daily.nao.cdas.z500.19500101_current.csv",
        frequency="daily",
        analysis_type="numeric",
    ),
    TeleconnectionMetadataRecord(
        code="PNA",
        name="Pacific/North American",
        source="NOAA CPC",
        source_url="https://ftp.cpc.ncep.noaa.gov/cwlinks/norm.daily.pna.cdas.z500.19500101_current.csv",
        frequency="daily",
        analysis_type="numeric",
    ),
    TeleconnectionMetadataRecord(
        code="NINO34",
        name="Niño 3.4",
        source="NOAA CPC / PSL",
        source_url="https://www.cpc.ncep.noaa.gov/data/indices/detrend.nino34.ascii.txt",
        frequency="monthly",
        analysis_type="numeric",
    ),
    TeleconnectionMetadataRecord(
        code="MJO",
        name="Madden-Julian Oscillation",
        source="Australian BOM",
        source_url="http://www.bom.gov.au/clim_data/IDCKGEM000/rmm.74toRealtime.txt",
        frequency="daily",
        analysis_type="phase",
    ),
]


def get_connection(db_path: Optional[Path | str] = None) -> sqlite3.Connection:
    """Open a SQLite connection with foreign keys and WAL mode enabled."""
    resolved = get_db_path(db_path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(resolved), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def seed_teleconnections(conn: sqlite3.Connection) -> int:
    """Idempotently populate the ``teleconnection`` metadata table."""
    rows = [
        (r.code, r.name, r.source, r.source_url, r.frequency, r.analysis_type)
        for r in INITIAL_TELECONNECTIONS
    ]
    conn.executemany(
        """
        INSERT INTO teleconnection (code, name, source, source_url, frequency, analysis_type)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(code) DO UPDATE SET
            name = excluded.name,
            source = excluded.source,
            source_url = excluded.source_url,
            frequency = excluded.frequency,
            analysis_type = excluded.analysis_type;
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def init_db(db_path: Optional[Path | str] = None, seed: bool = True) -> Path:
    """Create all seven v1 tables and seed initial teleconnection metadata."""
    resolved = get_db_path(db_path)
    conn = get_connection(resolved)
    try:
        conn.executescript(SCHEMA_DDL)
        if seed:
            seed_teleconnections(conn)
        conn.commit()
    finally:
        conn.close()
    return resolved


def upsert_location(conn: sqlite3.Connection, loc: LocationRecord) -> None:
    """Insert or update a ``location`` record."""
    conn.execute(
        """
        INSERT INTO location (
            id, name, latitude, longitude,
            era5_latitude, era5_longitude,
            bootstrap_status, bootstrap_date,
            history_start, history_end
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            latitude = excluded.latitude,
            longitude = excluded.longitude,
            era5_latitude = COALESCE(excluded.era5_latitude, location.era5_latitude),
            era5_longitude = COALESCE(excluded.era5_longitude, location.era5_longitude),
            bootstrap_status = excluded.bootstrap_status,
            bootstrap_date = COALESCE(excluded.bootstrap_date, location.bootstrap_date),
            history_start = COALESCE(excluded.history_start, location.history_start),
            history_end = COALESCE(excluded.history_end, location.history_end);
        """,
        (
            loc.id,
            loc.name,
            loc.latitude,
            loc.longitude,
            loc.era5_latitude,
            loc.era5_longitude,
            loc.bootstrap_status,
            loc.bootstrap_date,
            loc.history_start,
            loc.history_end,
        ),
    )
    conn.commit()


def get_location(conn: sqlite3.Connection, location_id: str) -> Optional[LocationRecord]:
    """Fetch a single location record by ID."""
    row = conn.execute(
        "SELECT * FROM location WHERE id = ?;", (location_id,)
    ).fetchone()
    if row is None:
        return None
    return LocationRecord(
        id=row["id"],
        name=row["name"],
        latitude=float(row["latitude"]),
        longitude=float(row["longitude"]),
        era5_latitude=float(row["era5_latitude"]) if row["era5_latitude"] is not None else None,
        era5_longitude=float(row["era5_longitude"]) if row["era5_longitude"] is not None else None,
        bootstrap_status=row["bootstrap_status"],
        bootstrap_date=row["bootstrap_date"],
        history_start=row["history_start"],
        history_end=row["history_end"],
    )


def upsert_teleconnection_observations(
    conn: sqlite3.Connection,
    records: Iterable[TeleconnectionObservationRecord],
) -> int:
    """Batch upsert numerical teleconnection observations."""
    rows = [(r.code, r.observation_date, float(r.value)) for r in records]
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT INTO teleconnection_observation (code, observation_date, value)
        VALUES (?, ?, ?)
        ON CONFLICT(code, observation_date) DO UPDATE SET
            value = excluded.value;
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def upsert_mjo_observations(
    conn: sqlite3.Connection,
    records: Iterable[MJOObservationRecord],
) -> int:
    """Batch upsert daily MJO RMM observations."""
    rows = [
        (
            r.observation_date,
            float(r.rmm1),
            float(r.rmm2),
            int(r.phase),
            float(r.amplitude),
            int(r.active),
        )
        for r in records
    ]
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT INTO mjo_observation (observation_date, rmm1, rmm2, phase, amplitude, active)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(observation_date) DO UPDATE SET
            rmm1 = excluded.rmm1,
            rmm2 = excluded.rmm2,
            phase = excluded.phase,
            amplitude = excluded.amplitude,
            active = excluded.active;
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def upsert_weather_daily(
    conn: sqlite3.Connection,
    records: Iterable[WeatherDailyRecord],
) -> int:
    """Batch upsert daily ERA5 weather observations and anomalies."""
    rows = [
        (
            r.location_id,
            r.observation_date,
            r.tmean_c,
            r.tmin_c,
            r.tmax_c,
            r.precip_mm,
            r.normal_tmean_c,
            r.normal_tmin_c,
            r.normal_tmax_c,
            r.tmean_anomaly_c,
            r.tmin_anomaly_c,
            r.tmax_anomaly_c,
        )
        for r in records
    ]
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT INTO weather_daily (
            location_id, observation_date,
            tmean_c, tmin_c, tmax_c, precip_mm,
            normal_tmean_c, normal_tmin_c, normal_tmax_c,
            tmean_anomaly_c, tmin_anomaly_c, tmax_anomaly_c
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(location_id, observation_date) DO UPDATE SET
            tmean_c = COALESCE(excluded.tmean_c, weather_daily.tmean_c),
            tmin_c = COALESCE(excluded.tmin_c, weather_daily.tmin_c),
            tmax_c = COALESCE(excluded.tmax_c, weather_daily.tmax_c),
            precip_mm = COALESCE(excluded.precip_mm, weather_daily.precip_mm),
            normal_tmean_c = COALESCE(excluded.normal_tmean_c, weather_daily.normal_tmean_c),
            normal_tmin_c = COALESCE(excluded.normal_tmin_c, weather_daily.normal_tmin_c),
            normal_tmax_c = COALESCE(excluded.normal_tmax_c, weather_daily.normal_tmax_c),
            tmean_anomaly_c = COALESCE(excluded.tmean_anomaly_c, weather_daily.tmean_anomaly_c),
            tmin_anomaly_c = COALESCE(excluded.tmin_anomaly_c, weather_daily.tmin_anomaly_c),
            tmax_anomaly_c = COALESCE(excluded.tmax_anomaly_c, weather_daily.tmax_anomaly_c);
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def upsert_teleconnection_effects(
    conn: sqlite3.Connection,
    records: Iterable[TeleconnectionEffectRecord],
) -> int:
    """Batch upsert precomputed ``teleconnection_effect`` rows."""
    rows = [
        (
            r.location_id,
            r.code,
            r.season,
            r.state,
            int(r.lag_start_days),
            int(r.lag_end_days),
            r.variable,
            r.mean_effect,
            r.median_effect,
            int(r.sample_count),
            r.p_value,
            r.confidence,
        )
        for r in records
    ]
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT INTO teleconnection_effect (
            location_id, code, season, state,
            lag_start_days, lag_end_days, variable,
            mean_effect, median_effect, sample_count, p_value, confidence
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(
            location_id, code, season, state,
            lag_start_days, lag_end_days, variable
        ) DO UPDATE SET
            mean_effect = excluded.mean_effect,
            median_effect = excluded.median_effect,
            sample_count = excluded.sample_count,
            p_value = excluded.p_value,
            confidence = excluded.confidence;
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def upsert_mjo_effects(
    conn: sqlite3.Connection,
    records: Iterable[MJOEffectRecord],
) -> int:
    """Batch upsert precomputed ``mjo_effect`` rows."""
    rows = [
        (
            r.location_id,
            r.season,
            int(r.phase),
            int(r.lag_start_days),
            int(r.lag_end_days),
            r.variable,
            r.mean_effect,
            r.median_effect,
            int(r.sample_count),
            r.p_value,
            r.confidence,
        )
        for r in records
    ]
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT INTO mjo_effect (
            location_id, season, phase,
            lag_start_days, lag_end_days, variable,
            mean_effect, median_effect, sample_count, p_value, confidence
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(
            location_id, season, phase,
            lag_start_days, lag_end_days, variable
        ) DO UPDATE SET
            mean_effect = excluded.mean_effect,
            median_effect = excluded.median_effect,
            sample_count = excluded.sample_count,
            p_value = excluded.p_value,
            confidence = excluded.confidence;
        """,
        rows,
    )
    conn.commit()
    return len(rows)
