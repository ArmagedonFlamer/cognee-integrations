"""Tests for `_retire_statusline_entry` (session-start.py): the one-time removal
of the `statusLine` entry earlier plugin versions wrote to `~/.claude/settings.json`.

The status row is drawn above the prompt by the hooks module now, so the plugin
no longer installs a settings-file status line, and takes back the one it wrote:
  * an entry that is ours (its command runs cognee-statusline.sh) → removed, the
    rest of the file kept;
  * anything the user put there (dict, bare string, a similarly-named command, or
    a cognee mention outside the command itself) → left untouched, byte for byte;
  * no entry, or no settings file → nothing written, no file created;
  * corrupt JSON → preserved, never clobbered.

claude-code only: codex has no `_retire_statusline_entry` (its host wires the
status line itself).
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def ss(suite, hook_module, temp_home, monkeypatch):
    module = hook_module(suite, "session-start.py")
    if not hasattr(module, "_retire_statusline_entry"):
        pytest.skip(f"{suite.name}: the host wires its own status line")
    monkeypatch.setattr(module, "hook_log", lambda *a, **k: None)
    return module


@pytest.fixture
def settings(temp_home):
    """The host settings file the migration targets, inside the per-test HOME."""
    return temp_home / ".claude" / "settings.json"


def _write(settings, payload, *, raw=False):
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(payload if raw else json.dumps(payload), encoding="utf-8")


def _read(settings):
    return json.loads(settings.read_text(encoding="utf-8"))


def _mark_band(temp_home, session="11111111-2222-4333-8444-555555555555"):
    """What the hooks module writes at a session start it runs in."""
    band = temp_home / ".cognee-plugin" / "claude-code" / "band"
    band.mkdir(parents=True, exist_ok=True)
    (band / f"{session}.json").write_text(json.dumps({"session_id": session}), encoding="utf-8")


OURS = {
    "type": "command",
    "command": '[ -x "/plugins/cognee-memory/scripts/cognee-statusline.sh" && exec '
    '"/plugins/cognee-memory/scripts/cognee-statusline.sh" || true',
    "refreshInterval": 2,
}


def test_our_entry_is_removed_and_the_rest_kept(ss, settings, temp_home):
    _mark_band(temp_home)
    _write(settings, {"statusLine": OURS, "enabledPlugins": {"cognee-memory@cognee": True}})
    ss._retire_statusline_entry()
    assert _read(settings) == {"enabledPlugins": {"cognee-memory@cognee": True}}


def test_a_stale_cache_path_of_ours_is_removed_too(ss, settings, temp_home):
    _mark_band(temp_home)
    stale = {
        "type": "command",
        "command": "/old/plugin/cache/cognee-memory/0.1.0/scripts/cognee-statusline.sh",
    }
    _write(settings, {"statusLine": stale})
    ss._retire_statusline_entry()
    assert "statusLine" not in _read(settings)


@pytest.mark.parametrize(
    "original",
    [
        {"type": "command", "command": "printf custom-status"},
        "/my/custom/statusline.sh",
        {"type": "command", "command": "/usr/local/bin/cognee-statusline-custom"},
        {
            "type": "command",
            "command": "printf custom-status",
            "description": "not the cognee-statusline command",
        },
    ],
)
def test_a_users_own_entry_is_preserved_byte_for_byte(ss, settings, original):
    text = json.dumps({"statusLine": original})
    _write(settings, text, raw=True)
    ss._retire_statusline_entry()
    assert settings.read_text(encoding="utf-8") == text


def test_nothing_is_written_when_there_is_no_entry(ss, settings):
    text = json.dumps({"enabledPlugins": {"cognee-memory@cognee": True}})
    _write(settings, text, raw=True)
    ss._retire_statusline_entry()
    assert settings.read_text(encoding="utf-8") == text


def test_no_settings_file_is_created(ss, settings):
    ss._retire_statusline_entry()
    assert not settings.exists()


def test_corrupt_settings_are_preserved(ss, settings):
    original = "{not valid json"
    _write(settings, original, raw=True)
    ss._retire_statusline_entry()
    assert settings.read_text(encoding="utf-8") == original


# ── the gate: only a module that has drawn retires the line ─────────────────


def test_the_entry_is_kept_while_the_module_has_never_drawn(ss, settings):
    """An older Claude Code, --safe-mode or a managed org never loads the hooks
    module, so there is nothing to draw the row in the line's place."""
    _write(settings, {"statusLine": OURS, "enabledPlugins": {"cognee-memory@cognee": True}})
    ss._retire_statusline_entry()
    assert _read(settings)["statusLine"] == OURS


def test_the_entry_is_removed_once_a_band_marker_exists(ss, settings, temp_home):
    _mark_band(temp_home)
    _write(settings, {"statusLine": OURS})
    ss._retire_statusline_entry()
    assert "statusLine" not in _read(settings)
