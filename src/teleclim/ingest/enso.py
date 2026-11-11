"""NOAA CPC / PSL monthly Niño 3.4 index ingestion."""
from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import List, Optional

import requests

from ..config import TeleclimConfig, load_config
from ..models import TeleconnectionObservationRecord

logger = logging.getLogger(__name__)


def parse_nino34_ascii(
    raw_text: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[TeleconnectionObservationRecord]:
    """Parse NOAA CPC detrend.nino34.ascii.txt into monthly TeleconnectionObservationRecord rows.

    Each monthly record is stored with ``observation_date = 'YYYY-MM-01'`` and ``code = 'NINO34'``,
    where ``value`` is the ERSSTv5 1991–2020 centered-base-period SST anomaly (°C).
    """
    records: List[TeleconnectionObservationRecord] = []
    for line in raw_text.strip().splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() == "YR":
            continue
        try:
            yr = int(parts[0])
            mon = int(parts[1])
            anom = float(parts[4])
            if math.isnan(anom) or anom <= -90.0 or abs(anom) > 20.0:
                continue
            obs_date = f"{yr:04d}-{mon:02d}-01"
            if start_date and obs_date < start_date:
                continue
            if end_date and obs_date > end_date:
                continue
            records.append(
                TeleconnectionObservationRecord(
                    code="NINO34",
                    observation_date=obs_date,
                    value=anom,
                )
            )
        except (ValueError, IndexError):
            continue

    records.sort(key=lambda r: r.observation_date)
    return records


def download_nino34(
    config: Optional[TeleclimConfig] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    timeout: int = 30,
) -> List[TeleconnectionObservationRecord]:
    """Download and parse monthly Niño 3.4 anomalies from NOAA CPC."""
    cfg = config or load_config()
    url = cfg.indices.nino34_url
    raw_dir = cfg.resolve_raw_indices_dir()

    logger.info("Downloading Niño 3.4 monthly history from %s", url)
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    raw_text = resp.text

    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "nino34_cpc.txt").write_text(raw_text, encoding="utf-8")

    records = parse_nino34_ascii(raw_text, start_date=start_date, end_date=end_date)
    logger.info("Parsed %d monthly observations for NINO34", len(records))
    return records
