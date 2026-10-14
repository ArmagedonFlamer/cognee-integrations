"""Tests for `_ensure_statusline_configured` (session-start.py) — the one-time
write of the `statusLine` entry into the host's `~/.claude/settings.json`.

The rule is that the plugin owns exactly what it wrote and nothing else:
  * absent entry → write ours;
  * anything the user put there (dict, bare string, a similarly-named command, or
    a cognee mention outside the command itself) → left untouched;
  * an entry that IS ours but points at a stale plugin-cache path → refreshed;
  * an entry already current (refreshInterval included) → not rewritten at all,
    byte for byte;
  * corrupt JSON → preserved, never clobbered;
  * `COGNEE_STATUSLINE=false` → nothing written.

claude-code only: codex has no `_ensure_statusline_configured` (its host wires
the status line itself).

Migrated from claude-code/tests/test_statusline_config.py, whose `ss is None`
guard silently reported skipped tests as PASS — a real skip now.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def ss(suite, hook_module, temp_home, monkeypatch):
    module = hook_module(suite, "session-start.py")
    if not hasattr(module, "_ensure_statusline_configured"):
        pytest.skip(f"{suite.name}: the host wires its own status line")
    monkeypatch.setattr(module, "hook_log", lambda *a, **k: None)
    return module


@pytest.fixture
def settings(temp_home):
    """The host settings file the writer targets, inside the per-test HOME."""
    return temp_home / ".claude" / "settings.json"


def _write(settings, payload, *, raw=False):
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(payload if raw else json.dumps(payload), encoding="utf-8")


def _status_line(settings):
    return json.loads(settings.read_text(encoding="utf-8"))["statusLine"]


def test_statusline_written_when_absent(ss, settings):
    ss._ensure_statusline_configured()
    assert "cognee-statusline.sh" in _status_line(settings)["command"]


def test_existing_user_statusline_is_preserved(ss, settings):
    original = {"type": "command", "command": "printf custom-status"}
    _write(settings, {"statusLine": original})
    ss._ensure_statusline_configured()
    assert _status_line(settings) == original


def test_existing_non_dict_statusline_is_preserved(ss, settings):
    original = "/my/custom/statusline.sh"
    _write(settings, {"statusLine": original})
    ss._ensure_statusline_configured()
    assert _status_line(settings) == original


def test_cognee_marker_outside_command_does_not_claim_statusline(ss, settings):
    original = {
        "type": "command",
        "command": "printf custom-status",
        "description": "not the cognee-statusline command",
    }
    _write(settings, {"statusLine": original})
    ss._ensure_statusline_configured()
    assert _status_line(settings) == original


def test_similarly_named_custom_statusline_is_preserved(ss, settings):
    original = {"type": "command", "command": "/usr/local/bin/cognee-statusline-custom"}
    _write(settings, {"statusLine": original})
    ss._ensure_statusline_configured()
    assert _status_line(settings) == original


def test_owned_statusline_can_be_refreshed(ss, settings):
    stale = {
        "type": "command",
        "command": "/old/plugin/cache/cognee-memory/0.1.0/scripts/cognee-statusline.sh",
    }
    _write(settings, {"statusLine": stale})
    ss._ensure_statusline_configured()
    refreshed = _status_line(settings)
    assert refreshed != stale
    assert "cognee-statusline.sh" in refreshed["command"]


def test_current_statusline_is_not_rewritten(ss, suite, settings):
    script = suite.scripts_dir / "cognee-statusline.sh"
    # Must match what _ensure_statusline_configured() considers current —
    # refreshInterval included, or the entry reads as outdated and the "no
    # rewrite" path under test is never taken.
    desired = {
        "type": "command",
        "command": f'[ -x "{script}" ] && exec "{script}" || true',
        "refreshInterval": 2,
    }
    original = json.dumps({"statusLine": desired})
    _write(settings, original, raw=True)
    ss._ensure_statusline_configured()
    assert settings.read_text(encoding="utf-8") == original


def test_corrupt_settings_are_preserved(ss, settings):
    original = "{not valid json"
    _write(settings, original, raw=True)
    ss._ensure_statusline_configured()
    assert settings.read_text(encoding="utf-8") == original


def test_statusline_setup_can_be_disabled_by_env(ss, settings, monkeypatch):
    monkeypatch.setenv("COGNEE_STATUSLINE", "false")
    ss._ensure_statusline_configured()
    assert not settings.exists()


def _pin(settings, *, enabled=True):
    value = {
        "env": {"COGNEE_STATUSLINE_PIN": "true"},
        "enabledPlugins": {"cognee-memory@cognee": enabled},
        "statusLine": {"type": "command", "command": "printf other-plugin"},
        "unrelated": {"keep": [1, 2, 3]},
    }
    _write(settings, value)
    return value


def test_pin_reclaims_other_plugin_and_preserves_settings(ss, settings):
    previous = _pin(settings)
    ss._ensure_statusline_configured()
    updated = json.loads(settings.read_text())
    assert "cognee-statusline.sh" in updated.pop("statusLine")["command"]
    previous.pop("statusLine")
    assert updated == previous
    backup = settings.parent.parent / ".cognee-plugin/claude-code/statusline-before-pin.json"
    assert json.loads(backup.read_text())["statusLine"]["command"] == "printf other-plugin"
    original = backup.read_bytes()
    _pin(settings)
    ss._ensure_statusline_configured()
    assert backup.read_bytes() == original


@pytest.mark.parametrize("optout", ["pin", "statusline", "plugin"])
def test_pin_can_be_disabled_live(ss, settings, monkeypatch, optout):
    value = _pin(settings)
    monkeypatch.setenv("COGNEE_STATUSLINE_PIN", "true")
    if optout == "pin":
        value["env"]["COGNEE_STATUSLINE_PIN"] = "false"
    elif optout == "statusline":
        value["env"]["COGNEE_STATUSLINE"] = "false"
    else:
        value["enabledPlugins"]["cognee-memory@cognee"] = False
    _write(settings, value)
    from _statusline_config import ensure_statusline_configured

    ensure_statusline_configured(pinned_only=True)
    assert json.loads(settings.read_text()) == value


def test_pin_selects_updated_install_not_old_watcher(ss, settings, tmp_path):
    _pin(settings)
    script = tmp_path / "new release" / "scripts" / "cognee-statusline.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\n")
    _write(
        settings.parent / "plugins/installed_plugins.json",
        {
            "plugins": {
                "cognee-memory@cognee": [
                    {"scope": "user", "installPath": str(script.parent.parent)}
                ]
            }
        },
    )
    ss._ensure_statusline_configured()
    assert str(script) in _status_line(settings)["command"]


def test_pin_does_not_clobber_concurrent_external_edit(ss, settings, monkeypatch):
    _pin(settings)
    import _statusline_config as config

    real_dump = config.json.dump

    def concurrent_dump(value, handle, **kwargs):
        real_dump(value, handle, **kwargs)
        external = json.loads(settings.read_text())
        external["concurrent_change"] = "keep me"
        settings.write_text(json.dumps(external))

    monkeypatch.setattr(config.json, "dump", concurrent_dump)
    ss._ensure_statusline_configured()
    assert json.loads(settings.read_text())["concurrent_change"] == "keep me"
    assert _status_line(settings)["command"] == "printf other-plugin"


def test_live_watcher_restores_pin_and_stops_when_unpinned(ss, suite, settings, temp_home):
    import os
    import subprocess
    import sys
    import time

    from utils.isolation import build_env

    _pin(settings)
    process = subprocess.Popen(
        [
            sys.executable,
            str(suite.scripts_dir / "exit-watcher.py"),
            json.dumps({"parent_pid": os.getpid(), "session_id": "pin-test", "base_url": ""}),
        ],
        env=build_env(suite, temp_home),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:

        def wait_for_repair():
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                assert process.poll() is None
                if "cognee-statusline.sh" in _status_line(settings)["command"]:
                    return
                time.sleep(0.05)
            pytest.fail("Watcher did not restore the pinned status line")

        wait_for_repair()
        _pin(settings)  # Another plugin replaces it while this session is idle.
        wait_for_repair()
        value = _pin(settings)
        value["env"]["COGNEE_STATUSLINE_PIN"] = "false"
        _write(settings, value)
        time.sleep(2.3)
        assert json.loads(settings.read_text()) == value
    finally:
        process.terminate()
        process.communicate(timeout=5)
