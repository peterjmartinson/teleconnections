#!/usr/bin/env python3
"""Initialize the v1 teleconnection climatology SQLite database and seed metadata."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from teleclim.cli import init_db_main

if __name__ == "__main__":
    raise SystemExit(init_db_main())
