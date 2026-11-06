#!/usr/bin/env python3
"""Entrada del CLI de perfiles de estáticos de SPanel.

    python3 scripts/spanel-profiles.py up
    python3 scripts/spanel-profiles.py verify
    python3 scripts/spanel-profiles.py down
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from spanel_profiles.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
