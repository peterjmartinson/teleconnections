"""MJO seasonal phase lag composite analyzer (active amplitude >= 1.0, phases 1..8)."""
from __future__ import annotations

import logging
import sqlite3
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from ..db import upsert_mjo_effects
from ..models import MJOEffectRecord
from .climatology import compute_calendar_normals_df
from .composites import (
    DEFAULT_LAG_WINDOWS,
    ROLLING_SEASONS,
    _compute_stats_for_subset,
    compute_forward_window_means,
)

logger = logging.getLogger(__name__)


def compute_mjo_effects(
    conn: sqlite3.Connection,
    location_id: str,
    weather_df: Optional[pd.DataFrame] = None,
    lag_windows: Sequence[Tuple[int, int]] = DEFAULT_LAG_WINDOWS,
    active_amplitude: float = 1.0,
) -> List[MJOEffectRecord]:
    """Compute seasonal lagged MJO composites for active MJO observations (amplitude >= 1.0)."""
    if weather_df is None:
        raw_w = pd.read_sql_query(
            """
            SELECT location_id, observation_date, tmean_c, tmin_c, tmax_c, precip_mm,
                   normal_tmean_c, normal_tmin_c, normal_tmax_c,
                   tmean_anomaly_c, tmin_anomaly_c, tmax_anomaly_c
            FROM weather_daily
            WHERE location_id = ?
            ORDER BY observation_date ASC;
            """,
            conn,
            params=(location_id,),
        )
        if raw_w.empty:
            return []
        weather_df = compute_calendar_normals_df(raw_w)
    elif "precip_anomaly_mm" not in weather_df.columns:
        weather_df = compute_calendar_normals_df(weather_df)

    w_df = weather_df.sort_values("observation_date").reset_index(drop=True)
    w_df["month"] = pd.to_datetime(w_df["observation_date"]).dt.month

    tmean_anom = pd.to_numeric(w_df["tmean_anomaly_c"], errors="coerce").to_numpy(dtype=float)
    precip_anom = pd.to_numeric(w_df["precip_anomaly_mm"], errors="coerce").to_numpy(dtype=float)
    months = w_df["month"].to_numpy(dtype=np.int32)

    lag_series_map: Dict[Tuple[int, int, str], np.ndarray] = {}
    for l_start, l_end in lag_windows:
        lag_series_map[(l_start, l_end, "tmean_anomaly_c")] = compute_forward_window_means(
            tmean_anom, l_start, l_end
        )
        lag_series_map[(l_start, l_end, "precip_mm")] = compute_forward_window_means(
            precip_anom, l_start, l_end
        )

    mjo_df = pd.read_sql_query(
        """
        SELECT observation_date, phase, amplitude, active
        FROM mjo_observation
        ORDER BY observation_date ASC;
        """,
        conn,
    )
    if mjo_df.empty:
        logger.warning("No mjo_observation rows found in database")
        return []

    # Map observation_date -> phase (only where amplitude >= active_amplitude and active == 1)
    active_phase_map: Dict[str, int] = {}
    all_mjo_dates: set[str] = set()
    for _, r in mjo_df.iterrows():
        d_str = str(r["observation_date"])
        all_mjo_dates.add(d_str)
        if float(r["amplitude"]) >= active_amplitude and int(r["active"]) == 1:
            active_phase_map[d_str] = int(r["phase"])

    has_mjo_obs = w_df["observation_date"].isin(all_mjo_dates).to_numpy(dtype=bool)
    active_phase_arr = (
        w_df["observation_date"]
        .map(active_phase_map)
        .fillna(0)
        .to_numpy(dtype=np.int32)
    )

    effects: List[MJOEffectRecord] = []
    for season_name, season_months in ROLLING_SEASONS.items():
        season_mask = np.isin(months, season_months) & has_mjo_obs

        for phase_num in range(1, 9):
            in_phase_mask = active_phase_arr == phase_num

            for l_start, l_end in lag_windows:
                for var_name in ("tmean_anomaly_c", "precip_mm"):
                    win_vals = lag_series_map[(l_start, l_end, var_name)]
                    mean_e, med_e, n_cnt, p_val, conf = _compute_stats_for_subset(
                        window_vals=win_vals,
                        in_state_mask=in_phase_mask,
                        season_mask=season_mask,
                        min_sep_days=5,
                    )
                    effects.append(
                        MJOEffectRecord(
                            location_id=location_id,
                            season=season_name,
                            phase=phase_num,
                            lag_start_days=l_start,
                            lag_end_days=l_end,
                            variable=var_name,
                            mean_effect=mean_e,
                            median_effect=med_e,
                            sample_count=n_cnt,
                            p_value=p_val,
                            confidence=conf,
                        )
                    )

    upsert_mjo_effects(conn, effects)
    logger.info(
        "Computed and stored %d mjo_effect rows for %s",
        len(effects),
        location_id,
    )
    return effects
