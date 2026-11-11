"""Unit and pipeline tests for the Teleconnection Climatology Builder (teleclim)."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from teleclim.analysis import (
    ROLLING_SEASONS,
    SEASON_FOR_MONTH,
    calculate_location_climatology,
    classify_index_state,
    compute_calendar_normals_df,
    compute_mjo_effects,
    compute_teleconnection_effects,
)
from teleclim.config import get_db_path, load_config
from teleclim.db import (
    get_connection,
    get_location,
    init_db,
    upsert_location,
    upsert_mjo_observations,
    upsert_teleconnection_observations,
    upsert_weather_daily,
)
from teleclim.ingest import (
    parse_bom_rmm_text,
    parse_cpc_cdas_csv,
    parse_nino34_ascii,
    parse_open_meteo_era5_json,
    parse_psl_romi_text,
)
from teleclim.models import (
    LocationRecord,
    MJOObservationRecord,
    TeleconnectionObservationRecord,
    WeatherDailyRecord,
)
from teleclim.services import slugify_location_id


def test_schema_and_initial_seed(tmp_path: Path) -> None:
    db_file = tmp_path / "climate.db"
    resolved = init_db(db_file)
    assert resolved == db_file
    assert db_file.exists()

    conn = get_connection(db_file)
    try:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table';"
            ).fetchall()
        }
        expected_tables = {
            "location",
            "teleconnection",
            "teleconnection_observation",
            "mjo_observation",
            "weather_daily",
            "teleconnection_effect",
            "mjo_effect",
        }
        assert expected_tables.issubset(tables)

        codes = {
            row["code"]
            for row in conn.execute("SELECT code FROM teleconnection;").fetchall()
        }
        assert codes == {"AO", "NAO", "PNA", "NINO34", "MJO"}
    finally:
        conn.close()


def test_db_path_resolution(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    custom = tmp_path / "custom_climate.db"
    monkeypatch.setenv("TELECLIM_DB", str(custom))
    assert get_db_path() == custom

    monkeypatch.delenv("TELECLIM_DB", raising=False)
    default_p = get_db_path()
    assert default_p.name == "climate.db"
    assert "database" in str(default_p)


def test_ingest_parsers() -> None:
    cpc_sample = "year,month,day,ao_index_cdas\n2025,1,1,-1.82\n2025,1,2,-0.45\n2025,1,3,NaN\n"
    ao_recs = parse_cpc_cdas_csv("AO", cpc_sample)
    assert len(ao_recs) == 2
    assert ao_recs[0].observation_date == "2025-01-01"
    assert ao_recs[0].value == pytest.approx(-1.82)

    nino_sample = (
        " YR   MON  TOTAL ClimAdjust ANOM\n"
        "2025   1   25.80   26.55   -0.75\n"
        "2025   2   26.10   26.70   -0.60\n"
    )
    nino_recs = parse_nino34_ascii(nino_sample)
    assert len(nino_recs) == 2
    assert nino_recs[0].observation_date == "2025-01-01"
    assert nino_recs[0].value == pytest.approx(-0.75)

    bom_sample = (
        "RMM values up to real time\n"
        " year, month, day, RMM1, RMM2, phase, amplitude.\n"
        " 2026 11 7 -0.835967 -2.286577 2 2.434600 Gottschalk10\n"
        " 2026 11 8 999.0 999.0 999 999.0 Missing\n"
    )
    bom_recs = parse_bom_rmm_text(bom_sample)
    assert len(bom_recs) == 1
    assert bom_recs[0].observation_date == "2026-11-07"
    assert bom_recs[0].phase == 2
    assert bom_recs[0].active == 1

    romi_sample = "2026 11 7 0 0.43475 0.18442 0.47225\n"
    romi_recs = parse_psl_romi_text(romi_sample)
    assert len(romi_recs) == 1
    assert romi_recs[0].active == 0

    era5_payload = {
        "latitude": 40.03515,
        "longitude": -75.272705,
        "daily": {
            "time": ["2026-01-01", "2026-01-02"],
            "temperature_2m_mean": [-1.8, 0.4],
            "temperature_2m_min": [-5.2, -3.1],
            "temperature_2m_max": [1.6, 3.9],
            "precipitation_sum": [0.0, 4.2],
        },
    }
    era5_res = parse_open_meteo_era5_json("bryn_mawr_pa", 40.02, -75.34, era5_payload)
    assert era5_res.era5_latitude == pytest.approx(40.03515)
    assert len(era5_res.records) == 2
    assert era5_res.records[1].precip_mm == pytest.approx(4.2)


def test_classify_states_and_seasons() -> None:
    assert classify_index_state("AO", -1.7) == "strong_negative"
    assert classify_index_state("NAO", -0.8) == "negative"
    assert classify_index_state("PNA", 0.0) == "neutral"
    assert classify_index_state("PNA", 1.2) == "positive"
    assert classify_index_state("AO", 1.8) == "strong_positive"

    assert classify_index_state("NINO34", -1.6) == "strong_la_nina"
    assert classify_index_state("NINO34", -0.7) == "la_nina"
    assert classify_index_state("NINO34", 0.2) == "neutral"
    assert classify_index_state("NINO34", 0.9) == "el_nino"
    assert classify_index_state("NINO34", 1.9) == "strong_el_nino"

    assert len(ROLLING_SEASONS) == 12
    assert SEASON_FOR_MONTH[11] == "OND"
    assert SEASON_FOR_MONTH[1] == "DJF"
    assert slugify_location_id("Bryn Mawr, PA") == "bryn_mawr_pa"


def test_end_to_end_climatology_and_composites(tmp_path: Path) -> None:
    db_file = tmp_path / "climate.db"
    init_db(db_file)
    conn = get_connection(db_file)

    try:
        upsert_location(
            conn,
            LocationRecord(
                id="test_loc",
                name="Test Location",
                latitude=40.0,
                longitude=-75.0,
                bootstrap_status="in_progress",
            ),
        )

        # Build 3 years of synthetic daily weather and teleconnections
        start_d = date(2023, 1, 1)
        n_days = 365 * 3
        weather_rows = []
        tc_rows = []
        mjo_rows = []

        for i in range(n_days):
            cur_d = start_d + timedelta(days=i)
            d_str = cur_d.isoformat()
            # Seasonal cycle + cold anomaly when AO is strongly negative
            seasonal_base = 12.0 - 10.0 * np.cos(2 * np.pi * cur_d.timetuple().tm_yday / 365.0)
            ao_val = -1.8 if (i // 10) % 3 == 0 else (0.8 if (i // 10) % 3 == 1 else 0.0)
            temp_shift = -3.0 if ao_val <= -1.5 else (1.5 if ao_val >= 0.5 else 0.0)
            tmean = float(seasonal_base + temp_shift)
            precip = 5.0 if ao_val <= -1.5 else 1.0

            weather_rows.append(
                WeatherDailyRecord(
                    location_id="test_loc",
                    observation_date=d_str,
                    tmean_c=tmean,
                    tmin_c=tmean - 4.0,
                    tmax_c=tmean + 4.0,
                    precip_mm=precip,
                )
            )
            tc_rows.append(TeleconnectionObservationRecord("AO", d_str, ao_val))
            tc_rows.append(TeleconnectionObservationRecord("NAO", d_str, ao_val * 0.9))
            tc_rows.append(TeleconnectionObservationRecord("PNA", d_str, -ao_val * 0.8))
            if cur_d.day == 1:
                tc_rows.append(TeleconnectionObservationRecord("NINO34", d_str, -0.8))

            mjo_phase = ((i // 7) % 8) + 1
            mjo_rows.append(
                MJOObservationRecord(
                    observation_date=d_str,
                    rmm1=1.2,
                    rmm2=0.5,
                    phase=mjo_phase,
                    amplitude=1.3,
                    active=1,
                )
            )

        upsert_weather_daily(conn, weather_rows)
        upsert_teleconnection_observations(conn, tc_rows)
        upsert_mjo_observations(conn, mjo_rows)

        enriched_df = calculate_location_climatology(conn, "test_loc", window_days=15)
        assert enriched_df is not None
        assert not enriched_df["tmean_anomaly_c"].isna().any()

        tc_eff = compute_teleconnection_effects(conn, "test_loc", weather_df=enriched_df)
        mjo_eff = compute_mjo_effects(conn, "test_loc", weather_df=enriched_df)

        assert len(tc_eff) > 0
        assert len(mjo_eff) > 0

        # Check AO strong_negative lag 0-5 in DJF has negative mean temperature effect
        row = conn.execute(
            """
            SELECT mean_effect, sample_count, confidence
            FROM teleconnection_effect
            WHERE location_id = 'test_loc'
              AND code = 'AO'
              AND season = 'DJF'
              AND state = 'strong_negative'
              AND lag_start_days = 0
              AND lag_end_days = 5
              AND variable = 'tmean_anomaly_c';
            """
        ).fetchone()
        assert row is not None
        assert row["mean_effect"] < -1.0
        assert row["sample_count"] > 20
    finally:
        conn.close()
