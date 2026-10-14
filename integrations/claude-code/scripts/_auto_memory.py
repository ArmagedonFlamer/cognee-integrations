"""Claude Code's built-in auto memory (MEMORY.md): read its state, turn it off.

Auto memory competes with Cognee: its index is loaded into every session, so
facts Cognee recalls are often already known and the agent cannot tell which
memory it learned them from. A plugin cannot switch it off by itself (a
plugin's own settings.json only honours ``agent`` and ``subagentStatusLine``),
so SessionStart asks the user once and, on a yes, ``cognee-auto-memory.py``
writes ``autoMemoryEnabled: false`` into the settings scope the user picked.

Claude Code resolves the switch as follows, and ``auto_memory_status`` mirrors it:

1. ``CLAUDE_CODE_DISABLE_AUTO_MEMORY`` (``1`` off, ``0`` forced on) wins over the
   setting. It may come from the process environment or a settings ``env`` block.
2. ``autoMemoryEnabled`` from the highest settings scope that sets it:
   managed > local (``.claude/settings.local.json``) > project
   (``.claude/settings.json``) > user (``~/.claude/settings.json``).
3. Otherwise auto memory is on.

Stdlib only, and no ``_plugin_common`` import: doctor and the CLI must work
without the plugin venv.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ENV_VAR = "CLAUDE_CODE_DISABLE_AUTO_MEMORY"
SETTING = "autoMemoryEnabled"

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}

#: Offer state: how often SessionStart offered, and what the user chose.
OFFER_FILE = Path.home() / ".cognee-plugin" / "claude-code" / "auto-memory-offer.json"
#: Sessions the offer is injected into before we stop, when no decision came back
#: (the agent may not have asked, or the user ignored it).
MAX_OFFERS = 3


def _managed_settings_path() -> Path:
    if sys.platform == "darwin":
        return Path("/Library/Application Support/ClaudeCode/managed-settings.json")
    if os.name == "nt":
        return Path(r"C:\Program Files\ClaudeCode\managed-settings.json")
    return Path("/etc/claude-code/managed-settings.json")


#: Indirection so tests can point "managed" at a temp file.
MANAGED_SETTINGS = _managed_settings_path()


def user_config_dir() -> Path:
    override = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    return Path(override).expanduser() if override else Path.home() / ".claude"


def project_dir(explicit: str | None = None) -> Path:
    raw = explicit or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    return Path(raw).expanduser().resolve()


def settings_path(scope: str, project: str | None = None) -> Path:
    """Where ``scope`` (``user`` or ``project``) keeps the setting we write.

    ``project`` means the personal per-project file, ``settings.local.json``:
    the choice belongs to this user, not to everyone who clones the repo.
    """
    if scope == "user":
        return user_config_dir() / "settings.json"
    if scope == "project":
        return project_dir(project) / ".claude" / "settings.local.json"
    raise ValueError(f"unknown scope {scope!r}; expected 'user' or 'project'")


def _scopes(project: str | None) -> list[tuple[str, Path]]:
    """Settings files, highest precedence first."""
    root = project_dir(project)
    return [
        ("managed", MANAGED_SETTINGS),
        ("local", root / ".claude" / "settings.local.json"),
        ("project", root / ".claude" / "settings.json"),
        ("user", user_config_dir() / "settings.json"),
    ]


def _read_settings(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _flag(value) -> bool | None:
    """``True``/``False`` for a recognisable boolean, ``None`` otherwise."""
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    return None


def auto_memory_status(project: str | None = None) -> dict:
    """Whether auto memory is on, and what decided it.

    Returns ``{"enabled": bool, "source": str, "scope": str}``. ``scope`` is
    ``env``, ``managed``, ``local``, ``project``, ``user`` or ``default``;
    ``source`` names the variable or file for display.
    """
    disable = _flag(os.environ.get(ENV_VAR, ""))
    if disable is not None:
        return {"enabled": not disable, "source": f"{ENV_VAR}={int(disable)}", "scope": "env"}

    scopes = [(scope, path, _read_settings(path)) for scope, path in _scopes(project)]
    for scope, path, settings in scopes:
        env_block = settings.get("env")
        if isinstance(env_block, dict):
            disable = _flag(env_block.get(ENV_VAR, ""))
            if disable is not None:
                return {
                    "enabled": not disable,
                    "source": f"{ENV_VAR}={int(disable)} in {path}",
                    "scope": scope,
                }
    for scope, path, settings in scopes:
        enabled = _flag(settings.get(SETTING, ""))
        if enabled is not None:
            return {"enabled": enabled, "source": f"{SETTING} in {path}", "scope": scope}
    return {"enabled": True, "source": "default", "scope": "default"}


def write_setting(scope: str, enabled: bool | None, project: str | None = None) -> Path:
    """Set (or, with ``enabled=None``, remove) ``autoMemoryEnabled`` in ``scope``.

    Keeps every other key. Refuses to touch a file that is not a JSON object,
    so a hand-edited settings file is never clobbered.
    """
    path = settings_path(scope, project)
    settings: dict = {}
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            settings = json.loads(text)
            if not isinstance(settings, dict):
                raise ValueError(f"{path} is not a JSON object")
    if enabled is None:
        if SETTING not in settings:
            return path
        settings.pop(SETTING)
    else:
        settings[SETTING] = enabled
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".settings-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def read_offer() -> dict:
    return _read_settings(OFFER_FILE)


def record_offer(**fields) -> None:
    """Merge ``fields`` into the offer state (best effort)."""
    state = read_offer()
    state.update(fields)
    try:
        OFFER_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = OFFER_FILE.with_name(f"{OFFER_FILE.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, OFFER_FILE)
    except OSError:
        pass


def count_offer() -> None:
    """Record that SessionStart injected the offer once more."""
    state = read_offer()
    try:
        count = int(state.get("offer_count", 0) or 0)
    except (TypeError, ValueError):
        count = 0
    record_offer(offer_count=count + 1, last_offered_at=now())


def should_offer(project: str | None = None) -> bool:
    """Ask until the user decides (at most ``MAX_OFFERS`` sessions), and only
    while auto memory is on by default.

    Only a decision stops it for good: the offer is injected before anyone
    has answered, so being offered proves nothing. Auto memory switched on
    explicitly (the env var, or ``autoMemoryEnabled: true`` in any settings
    file) is a choice someone already made, so we leave it alone; doctor still
    reports it.
    """
    offer = read_offer()
    if offer.get("decision"):
        return False
    try:
        if int(offer.get("offer_count", 0) or 0) >= MAX_OFFERS:
            return False
    except (TypeError, ValueError):
        pass
    status = auto_memory_status(project)
    return status["enabled"] and status["scope"] == "default"


def now() -> int:
    return int(time.time())
