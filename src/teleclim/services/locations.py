"""Location ERA5 download, climatology, and teleconnection/MJO composite service."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from ..analysis import (
    calculate_location_climatology,
    compute_mjo_effects,
    compute_teleconnection_effects,
)
from ..config import TeleclimConfig, load_config
from ..db import (
    get_connection,
    get_location,
    init_db,
    upsert_location,
    upsert_weather_daily,
)
from ..ingest import download_weather
from ..models import LocationRecord
from .indices import bootstrap_global_indices

logger = logging.getLogger(__name__)


def slugify_location_id(name: str) -> str:
    """Convert a display name like 'Bryn Mawr, PA' into 'bryn_mawr_pa'."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return slug or "location"


def bootstrap_location(
    location_id: str,
    name: str,
    latitude: float,
    longitude: float,
    start: Optional[str] = None,
    end: Optional[str] = None,
    db_path: Optional[Path | str] = None,
    config: Optional[TeleclimConfig] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Bootstrap a location's ERA5 weather history, daily normals, and teleconnection/MJO effects.

    Workflow:
      1. Ensure schema & global teleconnection indices exist in SQLite
      2. Create/update location row with bootstrap_status='in_progress'
      3. Download ERA5 daily weather (1979 through 2026+)
      4. Populate weather_daily and calculate ±15-day calendar normals & anomalies
      5. Compute seasonal lagged composites for AO, NAO, PNA, NINO34 -> teleconnection_effect
      6. Compute seasonal lagged composites for active MJO phases 1..8 -> mjo_effect
      7. Mark location.bootstrap_status='complete' with bootstrap_date
    """
    cfg = config or load_config()
    resolved_db = init_db(db_path=db_path, seed=True)
    loc_id = location_id.strip() or slugify_location_id(name)

    conn = get_connection(resolved_db)
    try:
        existing = get_location(conn, loc_id)
        if existing and existing.bootstrap_status == "complete" and not force:
            # Verify effects actually exist
            tc_cnt = conn.execute(
                "SELECT COUNT(*) FROM teleconnection_effect WHERE location_id = ?;",
                (loc_id,),
            ).fetchone()[0]
            mjo_cnt = conn.execute(
                "SELECT COUNT(*) FROM mjo_effect WHERE location_id = ?;",
                (loc_id,),
            ).fetchone()[0]
            if tc_cnt > 0 and mjo_cnt > 0:
                logger.info(
                    "Location %s (%s) already bootstrapped (%d teleconnection_effect, %d mjo_effect rows).",
                    loc_id,
                    name,
                    tc_cnt,
                    mjo_cnt,
                )
                return {
                    "location_id": loc_id,
                    "status": "complete",
                    "skipped": True,
                    "teleconnection_effect_rows": int(tc_cnt),
                    "mjo_effect_rows": int(mjo_cnt),
                }

        # Self-heal: if global indices have not been bootstrapped yet, bootstrap them now
        obs_cnt = conn.execute("SELECT COUNT(*) FROM teleconnection_observation;").fetchone()[0]
        mjo_obs_cnt = conn.execute("SELECT COUNT(*) FROM mjo_observation;").fetchone()[0]
    finally:
        conn.close()

    if obs_cnt == 0 or mjo_obs_cnt == 0:
        logger.info("Global index tables empty; running bootstrap_global_indices first...")
        bootstrap_global_indices(db_path=resolved_db, config=cfg)

    conn = get_connection(resolved_db)
    try:
        upsert_location(
            conn,
            LocationRecord(
                id=loc_id,
                name=name,
                latitude=latitude,
                longitude=longitude,
                bootstrap_status="in_progress",
            ),
        )
    finally:
        conn.close()

    era5_res = download_weather(
        latitude=latitude,
        longitude=longitude,
        start=start,
        end=end,
        location_id=loc_id,
        config=cfg,
    )

    conn = get_connection(resolved_db)
    try:
        weather_count = upsert_weather_daily(conn, era5_res.records)
        enriched_df = calculate_location_climatology(
            conn,
            location_id=loc_id,
            window_days=cfg.analysis.climatology_window_days,
        )
        tc_effects = compute_teleconnection_effects(
            conn,
            location_id=loc_id,
            weather_df=enriched_df,
            lag_windows=cfg.analysis.lag_windows,
        )
        mjo_effects = compute_mjo_effects(
            conn,
            location_id=loc_id,
            weather_df=enriched_df,
            lag_windows=cfg.analysis.lag_windows,
            active_amplitude=cfg.analysis.mjo_active_amplitude,
        )

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        upsert_location(
            conn,
            LocationRecord(
                id=loc_id,
                name=name,
                latitude=latitude,
                longitude=longitude,
                era5_latitude=era5_res.era5_latitude,
                era5_longitude=era5_res.era5_longitude,
                bootstrap_status="complete",
                bootstrap_date=now_iso,
                history_start=era5_res.history_start,
                history_end=era5_res.history_end,
            ),
        )
    finally:
        conn.close()

    summary = {
        "location_id": loc_id,
        "name": name,
        "status": "complete",
        "skipped": False,
        "era5_latitude": era5_res.era5_latitude,
        "era5_longitude": era5_res.era5_longitude,
        "history_start": era5_res.history_start,
        "history_end": era5_res.history_end,
        "weather_daily_rows": weather_count,
        "teleconnection_effect_rows": len(tc_effects),
        "mjo_effect_rows": len(mjo_effects),
    }
    logger.info("Bootstrapped location %s: %s", loc_id, summary)
    return summary
