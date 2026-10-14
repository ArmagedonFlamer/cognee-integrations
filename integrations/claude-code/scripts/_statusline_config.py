"""Local status-line ownership and optional user-requested pinning."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from _file_lock import file_lock

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _flag(settings: dict, name: str) -> str:
    # Read live settings first: turning a pin off must stop existing watchers too.
    env = settings.get("env")
    if isinstance(env, dict) and name in env:
        return str(env[name]).strip().lower()
    return os.environ.get(name, "").strip().lower()


def _script(pinned: bool) -> Path:
    fallback = Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or Path(__file__).resolve().parent.parent)
    if pinned:
        # An old session must not restore its old cache path after an update.
        try:
            registry = json.loads(
                (Path.home() / ".claude/plugins/installed_plugins.json").read_text(encoding="utf-8")
            )
            for key, entries in registry.get("plugins", {}).items():
                if not key.startswith("cognee-memory@") or not isinstance(entries, list):
                    continue
                for entry in entries:
                    if isinstance(entry, dict) and entry.get("scope") == "user":
                        candidate = Path(entry["installPath"]) / "scripts/cognee-statusline.sh"
                        if candidate.is_file():
                            return candidate
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            pass
    return fallback / "scripts/cognee-statusline.sh"


def _shell_quote(path: Path) -> str:
    # Double quotes retain the established command format, escaping shell syntax.
    value = str(path)
    for char in ("\\", '"', "$", "`"):
        value = value.replace(char, "\\" + char)
    return '"' + value + '"'


def _enabled(settings: dict) -> bool:
    plugins = settings.get("enabledPlugins", {})
    return isinstance(plugins, dict) and any(
        str(key).startswith("cognee-memory@") and value is True for key, value in plugins.items()
    )


def ensure_statusline_configured(*, pinned_only=False, log=None) -> None:
    """Restore the opted-in pin, or install/refresh our own ordinary status line.

    Only statusLine changes. Corrupt files are untouched; a concurrent external
    edit aborts this attempt and is picked up at the next check. No network.
    """
    log = log or (lambda *args, **kwargs: None)
    settings_path = Path.home() / ".claude/settings.json"
    try:
        # A pin is opt-in. Avoid even creating a lock when no settings exist.
        if pinned_only and not settings_path.is_file():
            return
        with file_lock(settings_path.parent / ".cognee-statusline.lock", timeout=0.0) as acquired:
            if not acquired:
                return
            original = settings_path.read_text(encoding="utf-8") if settings_path.exists() else ""
            settings = json.loads(original) if original.strip() else {}
            if not isinstance(settings, dict):
                return
            pinned = _flag(settings, "COGNEE_STATUSLINE_PIN") in _TRUE
            if pinned_only and not pinned:
                return
            if _flag(settings, "COGNEE_STATUSLINE") in _FALSE or (
                pinned and not _enabled(settings)
            ):
                return
            script = _script(pinned)
            if not script.is_file():
                log("statusline_setup_skipped", {"reason": "script_not_found", "path": str(script)})
                return
            quoted = _shell_quote(script)
            desired = {"type": "command", "command": f"[ -x {quoted} ] && exec {quoted} || true"}
            try:
                interval = int(float(_flag(settings, "COGNEE_STATUSLINE_REFRESH_INTERVAL") or 2))
            except (ValueError, OverflowError):
                interval = 2
            if interval >= 1:
                desired["refreshInterval"] = interval
            existing = settings.get("statusLine")
            if existing == desired:
                return
            command = existing.get("command") if isinstance(existing, dict) else None
            owned = isinstance(command, str) and "cognee-statusline.sh" in command
            if existing and not owned and not pinned:
                log("statusline_setup_skipped", {"reason": "user_statusline_exists"})
                return
            if pinned and existing and not owned:
                backup = Path.home() / ".cognee-plugin/claude-code/statusline-before-pin.json"
                backup.parent.mkdir(parents=True, exist_ok=True)
                try:
                    fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except FileExistsError:
                    pass
                else:
                    with os.fdopen(fd, "w", encoding="utf-8") as handle:
                        json.dump({"statusLine": existing}, handle, indent=2)
                        handle.write("\n")
            settings["statusLine"] = desired
            fd, tmp = tempfile.mkstemp(
                dir=settings_path.parent, prefix=".settings-", suffix=".json"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(settings, handle, indent=2)
                    handle.write("\n")
                latest = settings_path.read_text(encoding="utf-8") if settings_path.exists() else ""
                if latest != original:
                    return
                if settings_path.exists():
                    os.chmod(tmp, settings_path.stat().st_mode & 0o777)
                os.replace(tmp, settings_path)
                log("statusline_configured", {"path": str(script)})
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
    except Exception as exc:
        log("statusline_setup_failed", {"error": str(exc)[:200]})
