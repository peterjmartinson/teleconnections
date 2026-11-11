"""Command-line entrypoints for Teleconnection Climatology Builder."""
from __future__ import annotations

import argparse
import logging
from typing import List, Optional

from .db import init_db
from .services import bootstrap_global_indices, bootstrap_location, slugify_location_id


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def init_db_main(argv: Optional[List[str]] = None) -> int:
    _configure_logging()
    parser = argparse.ArgumentParser(
        description="Initialize the Teleconnection Climatology SQLite schema and seed metadata."
    )
    parser.add_argument("--db", default=None, help="Override path to climate.db")
    args = parser.parse_args(argv)

    path = init_db(db_path=args.db, seed=True)
    print(f"Initialized teleconnection climatology database at: {path}")
    return 0


def bootstrap_indices_main(argv: Optional[List[str]] = None) -> int:
    _configure_logging()
    parser = argparse.ArgumentParser(
        description="Download and upsert global teleconnection indices (AO, NAO, PNA, NINO34, MJO)."
    )
    parser.add_argument("--db", default=None, help="Override path to climate.db")
    parser.add_argument("--start", default=None, help="Optional start date (YYYY-MM-DD)")
    parser.add_argument("--end", default=None, help="Optional end date (YYYY-MM-DD)")
    args = parser.parse_args(argv)

    counts = bootstrap_global_indices(
        db_path=args.db,
        start_date=args.start,
        end_date=args.end,
    )
    print(f"Bootstrapped global indices: {counts}")
    return 0


def bootstrap_location_main(argv: Optional[List[str]] = None) -> int:
    _configure_logging()
    parser = argparse.ArgumentParser(
        description="Bootstrap historical ERA5 weather and teleconnection/MJO effects for a location."
    )
    parser.add_argument("--id", dest="location_id", default="", help="Location slug ID (e.g. bryn_mawr_pa)")
    parser.add_argument("--name", required=True, help="Display name (e.g. 'Bryn Mawr, PA')")
    parser.add_argument("--lat", type=float, required=True, help="Latitude in decimal degrees")
    parser.add_argument("--lon", type=float, required=True, help="Longitude in decimal degrees")
    parser.add_argument("--start", default=None, help="Override ERA5 start date (YYYY-MM-DD)")
    parser.add_argument("--end", default=None, help="Override ERA5 end date (YYYY-MM-DD)")
    parser.add_argument("--db", default=None, help="Override path to climate.db")
    parser.add_argument("--force", action="store_true", help="Rebuild even if bootstrap_status is complete")
    args = parser.parse_args(argv)

    loc_id = args.location_id.strip() or slugify_location_id(args.name)
    res = bootstrap_location(
        location_id=loc_id,
        name=args.name,
        latitude=args.lat,
        longitude=args.lon,
        start=args.start,
        end=args.end,
        db_path=args.db,
        force=args.force,
    )
    print(f"Location bootstrap result: {res}")
    return 0
