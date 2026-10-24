"""Read the database constants from a site's `wp-config.php` as data.

The hosting plugin needs the same four values WordPress uses, and a site may
declare them in two places:

1. the container environment (`WORDPRESS_DB_*`), which `service.py` reads
   first;
2. `wp-config.php` inside the site volume (`DB_*`), which this module covers.

Nothing here executes PHP. The file is read as text and a small, deliberately
literal parser extracts `define('DB_...', '...')`. A value built with a
variable, a function or an include is not guessed: it is treated as absent.

The password is only returned in memory. It is never written to a log, an
exception message or a command line.
"""

from __future__ import annotations

import re
import shlex

from .transport import build_docker_argv, run

WP_CONFIG_PATH = "/var/www/html/wp-config.php"

# The name inside `define()` and the value quote are captured so the correct
# backreference is used. Escaped characters are consumed before the closing
# quote, which keeps a value like 'pa\'ss' in one piece.
_DEFINE_SINGLE = re.compile(
    r"""define\s*\(\s*(['"])DB_(HOST|NAME|USER|PASSWORD)\1\s*,\s*'((?:\\.|[^'\\])*)'""",
    re.DOTALL,
)
_DEFINE_DOUBLE = re.compile(
    r'''define\s*\(\s*(['"])DB_(HOST|NAME|USER|PASSWORD)\1\s*,\s*"((?:\\.|[^"\\])*)"''',
    re.DOTALL,
)


def _strip_php_comments(text: str) -> str:
    """Blank PHP comments while keeping quoted strings untouched.

    A password can contain `#` or `//`; removing comments without knowing where
    a string starts would truncate that value. This scanner tracks single and
    double quoted strings and only treats comment markers outside them as
    comments. Positions are preserved so the first `define()` still wins.
    """
    result: list[str] = []
    index = 0
    length = len(text)
    state: str | None = None

    while index < length:
        char = text[index]
        following = text[index + 1] if index + 1 < length else ""

        if state is None:
            if char in {"'", '"'}:
                state = char
                result.append(char)
                index += 1
            elif char == "/" and following == "/":
                state = "line"
                result.extend((" ", " "))
                index += 2
            elif char == "#":
                state = "line"
                result.append(" ")
                index += 1
            elif char == "/" and following == "*":
                state = "block"
                result.extend((" ", " "))
                index += 2
            else:
                result.append(char)
                index += 1
            continue

        if state == "line":
            if char == "\n":
                state = None
            result.append("\n" if char == "\n" else " ")
            index += 1
            continue

        if state == "block":
            if char == "*" and following == "/":
                state = None
                result.extend((" ", " "))
                index += 2
            else:
                result.append("\n" if char == "\n" else " ")
                index += 1
            continue

        # Inside a quoted string: the opening quote consumed above, escapes
        # belong to the value and must not end the string.
        result.append(char)
        if char == "\\" and index + 1 < length:
            result.append(text[index + 1])
            index += 2
            continue
        if char == state:
            state = None
        index += 1

    return "".join(result)


def _php_string(raw: str, quote: str) -> str:
    """Turn a PHP string literal body into its value, without running PHP."""
    result: list[str] = []
    index = 0
    length = len(raw)

    while index < length:
        char = raw[index]
        following = raw[index + 1] if index + 1 < length else ""
        if char != "\\" or index + 1 >= length:
            result.append(char)
            index += 1
            continue

        if quote == "'":
            if following in {"\\", "'"}:
                result.append(following)
                index += 2
            else:
                result.append(char)
                index += 1
            continue

        escapes = {
            "n": "\n",
            "r": "\r",
            "t": "\t",
            "v": "\v",
            "e": "\x1b",
            "f": "\f",
            "\\": "\\",
            '"': '"',
            "$": "$",
        }
        if following in escapes:
            result.append(escapes[following])
            index += 2
        else:
            result.append(char)
            index += 1

    return "".join(result)


def parse_wp_config(text: str) -> dict[str, str]:
    """The literal `DB_*` constants, or the subset the file declares.

    The first definition of a constant wins, the same way `define()` behaves in
    PHP. Both quote styles are accepted, and comments are ignored.
    """
    clean = _strip_php_comments(text)
    matches: list[tuple[int, str, str, str]] = []
    for pattern in (_DEFINE_SINGLE, _DEFINE_DOUBLE):
        for match in pattern.finditer(clean):
            matches.append((match.start(), match.group(2), match.group(3), match.group(1)))

    values: dict[str, str] = {}
    for _position, key, raw, quote in sorted(matches, key=lambda item: item[0]):
        values.setdefault(f"DB_{key}", _php_string(raw, quote))
    return values


def credentials_from_wp_config(text: str) -> tuple[str, str, str, str] | None:
    """`(host, database, user, password)`, or `None` if any is missing.

    The password is not stripped: it is exactly the literal WordPress uses.
    The other three are trimmed because whitespace around a container name or
    database name is never meaningful.
    """
    values = parse_wp_config(text)
    host = values.get("DB_HOST", "").strip()
    database = values.get("DB_NAME", "").strip()
    user = values.get("DB_USER", "").strip()
    password = values.get("DB_PASSWORD", "")
    if not all((host, database, user, password)):
        return None
    return host, database, user, password


def read_wp_config(
    env: dict[str, str],
    container_name: str,
    *,
    timeout: int = 60,
) -> str | None:
    """Read `wp-config.php` from the site container, or `None` if unreadable.

    It is a single `cat`, not PHP: no code from the file is executed. A failure
    returns `None` on purpose, so the caller can keep the original "database
    not declared" error instead of turning a missing file into a leak or a
    traceback. The content never enters the error path.
    """
    command = f"docker exec {shlex.quote(container_name)} cat {WP_CONFIG_PATH}"
    result = run(build_docker_argv(command, env=env), env=env, timeout=timeout)
    if result.returncode != 0:
        return None
    return result.stdout


__all__ = [
    "WP_CONFIG_PATH",
    "credentials_from_wp_config",
    "parse_wp_config",
    "read_wp_config",
]
