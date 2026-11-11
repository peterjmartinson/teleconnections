"""NOAA CPC daily teleconnection index ingestion (AO, NAO, PNA)."""
from __future__ import annotations

import csv
import io
import logging
import math
from pathlib import Path
from typing import Dict, List, Optional

import requests

from ..config import TeleclimConfig, load_config
from ..models import TeleconnectionObservationRecord

logger = logging.getLogger(__name__)


def parse_cpc_cdas_csv(
    code: str,
    csv_text: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[TeleconnectionObservationRecord]:
    """Parse a NOAA CPC daily CDAS CSV file into TeleconnectionObservationRecord rows."""
    code_upper = code.upper().strip()
    records: List[TeleconnectionObservationRecord] = []

    reader = csv.DictReader(io.StringIO(csv_text.strip()))
    for row in reader:
        try:
            y = int(row["year"])
            m = int(row["month"])
            d = int(row["day"])
            obs_date = f"{y:04d}-{m:02d}-{d:02d}"
            if start_date and obs_date < start_date:
                continue
            if end_date and obs_date > end_date:
                continue

            val_str: Optional[str] = None
            for k, v in row.items():
                if k not in ("year", "month", "day") and v is not None:
                    val_str = v.strip()
                    break
            if not val_str or val_str in ("", "NaN", "-999", "-99.9", "-999.0"):
                continue

            val = float(val_str)
            if math.isnan(val) or abs(val) > 50.0:
                continue

            records.append(
                TeleconnectionObservationRecord(
                    code=code_upper,
                    observation_date=obs_date,
                    value=val,
                )
            )
        except (KeyError, ValueError, TypeError):
            continue

    records.sort(key=lambda r: r.observation_date)
    return records


def download_cpc_index(
    code: str,
    url: str,
    raw_cache_dir: Optional[Path] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    timeout: int = 30,
) -> List[TeleconnectionObservationRecord]:
    """Download and parse a single NOAA CPC daily CDAS index (AO, NAO, or PNA)."""
    code_upper = code.upper().strip()
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    raw_text = resp.text

    if raw_cache_dir is not None:
        raw_cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = raw_cache_dir / f"{code_upper.lower()}_cdas.csv"
        cache_file.write_text(raw_text, encoding="utf-8")

    return parse_cpc_cdas_csv(
        code=code_upper,
        csv_text=raw_text,
        start_date=start_date,
        end_date=end_date,
    )


def download_cpc_indices(
    config: Optional[TeleclimConfig] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, List[TeleconnectionObservationRecord]]:
    """Download AO, NAO, and PNA daily histories from NOAA CPC."""
    cfg = config or load_config()
    raw_dir = cfg.resolve_raw_indices_dir()
    urls = {
        "AO": cfg.indices.ao_url,
        "NAO": cfg.indices.nao_url,
        "PNA": cfg.indices.pna_url,
    }
    results: Dict[str, List[TeleconnectionObservationRecord]] = {}
    for code, url in urls.items():
        logger.info("Downloading NOAA CPC %s from %s", code, url)
        results[code] = download_cpc_index(
            code=code,
            url=url,
            raw_cache_dir=raw_dir,
            start_date=start_date,
            end_date=end_date,
        )
        logger.info("Parsed %d daily observations for %s", len(results[code]), code)
    return results
