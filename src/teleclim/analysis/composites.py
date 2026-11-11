"""Ordinary teleconnection seasonal lag composite analyzer (AO, NAO, PNA, NINO34)."""
from __future__ import annotations

import logging
import math
import sqlite3
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

try:
    from scipy import stats as _scipy_stats
except ImportError:
    _scipy_stats = None

from ..db import upsert_teleconnection_effects
from ..models import TeleconnectionEffectRecord
from .climatology import compute_calendar_normals_df

logger = logging.getLogger(__name__)


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction evaluation for the incomplete beta function (Lentz's method)."""
    max_iter = 200
    eps = 3.0e-12
    fpmin = 1.0e-30
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < fpmin:
        d = fpmin
    d = 1.0 / d
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def _regularized_incomplete_beta(a: float, b: float, x: float) -> float:
    """Compute the regularized incomplete beta function I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    bt = math.exp(a * math.log(x) + b * math.log(1.0 - x) - lbeta)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def _welch_ttest_pvalue(sample_a: np.ndarray, sample_b: np.ndarray) -> Optional[float]:
    """Compute two-sided Welch's t-test p-value (using scipy.stats if installed, else exact incomplete beta)."""
    if _scipy_stats is not None:
        res = _scipy_stats.ttest_ind(sample_a, sample_b, equal_var=False, nan_policy="omit")
        if res.pvalue is not None and not math.isnan(float(res.pvalue)):
            return float(res.pvalue)
        return None

    a = sample_a[~np.isnan(sample_a)]
    b = sample_b[~np.isnan(sample_b)]
    na = len(a)
    nb = len(b)
    if na < 2 or nb < 2:
        return None
    va = float(np.var(a, ddof=1))
    vb = float(np.var(b, ddof=1))
    se2 = va / na + vb / nb
    if se2 <= 1e-18:
        return None
    t_stat = abs(float(np.mean(a)) - float(np.mean(b))) / math.sqrt(se2)
    df_num = se2 * se2
    df_den = ((va / na) ** 2) / (na - 1) + ((vb / nb) ** 2) / (nb - 1)
    if df_den <= 1e-18:
        return None
    df = df_num / df_den
    x = df / (df + t_stat * t_stat)
    return float(_regularized_incomplete_beta(0.5 * df, 0.5, x))

# 12 rolling 3-month seasons as specified in Issue #1
ROLLING_SEASONS: Dict[str, Tuple[int, int, int]] = {
    "DJF": (12, 1, 2),
    "JFM": (1, 2, 3),
    "FMA": (2, 3, 4),
    "MAM": (3, 4, 5),
    "AMJ": (4, 5, 6),
    "MJJ": (5, 6, 7),
    "JJA": (6, 7, 8),
    "JAS": (7, 8, 9),
    "ASO": (8, 9, 10),
    "SON": (9, 10, 11),
    "OND": (10, 11, 12),
    "NDJ": (11, 12, 1),
}

# Canonical mapping from calendar month (1..12) to its centered 3-month season
SEASON_FOR_MONTH: Dict[int, str] = {
    1: "DJF",
    2: "JFM",
    3: "FMA",
    4: "MAM",
    5: "AMJ",
    6: "MJJ",
    7: "JJA",
    8: "JAS",
    9: "ASO",
    10: "SON",
    11: "OND",
    12: "NDJ",
}

DAILY_STATES: Tuple[str, ...] = (
    "strong_negative",
    "negative",
    "neutral",
    "positive",
    "strong_positive",
)

NINO34_STATES: Tuple[str, ...] = (
    "strong_la_nina",
    "la_nina",
    "neutral",
    "el_nino",
    "strong_el_nino",
)

DEFAULT_LAG_WINDOWS: Tuple[Tuple[int, int], ...] = (
    (0, 5),
    (6, 10),
    (11, 15),
    (16, 20),
)


def classify_index_state(code: str, value: float) -> str:
    """Classify a numeric teleconnection index value into its canonical state string."""
    code_u = code.upper().strip()
    if code_u == "NINO34":
        if value <= -1.5:
            return "strong_la_nina"
        if value <= -0.5:
            return "la_nina"
        if value < 0.5:
            return "neutral"
        if value < 1.5:
            return "el_nino"
        return "strong_el_nino"

    # AO, NAO, PNA
    if value <= -1.5:
        return "strong_negative"
    if value <= -0.5:
        return "negative"
    if value < 0.5:
        return "neutral"
    if value < 1.5:
        return "positive"
    return "strong_positive"


def compute_forward_window_means(
    values: np.ndarray,
    lag_start: int,
    lag_end: int,
) -> np.ndarray:
    """Compute forward lag-window means [t + lag_start .. t + lag_end] for every day t.

    Requires complete window coverage (no NaNs inside the lag window) so the final days
    of the weather record do not produce partial-window edge artifacts.
    """
    n = len(values)
    width = lag_end - lag_start + 1
    out = np.full(n, np.nan, dtype=float)
    if n <= lag_end or width <= 0:
        return out

    # Use cumulative sum of values and valid-counts for O(N) vectorized window means
    valid_mask = ~np.isnan(values)
    safe_vals = np.where(valid_mask, values, 0.0)

    csum = np.concatenate(([0.0], np.cumsum(safe_vals)))
    ccnt = np.concatenate(([0], np.cumsum(valid_mask.astype(np.int32))))

    max_t = n - lag_end
    idx_start = np.arange(0, max_t) + lag_start
    idx_end = np.arange(0, max_t) + lag_end + 1

    win_sum = csum[idx_end] - csum[idx_start]
    win_cnt = ccnt[idx_end] - ccnt[idx_start]

    # Require at least (width - 1) valid days in the window (full coverage)
    min_req = max(1, width - 1)
    good = win_cnt >= min_req
    out[:max_t] = np.where(good, win_sum / np.maximum(win_cnt, 1), np.nan)
    return out


def decluster_indices(indices: np.ndarray, min_separation_days: int = 5) -> np.ndarray:
    """Thin sorted integer day indices so selected points are separated by >= min_separation_days.

    Prevents overlapping 5-day lag windows from consecutive days in the same regime from
    artificially inflating degrees of freedom in Welch's t-test.
    """
    if len(indices) <= 1 or min_separation_days <= 1:
        return indices
    selected: List[int] = [int(indices[0])]
    last = int(indices[0])
    for idx in indices[1:]:
        i_val = int(idx)
        if i_val - last >= min_separation_days:
            selected.append(i_val)
            last = i_val
    return np.array(selected, dtype=np.int32)


def classify_confidence(p_value: Optional[float], effective_n: int, raw_n: int) -> str:
    """Classify statistical confidence into high / medium / low."""
    if p_value is None or math.isnan(p_value) or raw_n < 10 or effective_n < 5:
        return "low"
    if p_value < 0.05 and effective_n >= 12 and raw_n >= 25:
        return "high"
    if p_value < 0.15 and effective_n >= 6 and raw_n >= 12:
        return "medium"
    return "low"


def _compute_stats_for_subset(
    window_vals: np.ndarray,
    in_state_mask: np.ndarray,
    season_mask: np.ndarray,
    min_sep_days: int = 5,
) -> Tuple[Optional[float], Optional[float], int, Optional[float], str]:
    """Compute mean_effect, median_effect, sample_count, p_value, and confidence."""
    valid_mask = ~np.isnan(window_vals)
    target_mask = season_mask & in_state_mask & valid_mask
    target_indices = np.flatnonzero(target_mask)
    raw_n = int(len(target_indices))

    if raw_n == 0:
        return None, None, 0, None, "low"

    target_vals = window_vals[target_indices]
    mean_eff = round(float(np.mean(target_vals)), 3)
    median_eff = round(float(np.median(target_vals)), 3)

    # Decluttered independent episodes for honest significance testing
    indep_target_idx = decluster_indices(target_indices, min_separation_days=min_sep_days)
    eff_n = int(len(indep_target_idx))

    other_mask = season_mask & (~in_state_mask) & valid_mask
    other_indices = np.flatnonzero(other_mask)
    indep_other_idx = decluster_indices(other_indices, min_separation_days=min_sep_days)

    p_val: Optional[float] = None
    if eff_n >= 4 and len(indep_other_idx) >= 4:
        sample_a = window_vals[indep_target_idx]
        sample_b = window_vals[indep_other_idx]
        if np.std(sample_a) > 1e-9 or np.std(sample_b) > 1e-9:
            raw_p = _welch_ttest_pvalue(sample_a, sample_b)
            if raw_p is not None and not math.isnan(raw_p):
                p_val = round(float(raw_p), 5)

    conf = classify_confidence(p_val, effective_n=eff_n, raw_n=raw_n)
    return mean_eff, median_eff, raw_n, p_val, conf


def compute_teleconnection_effects(
    conn: sqlite3.Connection,
    location_id: str,
    weather_df: Optional[pd.DataFrame] = None,
    lag_windows: Sequence[Tuple[int, int]] = DEFAULT_LAG_WINDOWS,
) -> List[TeleconnectionEffectRecord]:
    """Compute seasonal lagged teleconnection composites for AO, NAO, PNA, and NINO34."""
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
    w_df["year_month"] = w_df["observation_date"].str.slice(0, 7)

    tmean_anom = pd.to_numeric(w_df["tmean_anomaly_c"], errors="coerce").to_numpy(dtype=float)
    precip_anom = pd.to_numeric(w_df["precip_anomaly_mm"], errors="coerce").to_numpy(dtype=float)
    months = w_df["month"].to_numpy(dtype=np.int32)

    # Precompute forward lag-window means for each lag window and variable
    lag_series_map: Dict[Tuple[int, int, str], np.ndarray] = {}
    for l_start, l_end in lag_windows:
        lag_series_map[(l_start, l_end, "tmean_anomaly_c")] = compute_forward_window_means(
            tmean_anom, l_start, l_end
        )
        lag_series_map[(l_start, l_end, "precip_mm")] = compute_forward_window_means(
            precip_anom, l_start, l_end
        )

    obs_df = pd.read_sql_query(
        """
        SELECT code, observation_date, value
        FROM teleconnection_observation
        WHERE code IN ('AO', 'NAO', 'PNA', 'NINO34')
        ORDER BY code, observation_date ASC;
        """,
        conn,
    )
    if obs_df.empty:
        logger.warning("No teleconnection_observation rows found in database")
        return []

    effects: List[TeleconnectionEffectRecord] = []

    for code in ("AO", "NAO", "PNA", "NINO34"):
        sub_obs = obs_df[obs_df["code"] == code]
        if sub_obs.empty:
            continue

        if code == "NINO34":
            # Join monthly NINO34 onto daily weather by YYYY-MM
            ym_map = {
                str(r["observation_date"])[:7]: float(r["value"])
                for _, r in sub_obs.iterrows()
            }
            matched_vals = w_df["year_month"].map(ym_map).to_numpy(dtype=float)
            states_list = NINO34_STATES
            min_sep = 15  # Decluster monthly ENSO windows to ~2 independent samples per month
        else:
            date_map = {
                str(r["observation_date"]): float(r["value"])
                for _, r in sub_obs.iterrows()
            }
            matched_vals = w_df["observation_date"].map(date_map).to_numpy(dtype=float)
            states_list = DAILY_STATES
            min_sep = 5

        valid_idx_mask = ~np.isnan(matched_vals)
        state_arr = np.array(
            [
                classify_index_state(code, float(v)) if not math.isnan(float(v)) else ""
                for v in matched_vals
            ],
            dtype=object,
        )

        for season_name, season_months in ROLLING_SEASONS.items():
            season_mask = np.isin(months, season_months) & valid_idx_mask

            for state_name in states_list:
                in_state_mask = state_arr == state_name

                for l_start, l_end in lag_windows:
                    for var_name in ("tmean_anomaly_c", "precip_mm"):
                        win_vals = lag_series_map[(l_start, l_end, var_name)]
                        mean_e, med_e, n_cnt, p_val, conf = _compute_stats_for_subset(
                            window_vals=win_vals,
                            in_state_mask=in_state_mask,
                            season_mask=season_mask,
                            min_sep_days=min_sep,
                        )
                        effects.append(
                            TeleconnectionEffectRecord(
                                location_id=location_id,
                                code=code,
                                season=season_name,
                                state=state_name,
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

    upsert_teleconnection_effects(conn, effects)
    logger.info(
        "Computed and stored %d teleconnection_effect rows for %s",
        len(effects),
        location_id,
    )
    return effects
