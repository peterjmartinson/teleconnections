"""Global teleconnection and MJO ingestion service."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional

from ..config import TeleclimConfig, load_config
from ..db import (
    get_connection,
    init_db,
    seed_teleconnections,
    upsert_mjo_observations,
    upsert_teleconnection_observations,
)
from ..ingest import download_cpc_indices, download_mjo, download_nino34

logger = logging.getLogger(__name__)


def bootstrap_global_indices(
    db_path: Optional[Path | str] = None,
    config: Optional[TeleclimConfig] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, int]:
    """Download and upsert AO, NAO, PNA, NINO34, and MJO histories into SQLite.

    Idempotent: running again updates existing rows and inserts new dates.
    """
    cfg = config or load_config()
    resolved_db = init_db(db_path=db_path, seed=True)
    counts: Dict[str, int] = {}

    cpc_map = download_cpc_indices(config=cfg, start_date=start_date, end_date=end_date)
    nino_records = download_nino34(config=cfg, start_date=start_date, end_date=end_date)
    mjo_records = download_mjo(config=cfg, start_date=start_date, end_date=end_date)

    conn = get_connection(resolved_db)
    try:
        seed_teleconnections(conn)
        for code, recs in cpc_map.items():
            counts[code] = upsert_teleconnection_observations(conn, recs)
        counts["NINO34"] = upsert_teleconnection_observations(conn, nino_records)
        counts["MJO"] = upsert_mjo_observations(conn, mjo_records)
    finally:
        conn.close()

    logger.info("Global index bootstrap complete in %s: %s", resolved_db, counts)
    return counts
