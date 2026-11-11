"""Australian BOM daily MJO (Wheeler-Hendon RMM1/RMM2) ingestion with NOAA PSL ROMI fallback."""
from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import List, Optional

import requests

from ..config import TeleclimConfig, load_config
from ..models import MJOObservationRecord

logger = logging.getLogger(__name__)

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/plain,text/html,*/*;q=0.8",
}


def compute_wh_phase(rmm1: float, rmm2: float) -> int:
    """Compute Wheeler-Hendon MJO phase (1..8) from (RMM1, RMM2)."""
    deg = math.degrees(math.atan2(rmm2, rmm1))
    if deg < 0:
        deg += 360.0
    octant = int(math.floor(deg / 45.0)) % 8
    phase_map = [5, 6, 7, 8, 1, 2, 3, 4]
    return phase_map[octant]


def parse_bom_rmm_text(
    raw_text: str,
    active_threshold: float = 1.0,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[MJOObservationRecord]:
    """Parse Australian BOM rmm.74toRealtime.txt into MJOObservationRecord rows."""
    records: List[MJOObservationRecord] = []
    for line in raw_text.strip().splitlines():
        stripped = line.strip()
        if not stripped or stripped[0].isalpha():
            continue
        parts = stripped.replace(",", " ").split()
        if len(parts) < 7:
            continue
        try:
            yr = int(parts[0])
            mon = int(parts[1])
            day = int(parts[2])
            rmm1 = float(parts[3])
            rmm2 = float(parts[4])
            phase = int(float(parts[5]))
            amp = float(parts[6])

            # Filter out BOM missing value sentinels (999, 1.E36, etc.)
            if (
                abs(rmm1) >= 90.0
                or abs(rmm2) >= 90.0
                or amp >= 90.0
                or amp < 0.0
                or phase < 1
                or phase > 8
            ):
                continue

            obs_date = f"{yr:04d}-{mon:02d}-{day:02d}"
            if start_date and obs_date < start_date:
                continue
            if end_date and obs_date > end_date:
                continue

            active = 1 if amp >= active_threshold else 0
            records.append(
                MJOObservationRecord(
                    observation_date=obs_date,
                    rmm1=rmm1,
                    rmm2=rmm2,
                    phase=phase,
                    amplitude=amp,
                    active=active,
                )
            )
        except (ValueError, IndexError):
            continue

    records.sort(key=lambda r: r.observation_date)
    return records


def parse_psl_romi_text(
    raw_text: str,
    active_threshold: float = 1.0,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[MJOObservationRecord]:
    """Parse NOAA PSL ROMI (romi.cpcolr.1x.txt) into Wheeler-Hendon RMM-equivalent records."""
    records: List[MJOObservationRecord] = []
    for line in raw_text.strip().splitlines():
        parts = line.split()
        if len(parts) < 6:
            continue
        try:
            yr = int(parts[0])
            mon = int(parts[1])
            day = int(parts[2])
            if len(parts) >= 7:
                pc1 = float(parts[4])
                pc2 = float(parts[5])
                amp = float(parts[6])
            else:
                pc1 = float(parts[3])
                pc2 = float(parts[4])
                amp = float(parts[5])

            if pc1 <= -90.0 or pc2 <= -90.0 or amp < 0.0:
                continue

            obs_date = f"{yr:04d}-{mon:02d}-{day:02d}"
            if start_date and obs_date < start_date:
                continue
            if end_date and obs_date > end_date:
                continue

            # PSL ROMI convention: RMM1-equiv = PC2, RMM2-equiv = -PC1
            rmm1 = pc2
            rmm2 = -pc1
            calc_amp = math.hypot(rmm1, rmm2)
            if abs(amp - calc_amp) > 0.05:
                amp = calc_amp
            phase = compute_wh_phase(rmm1, rmm2)
            active = 1 if amp >= active_threshold else 0

            records.append(
                MJOObservationRecord(
                    observation_date=obs_date,
                    rmm1=rmm1,
                    rmm2=rmm2,
                    phase=phase,
                    amplitude=amp,
                    active=active,
                )
            )
        except (ValueError, IndexError):
            continue

    records.sort(key=lambda r: r.observation_date)
    return records


def download_mjo(
    config: Optional[TeleclimConfig] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    timeout: int = 30,
) -> List[MJOObservationRecord]:
    """Download daily MJO RMM observations from Australian BOM (with NOAA PSL fallback)."""
    cfg = config or load_config()
    raw_dir = cfg.resolve_raw_indices_dir()
    raw_dir.mkdir(parents=True, exist_ok=True)
    threshold = cfg.analysis.mjo_active_amplitude

    bom_url = cfg.indices.mjo_bom_url
    try:
        logger.info("Downloading Australian BOM MJO RMM from %s", bom_url)
        resp = requests.get(bom_url, headers=_BROWSER_HEADERS, timeout=timeout)
        resp.raise_for_status()
        raw_text = resp.text
        (raw_dir / "mjo_bom_rmm.txt").write_text(raw_text, encoding="utf-8")
        records = parse_bom_rmm_text(
            raw_text,
            active_threshold=threshold,
            start_date=start_date,
            end_date=end_date,
        )
        if records:
            logger.info("Parsed %d daily BOM MJO RMM observations", len(records))
            return records
    except Exception as exc:
        logger.warning("BOM MJO download failed (%s), falling back to NOAA PSL ROMI", exc)

    psl_url = cfg.indices.mjo_psl_fallback_url
    logger.info("Downloading fallback NOAA PSL ROMI from %s", psl_url)
    resp = requests.get(psl_url, timeout=timeout)
    resp.raise_for_status()
    raw_text = resp.text
    (raw_dir / "mjo_psl_romi.txt").write_text(raw_text, encoding="utf-8")
    records = parse_psl_romi_text(
        raw_text,
        active_threshold=threshold,
        start_date=start_date,
        end_date=end_date,
    )
    logger.info("Parsed %d daily PSL ROMI observations", len(records))
    return records
