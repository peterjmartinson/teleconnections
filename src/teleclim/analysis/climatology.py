"""Local ±15-day calendar-day climatological normals and anomaly calculator."""
from __future__ import annotations

import logging
import sqlite3
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _day_of_year_365(dt_series: pd.Series) -> np.ndarray:
    """Map dates onto a 1..365 calendar day-of-year (mapping Feb 29 onto Feb 28 / day 59)."""
    doy = dt_series.dt.dayofyear.to_numpy(dtype=np.int32)
    is_leap = dt_series.dt.is_leap_year.to_numpy(dtype=bool)
    month = dt_series.dt.month.to_numpy(dtype=np.int32)
    # In leap years, days after Feb 28 (doy >= 60, month >= 3 or Feb 29) shift back by 1
    shift_mask = is_leap & ((month > 2) | (doy == 60))
    doy = np.where(shift_mask, doy - 1, doy)
    return np.clip(doy, 1, 365)


def compute_calendar_normals_df(
    df: pd.DataFrame,
    window_days: int = 15,
) -> pd.DataFrame:
    """Compute ±window_days circular calendar-day normals and anomalies on a weather DataFrame.

    Input DataFrame must contain columns:
      ``observation_date``, ``tmean_c``, ``tmin_c``, ``tmax_c``, ``precip_mm``

    Adds/updates columns:
      ``normal_tmean_c``, ``normal_tmin_c``, ``normal_tmax_c``,
      ``tmean_anomaly_c``, ``tmin_anomaly_c``, ``tmax_anomaly_c``,
      ``normal_precip_mm``, ``precip_anomaly_mm``
    """
    if df.empty:
        return df.copy()

    out = df.copy()
    dt = pd.to_datetime(out["observation_date"])
    doy = _day_of_year_365(dt)
    out["_doy"] = doy

    tmean_arr = pd.to_numeric(out["tmean_c"], errors="coerce").to_numpy(dtype=float)
    tmin_arr = pd.to_numeric(out["tmin_c"], errors="coerce").to_numpy(dtype=float)
    tmax_arr = pd.to_numeric(out["tmax_c"], errors="coerce").to_numpy(dtype=float)
    precip_arr = pd.to_numeric(out["precip_mm"], errors="coerce").to_numpy(dtype=float)

    norm_tmean_by_doy = np.full(366, np.nan, dtype=float)
    norm_tmin_by_doy = np.full(366, np.nan, dtype=float)
    norm_tmax_by_doy = np.full(366, np.nan, dtype=float)
    norm_precip_by_doy = np.full(366, np.nan, dtype=float)

    for target_doy in range(1, 366):
        # Circular distance on a 365-day ring
        diff = np.abs(doy - target_doy)
        circ_dist = np.minimum(diff, 365 - diff)
        mask = circ_dist <= window_days

        if np.any(mask):
            v_tmean = tmean_arr[mask]
            v_tmin = tmin_arr[mask]
            v_tmax = tmax_arr[mask]
            v_precip = precip_arr[mask]

            if np.any(~np.isnan(v_tmean)):
                norm_tmean_by_doy[target_doy] = float(np.nanmean(v_tmean))
            if np.any(~np.isnan(v_tmin)):
                norm_tmin_by_doy[target_doy] = float(np.nanmean(v_tmin))
            if np.any(~np.isnan(v_tmax)):
                norm_tmax_by_doy[target_doy] = float(np.nanmean(v_tmax))
            if np.any(~np.isnan(v_precip)):
                norm_precip_by_doy[target_doy] = float(np.nanmean(v_precip))

    out["normal_tmean_c"] = np.round(norm_tmean_by_doy[doy], 3)
    out["normal_tmin_c"] = np.round(norm_tmin_by_doy[doy], 3)
    out["normal_tmax_c"] = np.round(norm_tmax_by_doy[doy], 3)
    out["normal_precip_mm"] = np.round(norm_precip_by_doy[doy], 3)

    out["tmean_anomaly_c"] = np.round(tmean_arr - out["normal_tmean_c"], 3)
    out["tmin_anomaly_c"] = np.round(tmin_arr - out["normal_tmin_c"], 3)
    out["tmax_anomaly_c"] = np.round(tmax_arr - out["normal_tmax_c"], 3)
    out["precip_anomaly_mm"] = np.round(precip_arr - out["normal_precip_mm"], 3)

    out.drop(columns=["_doy"], inplace=True)
    return out


def calculate_location_climatology(
    conn: sqlite3.Connection,
    location_id: str,
    window_days: int = 15,
) -> Optional[pd.DataFrame]:
    """Load weather_daily for location_id, compute ±15-day normals/anomalies, and update SQLite.

    Returns the enriched DataFrame (including ``precip_anomaly_mm`` for downstream composites).
    """
    df = pd.read_sql_query(
        """
        SELECT location_id, observation_date, tmean_c, tmin_c, tmax_c, precip_mm
        FROM weather_daily
        WHERE location_id = ?
        ORDER BY observation_date ASC;
        """,
        conn,
        params=(location_id,),
    )
    if df.empty:
        logger.warning("No weather_daily rows found for location_id=%s", location_id)
        return None

    enriched = compute_calendar_normals_df(df, window_days=window_days)

    update_rows = [
        (
            None if pd.isna(row["normal_tmean_c"]) else float(row["normal_tmean_c"]),
            None if pd.isna(row["normal_tmin_c"]) else float(row["normal_tmin_c"]),
            None if pd.isna(row["normal_tmax_c"]) else float(row["normal_tmax_c"]),
            None if pd.isna(row["tmean_anomaly_c"]) else float(row["tmean_anomaly_c"]),
            None if pd.isna(row["tmin_anomaly_c"]) else float(row["tmin_anomaly_c"]),
            None if pd.isna(row["tmax_anomaly_c"]) else float(row["tmax_anomaly_c"]),
            str(row["location_id"]),
            str(row["observation_date"]),
        )
        for _, row in enriched.iterrows()
    ]

    conn.executemany(
        """
        UPDATE weather_daily
        SET normal_tmean_c = ?,
            normal_tmin_c = ?,
            normal_tmax_c = ?,
            tmean_anomaly_c = ?,
            tmin_anomaly_c = ?,
            tmax_anomaly_c = ?
        WHERE location_id = ? AND observation_date = ?;
        """,
        update_rows,
    )
    conn.commit()
    logger.info(
        "Updated climatological normals and anomalies for %s (%d days)",
        location_id,
        len(update_rows),
    )
    return enriched
