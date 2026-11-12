#!/usr/bin/env python3
"""Thin operator entrypoint for the Domains DNS capability."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "vendor" / "systutor-core"
sys.path.insert(0, str(CORE / "src"))
sys.path.insert(0, str(CORE))

from plugins.domains.backend.dns.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
