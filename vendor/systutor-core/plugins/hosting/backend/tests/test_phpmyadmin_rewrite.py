from __future__ import annotations

import pytest

from plugins.hosting.backend.phpmyadmin_proxy import (
    filtered_headers,
    merge_cookies,
    remove_frame_breakout,
    rewrite,
    rewrite_location,
    validate_url,
)
from systutor.core.errors import AppError

BASE = "/api/v1/plugins/hosting/sites/site-a/phpmyadmin"


@pytest.mark.parametrize(
    "url",
    ["", "file:///etc/passwd", "http://u:p@host", "http://", "http://[broken", "http://host:bad"],
)
def test_reject_invalid_upstream(url: str) -> None:
    with pytest.raises(AppError) as exc:
        validate_url(url)
    assert exc.value.status_code == 409


def test_merge_set_replace_and_delete() -> None:
    result = merge_cookies(
        {"keep": "old", "gone": "old"},
        [
            ("Set-Cookie", "keep=new; HttpOnly"),
            ("Set-Cookie", "gone=deleted; Max-Age=0"),
            ("Set-Cookie", "extra=yes"),
        ],
    )
    assert result == {"keep": "new", "extra": "yes"}


def test_remove_phpmyadmin_frame_breakout_without_touching_other_scripts() -> None:
    html = (
        '<style id="cfs-style">html{display: none;}</style>'
        '<script src="js/dist/cross_framing_protection.js?v=5.2.3"></script>'
        '<script src="js/dist/functions.js?v=5.2.3"></script><main>dashboard</main>'
    )
    safe = remove_frame_breakout(html)
    assert "cfs-style" not in safe
    assert "cross_framing_protection.js" not in safe
    assert "functions.js" in safe
    assert "dashboard" in safe


def test_rewrites_origins_root_paths_and_headers() -> None:
    html = '<a href="http://spanel-phpmyadmin:8080/index.php"><img src="/themes/a.css"></a>'
    rewritten = rewrite(html, "http://spanel-phpmyadmin:8080", "http://127.0.0.1:8081", BASE)
    assert f"{BASE}/index.php" in rewritten
    assert f"{BASE}/themes/a.css" in rewritten
    assert (
        rewrite('href="index.php?route=/server/status"', "http://spanel-phpmyadmin:8080", "", BASE)
        == 'href="index.php?route=/server/status"'
    )
    assert (
        rewrite_location("index.php?a=1", "http://spanel-phpmyadmin:8080", "", BASE)
        == "index.php?a=1"
    )
    assert (
        rewrite_location("/index.php?a=1", "http://spanel-phpmyadmin:8080", "", BASE)
        == BASE + "/index.php?a=1"
    )
    assert (
        rewrite_location("https://outside.example/a", "http://spanel-phpmyadmin:8080", "", BASE)
        == "https://outside.example/a"
    )
    headers = filtered_headers(
        [
            ("Set-Cookie", "secret=yes"),
            ("X-Frame-Options", "DENY"),
            ("Connection", "keep-alive, X-Hop"),
            ("X-Hop", "bad"),
            ("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'"),
            ("Location", "http://127.0.0.1:8081/index.php"),
        ],
        "http://spanel-phpmyadmin:8080",
        "",
        BASE,
    )
    assert "Set-Cookie" not in headers and "X-Frame-Options" not in headers
    assert "X-Hop" not in headers
    assert headers["Location"] == BASE + "/index.php"
    assert headers["Content-Security-Policy"] == "default-src 'self'; frame-ancestors 'self'"
