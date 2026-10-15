"""Server settings from ``~/.cognee/.env``, the file every cognee plugin reads.

cognee >= 1.6.2 loads the first ``.env`` it finds walking up from its working
directory and lets it override the process environment. A server started in
whatever directory Hermes was launched from could therefore have its default
user, data roots or auth settings replaced by an unrelated project's ``.env``.
``server_bootstrap`` now starts the server in the plugin's own state directory
and hands it the values of ``~/.cognee/.env`` (or ``COGNEE_ENV_FILE``) instead,
the way the claude-code, codex and openclaw plugins do:

  real exported env vars  >  ~/.cognee/.env  >  plugin defaults

Only the spawned server's environment is touched, never this process's. Names
that could hijack the child (``PATH``, ``HOME``, ``LD_*``, ...) are skipped.
Parsing never raises: a file that cannot be read is ignored.
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from pathlib import Path

from .config import SHARED_COGNEE_HOME

_DENYLIST_EXACT = {"PATH", "HOME", "PYTHONPATH", "PYTHONHOME", "SHELL", "USER"}
_DENYLIST_PREFIXES = ("LD_", "DYLD_")


def env_file_path() -> Path:
    """The env file location (``COGNEE_ENV_FILE`` overrides the default)."""
    override = os.environ.get("COGNEE_ENV_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    return SHARED_COGNEE_HOME / ".env"


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def blocked(key: str) -> bool:
    return key in _DENYLIST_EXACT or key.startswith(_DENYLIST_PREFIXES)


def parse_env_file(path: Path) -> dict[str, str]:
    """``KEY=VALUE`` lines, ``#`` comments, an optional ``export `` and quotes.

    No interpolation and no multi-line values; malformed lines are skipped.
    """
    result: dict[str, str] = {}
    try:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-16")
    except Exception:
        return result
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or not all(c.isalnum() or c == "_" for c in key):
            continue
        result[key] = _unquote(value)
    return result


def apply_env_file(env: MutableMapping[str, str], path: Path | None = None) -> list[str]:
    """Add the env file's values to ``env`` where it has none. Returns the keys added."""
    path = path or env_file_path()
    if not path.is_file():
        return []
    added = []
    for key, value in parse_env_file(path).items():
        if blocked(key) or key in env:
            continue
        env[key] = value
        added.append(key)
    return added
