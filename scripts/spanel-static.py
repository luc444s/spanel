#!/usr/bin/env python3
"""Entrada del CLI de sitios estáticos de SPanel.

La implementación vive en `scripts/spanel_static/`. Este archivo solo ajusta el
`sys.path` para poder ejecutarlo desde cualquier directorio:

    python3 scripts/spanel-static.py create --name paginadelucas
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from spanel_static.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
