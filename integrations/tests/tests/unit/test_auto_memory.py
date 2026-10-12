"""Claude Code's built-in auto memory: reading its state, the one-time offer, the CLI.

Auto memory (MEMORY.md) duplicates Cognee, and a plugin cannot switch it off on
its own. SessionStart asks once while it is on by default, and
`cognee-auto-memory.py` writes `autoMemoryEnabled` into the scope the user picks
(SDK-1071). `auto_memory_status` must resolve the switch the way Claude Code
does, or the offer fires for users who already chose and doctor misreports.

claude-code only: no other host ships a competing built-in memory.
"""

from __future__ import annotations

import importlib
import json

import pytest


def _skip_without(suite, name):
    if not (suite.scripts_dir / name).exists():
        pytest.skip(f"{suite.name}: no built-in auto-memory to manage")


@pytest.fixture
def env(suite, temp_home, tmp_path, monkeypatch):
    """Clean slate: no env override, no managed file, a project dir under tmp."""
    _skip_without(suite, "_auto_memory.py")
    for name in ("CLAUDE_CODE_DISABLE_AUTO_MEMORY", "CLAUDE_PROJECT_DIR", "CLAUDE_CONFIG_DIR"):
        monkeypatch.delenv(name, raising=False)
    project = tmp_path / "project"
    project.mkdir()
    return {"home": temp_home, "project": project, "managed": tmp_path / "managed.json"}


def _bind(module, env, monkeypatch):
    monkeypatch.setattr(module, "MANAGED_SETTINGS", env["managed"])
    return module


@pytest.fixture
def am(suite, env, isolated_modules, monkeypatch):
    return _bind(isolated_modules(suite, "_auto_memory"), env, monkeypatch)


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _user_settings(env):
    return env["home"] / ".claude" / "settings.json"


def _local_settings(env):
    return env["project"] / ".claude" / "settings.local.json"


# ── status resolution ──────────────────────────────────────────────────────


def test_on_by_default(am, env):
    status = am.auto_memory_status(str(env["project"]))
    assert status == {"enabled": True, "source": "default", "scope": "default"}


def test_user_setting_turns_it_off(am, env):
    _write(_user_settings(env), {"autoMemoryEnabled": False})
    status = am.auto_memory_status(str(env["project"]))
    assert status["enabled"] is False
    assert status["scope"] == "user"


def test_higher_scope_wins(am, env):
    _write(_user_settings(env), {"autoMemoryEnabled": False})
    _write(_local_settings(env), {"autoMemoryEnabled": True})
    assert am.auto_memory_status(str(env["project"]))["scope"] == "local"
    _write(env["managed"], {"autoMemoryEnabled": False})
    status = am.auto_memory_status(str(env["project"]))
    assert (status["enabled"], status["scope"]) == (False, "managed")


def test_env_var_wins_over_setting(am, env, monkeypatch):
    _write(_user_settings(env), {"autoMemoryEnabled": False})
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_AUTO_MEMORY", "0")
    status = am.auto_memory_status(str(env["project"]))
    assert (status["enabled"], status["scope"]) == (True, "env")


def test_env_block_in_settings_counts_as_env_var(am, env):
    _write(_local_settings(env), {"autoMemoryEnabled": True})
    _write(_user_settings(env), {"env": {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}})
    assert am.auto_memory_status(str(env["project"]))["enabled"] is False


def test_unreadable_settings_are_ignored(am, env):
    _user_settings(env).parent.mkdir(parents=True)
    _user_settings(env).write_text("{not json", encoding="utf-8")
    assert am.auto_memory_status(str(env["project"]))["scope"] == "default"


# ── writing the setting ────────────────────────────────────────────────────


def test_write_keeps_other_keys(am, env):
    _write(_user_settings(env), {"statusLine": {"type": "command"}, "model": "opus"})
    am.write_setting("user", False)
    data = json.loads(_user_settings(env).read_text(encoding="utf-8"))
    assert data == {"statusLine": {"type": "command"}, "model": "opus", "autoMemoryEnabled": False}


def test_project_scope_writes_settings_local(am, env):
    path = am.write_setting("project", False, str(env["project"]))
    assert path == _local_settings(env).resolve()
    assert am.auto_memory_status(str(env["project"]))["scope"] == "local"


def test_remove_restores_default(am, env):
    am.write_setting("user", False)
    am.write_setting("user", None)
    assert json.loads(_user_settings(env).read_text(encoding="utf-8")) == {}
    assert am.auto_memory_status(str(env["project"]))["enabled"] is True


def test_refuses_to_clobber_non_object(am, env):
    _write(_user_settings(env), ["not", "an", "object"])
    with pytest.raises(ValueError):
        am.write_setting("user", False)
    assert json.loads(_user_settings(env).read_text(encoding="utf-8")) == ["not", "an", "object"]


# ── the one-time SessionStart offer ────────────────────────────────────────


@pytest.fixture
def ss(suite, env, hook_module, monkeypatch):
    module = hook_module(suite, "session-start.py")
    if not hasattr(module, "_apply_auto_memory_offer"):
        pytest.skip(f"{suite.name}: no auto-memory offer")
    # The hook imports _auto_memory lazily, from the suite dir now on sys.path.
    _bind(importlib.import_module("_auto_memory"), env, monkeypatch)
    return module


def _offer(ss, env, source="startup", output=None):
    out = ss._apply_auto_memory_offer(output or {}, {"cwd": str(env["project"]), "source": source})
    return (out.get("hookSpecificOutput") or {}).get("additionalContext", "")


def test_offered_once_with_runnable_commands(ss, env):
    text = _offer(ss, env)
    assert "AskUserQuestion" in text
    assert "cognee-auto-memory.py" in text
    assert "off --scope user" in text
    assert f'--project-dir "{env["project"]}"' in text
    assert "keep" in text
    assert _offer(ss, env) == ""  # never asked twice


def test_offer_appends_to_existing_context(ss, env):
    out = ss._apply_auto_memory_offer(
        {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "steer"}},
        {"cwd": str(env["project"]), "source": "startup"},
    )
    context = out["hookSpecificOutput"]["additionalContext"]
    assert context.startswith("steer\n\n")
    assert "auto-memory" in context


def test_not_offered_when_already_set(ss, env):
    _write(_user_settings(env), {"autoMemoryEnabled": True})
    assert _offer(ss, env) == ""


def test_not_offered_after_an_earlier_decision(ss, env):
    import _auto_memory

    _auto_memory.record_offer(decision="kept", decided_at=1)
    assert _offer(ss, env) == ""


def test_not_offered_on_compaction(ss, env):
    assert _offer(ss, env, source="compact") == ""
    assert "cognee-auto-memory.py" in _offer(ss, env)  # still offered later


def test_not_offered_when_steer_opted_out(ss, env, monkeypatch):
    monkeypatch.setenv("COGNEE_PREFER_MEMORY", "false")
    assert _offer(ss, env) == ""


# ── the CLI ────────────────────────────────────────────────────────────────


@pytest.fixture
def cli(suite, env, hook_module, monkeypatch):
    module = hook_module(suite, "cognee-auto-memory.py")
    _bind(module.am, env, monkeypatch)
    return module


def test_cli_off_user_records_decision(cli, env, capsys):
    assert cli.main(["off", "--scope", "user"]) == 0
    assert "Turned off" in capsys.readouterr().out
    assert json.loads(_user_settings(env).read_text(encoding="utf-8"))["autoMemoryEnabled"] is False
    assert cli.am.read_offer()["decision"] == "off_user"


def test_cli_warns_when_overridden(cli, env, capsys):
    _write(_local_settings(env), {"autoMemoryEnabled": True})
    cli.main(["off", "--scope", "user", "--project-dir", str(env["project"])])
    assert "still on" in capsys.readouterr().out


def test_cli_keep_and_status(cli, env, capsys):
    assert cli.main(["keep"]) == 0
    assert cli.am.read_offer()["decision"] == "kept"
    capsys.readouterr()
    cli.main(["status", "--project-dir", str(env["project"])])
    assert capsys.readouterr().out.strip() == "Claude Code auto-memory: on (default)"
