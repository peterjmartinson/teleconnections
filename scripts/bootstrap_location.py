#!/usr/bin/env python3
"""Bootstrap ERA5 daily climatology and teleconnection/MJO effects for a single location."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from teleclim.cli import bootstrap_location_main

if __name__ == "__main__":
    raise SystemExit(bootstrap_location_main())
