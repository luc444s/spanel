"""A.SPEC 0005: a mistyped permission name must fail here, not as a silent 403.

`require_permission("core.user.read")` (missing the s) compiles fine, passes
ruff, and passes every test. The endpoint it guards then returns 403 forever
and nothing tells you why. This test is the thing that tells you.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from systutor.api.seed import BASE_PERMISSIONS

ROOT = Path(__file__).resolve().parents[1]
GUARD_FUNCS = {"require_permission", "require_any_permission"}


def _scan_permissions() -> dict[str, list[str]]:
    """Collect every literal permission passed to a require_* guard."""
    found: dict[str, list[str]] = {}
    targets = [ROOT / "src", ROOT / "plugins"]
    for base in targets:
        for path in base.rglob("*.py"):
            if "__pycache__" in path.parts or "tests" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                # require_permission("x") is a Name; kernel.module.require_permission is
                # an Attribute. Both shapes exist in this codebase.
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                else:
                    continue
                if name not in GUARD_FUNCS:
                    continue
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        rel = f"{path.relative_to(ROOT)}:{node.lineno}"
                        found.setdefault(arg.value, []).append(rel)
    return found


def _plugin_permissions() -> set[str]:
    declared: set[str] = set()
    for manifest in (ROOT / "plugins").glob("*/permissions/*.json"):
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        declared.update(p["name"] for p in payload.get("permissions", []))
    return declared


CATALOG = set(BASE_PERMISSIONS) | _plugin_permissions()
USED = _scan_permissions()


def test_scan_actually_found_something():
    """Guard against the scanner silently matching nothing, which would make
    every other test in this file pass vacuously."""
    assert len(USED) > 10, f"scanner looks broken, only found {sorted(USED)}"


@pytest.mark.parametrize("permission", sorted(USED))
def test_permission_is_in_catalog(permission: str):
    assert permission in CATALOG, (
        f"'{permission}' is used at {USED[permission]} but is not in "
        f"BASE_PERMISSIONS nor any plugin manifest. "
        f"Add it to systutor/api/seed.py or the owning plugin's permissions/*.json, "
        f"or fix the typo. Until then the guard returns 403 for everyone."
    )


# Declared permissions that deliberately have no require_* guard. Each needs a
# reason, otherwise this file fails the next time someone adds a permission and
# wires the endpoint without the guard.
ALLOWED_UNGUARDED = {
    "core.auth.me",  # GET /auth/me is intentionally unguarded: reading your own
    # profile must not require a permission.
    "core.event.read",  # Event surfaces are not exposed as guarded endpoints yet.
    "core.role.manage",  # Legacy alias of core.roles.manage, kept for compatibility.
    "core.user.manage",  # Legacy alias of core.users.manage, kept for compatibility.
}


def test_base_permissions_are_all_used():
    """The other direction: a declared permission that nothing guards is dead
    weight, and usually means someone wired the endpoint and forgot the guard."""
    unused = sorted(set(BASE_PERMISSIONS) - set(USED) - ALLOWED_UNGUARDED)
    assert not unused, f"declared but never used in any require_* guard: {unused}"
