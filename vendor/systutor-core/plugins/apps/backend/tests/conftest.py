from __future__ import annotations

import sys
from pathlib import Path

# The repository-level verification runs from the host root rather than from
# vendor/systutor-core. Include the vendored plugin namespace without requiring
# an editable reinstall of the virtual environment.
CORE_ROOT = Path(__file__).resolve().parents[4]
if str(CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(CORE_ROOT))
