from __future__ import annotations

import sys
from pathlib import Path

CORE_ROOT = Path(__file__).resolve().parents[4]
# Test the checkout, not whatever editable install is in the environment.
sys.path.insert(0, str(CORE_ROOT / "src"))
sys.path.insert(0, str(CORE_ROOT))
