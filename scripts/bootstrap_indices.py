#!/usr/bin/env python3
"""Download and upsert global teleconnection indices (AO, NAO, PNA, NINO34, MJO)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from teleclim.cli import bootstrap_indices_main

if __name__ == "__main__":
    raise SystemExit(bootstrap_indices_main())
