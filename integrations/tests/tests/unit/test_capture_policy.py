"""Capture controls protect both online and buffered automatic memory."""

import asyncio
import json
from unittest.mock import Mock

import pytest


@pytest.fixture
def policy(suite, isolated_modules):
    return isolated_modules(suite, "_capture_policy")


@pytest.mark.parametrize(
    "path", ["/project/.env", "C:\\repo\\.env.prod", ".ssh/id_rsa", "cert.pem", ".aws/credentials"]
)
def test_default_sensitive_paths_are_excluded(policy, path):
    assert not policy.allow_tool("Read", {"file_path": path})
    assert policy.allow_tool("Read", {"file_path": "/project/main.py"})


def test_tool_allowlist_and_extra_paths(policy, monkeypatch):
    monkeypatch.setenv("COGNEE_CAPTURE_TOOLS", "Grep|Glob")
    assert not policy.allow_tool("Read", {"path": "main.py"})
    assert policy.allow_tool("Grep", {"path": "main.py"})
    monkeypatch.setenv("COGNEE_CAPTURE_DENY_PATHS", '["*.private"]')
    assert not policy.allow_tool("Grep", {"path": "payments.private"})


@pytest.mark.parametrize(
    "text,secret",
    [
        ("password=supersecret", "supersecret"),
        ('{"api_key": "hidden-value"}', "hidden-value"),
        ("Authorization: Bearer abc.secret.value", "abc.secret.value"),
        ("postgresql://user:password@host/db", "user:password"),
        (
            "-----BEGIN PRIVATE KEY-----\nPRIVATE_CONTENT\n-----END PRIVATE KEY-----",
            "PRIVATE_CONTENT",
        ),
        ("ghp_012345678901234567890123456789", "012345678901234567890123456789"),
    ],
)
def test_secret_formats_are_scrubbed(policy, text, secret):
    assert secret not in policy.redact(text)
    assert "[redacted:" in policy.redact(text)


def test_nested_keys_and_custom_patterns(policy, monkeypatch):
    assert policy.redact({"params": {"access_token": "private-value"}}) == {
        "params": {"access_token": "[redacted:credential]"}
    }
    monkeypatch.setenv("COGNEE_CAPTURE_REDACT_PATTERNS", '["customer-[0-9]+"]')
    assert policy.redact("customer-123") == "[redacted:custom]"
    monkeypatch.setenv("COGNEE_CAPTURE_REDACT_PATTERNS", '["["]')
    with pytest.raises(Exception):
        policy.redact("secret-bearing input")


def test_capture_disabled_does_not_touch_session_state(suite, hook_module, monkeypatch):
    prompt = hook_module(suite, "store-user-prompt.py")
    store = hook_module(suite, "store-to-session.py")
    monkeypatch.setenv("COGNEE_CAPTURE", "false")
    for module in (prompt, store):
        monkeypatch.setattr(
            module, "_load_session", Mock(side_effect=AssertionError("capture ran"))
        )
    asyncio.run(prompt._store("a sensitive prompt", {}))
    asyncio.run(store._store_tool_call({"tool_name": "Read", "tool_input": {"path": "normal.py"}}))
    asyncio.run(store._store_assistant_stop({"assistant_message": "a sensitive answer"}))


def test_filtered_trace_never_reaches_session_or_buffer(suite, hook_module, monkeypatch):
    store = hook_module(suite, "store-to-session.py")
    monkeypatch.setattr(store, "_load_session", Mock(side_effect=AssertionError("capture ran")))
    asyncio.run(
        store._store_tool_call(
            {
                "tool_name": "Read",
                "tool_input": {"file_path": "/repo/.env"},
                "tool_response": "password=supersecret",
            }
        )
    )


def test_online_and_buffered_trace_receive_redacted_content(suite, hook_module, monkeypatch):
    for online in (False, True):
        store = hook_module(suite, "store-to-session.py")
        if suite.name == "claude-code":
            monkeypatch.setattr(store, "cloud_enabled", lambda *args: False)

        monkeypatch.setattr(store, "_load_session", lambda: ("session", "dataset", "user"))
        monkeypatch.setattr(store, "load_config", lambda: {})
        monkeypatch.setattr(
            store,
            "resolve_runtime_mode",
            lambda: {"mode": "http", "base_url": "http://example.test"},
        )
        monkeypatch.setattr(store, "server_usable", lambda url: online)
        writes = []
        monkeypatch.setattr(
            store, "append_warmup_entry", lambda *args, **kwargs: writes.append(args[2])
        )
        monkeypatch.setattr(
            store, "remember_entry_via_http", lambda *args, **kwargs: writes.append(args[2])
        )
        asyncio.run(
            store._store_tool_call(
                {
                    "tool_name": "Bash",
                    "tool_input": {"command": "echo password=supersecret"},
                    "tool_response": "Bearer private.token.value",
                }
            )
        )
        assert len(writes) == 1
        serialized = json.dumps(writes[0])
        assert "supersecret" not in serialized and "private.token.value" not in serialized


@pytest.mark.parametrize(
    "tools,name,params,expected",
    [
        # Tool(prefix:*) borrows Claude Code's permission spelling (#423).
        ("Bash(git:*)|Read", "Bash", {"command": "git status"}, True),
        ("Bash(git:*)|Read", "Bash", {"command": "rg foo src/"}, False),
        ("Bash(git:*)|Read", "Bash", {"command": "gitx weird"}, False),
        ("Bash(git:*)|Read", "Bash", {"command": "  git\tlog -1"}, True),
        ("Bash(git:*)|Read", "Bash", {"command": "git"}, True),
        ("Bash(git:*)|Read", "Bash", {}, False),
        ("Bash(git:*)|Read", "Read", {"file_path": "main.py"}, True),
        ("Bash(git:*)|Read", "Grep", {"pattern": "x"}, False),
        # A multi-word prefix still ends on a word boundary.
        ("Bash(git status:*)", "Bash", {"command": "git status --short"}, True),
        ("Bash(git status:*)", "Bash", {"command": "git log"}, False),
        # Prefix test, not a shell parser: a compound command matches its first word.
        ("Bash(git:*)", "Bash", {"command": "cd x && git status"}, False),
        # Legacy spellings keep their meaning.
        ("Bash", "Bash", {"command": "rg foo src/"}, True),
        ("B*", "Bash", {"command": "rg foo src/"}, True),
        ('["Bash(git:*)", "Edit"]', "Bash", {"command": "git push"}, True),
        ('["Bash(git:*)", "Edit"]', "Edit", {"file_path": "a.py"}, True),
        ('["Bash(git:*)", "Edit"]', "Bash", {"command": "ls"}, False),
    ],
)
def test_command_prefix_patterns(policy, monkeypatch, tools, name, params, expected):
    monkeypatch.setenv("COGNEE_CAPTURE_TOOLS", tools)
    assert policy.allow_tool(name, params) is expected


def test_deny_tools_apply_after_the_allowlist(policy, monkeypatch):
    monkeypatch.setenv("COGNEE_CAPTURE_DENY_TOOLS", "Bash(rg:*)|Bash(grep:*)|Glob")
    assert policy.allow_tool("Bash", {"command": "git status"})
    assert not policy.allow_tool("Bash", {"command": "  rg foo"})
    assert not policy.allow_tool("Bash", {"command": "grep -r foo ."})
    assert not policy.allow_tool("Glob", {"pattern": "*.py"})
    assert policy.allow_tool("Read", {"file_path": "main.py"})
    # Deny wins over an allowlist entry naming the same call.
    monkeypatch.setenv("COGNEE_CAPTURE_TOOLS", "Bash")
    assert not policy.allow_tool("Bash", {"command": "rg foo"})
    assert policy.allow_tool("Bash", {"command": "pytest -q"})
    # The sensitive-path check still runs after both lists.
    assert not policy.allow_tool("Bash", {"command": "pytest", "file_path": "/repo/.env"})


@pytest.mark.parametrize("pattern", ["Bash(rg)", "Bash()", "Bash(rg:*", "Bash(:*)", "(git:*)"])
def test_malformed_patterns_fail_loudly(policy, monkeypatch, pattern):
    """Every misspelling used to match nothing and switch capture off in silence."""
    assert policy.capture_pattern_error() == ""
    # The bad entry is rejected even when an earlier entry would have matched.
    monkeypatch.setenv("COGNEE_CAPTURE_TOOLS", f"Bash(git:*)|{pattern}")
    with pytest.raises(policy.CapturePatternError):
        policy.allow_tool("Bash", {"command": "git status"})
    assert pattern in policy.capture_pattern_error()
    monkeypatch.delenv("COGNEE_CAPTURE_TOOLS")
    monkeypatch.setenv("COGNEE_CAPTURE_DENY_TOOLS", pattern)
    with pytest.raises(policy.CapturePatternError):
        policy.allow_tool("Read", {"file_path": "a.py"})
    assert pattern in policy.capture_pattern_error()


def test_invalid_pattern_fails_closed_in_the_store_hook(suite, hook_module, monkeypatch):
    store = hook_module(suite, "store-to-session.py")
    monkeypatch.setenv("COGNEE_CAPTURE_TOOLS", "Bash(rg)")
    monkeypatch.setattr(store, "_load_session", Mock(side_effect=AssertionError("capture ran")))
    events = []
    monkeypatch.setattr(store, "hook_log", lambda event, detail=None: events.append(event))
    asyncio.run(
        store._store_tool_call({"tool_name": "Bash", "tool_input": {"command": "git status"}})
    )
    assert "capture_tools_invalid" in events


def test_invalid_pattern_keeps_the_warmup_buffer(suite, isolated_modules, tmp_path, monkeypatch):
    """A typo in the allowlist must not cost traces buffered before it was made."""
    common = isolated_modules(suite, "_plugin_common")
    bridge = tmp_path / "bridge.json"
    monkeypatch.setattr(common, "_bridge_file", lambda sid="": bridge)
    monkeypatch.setattr(common, "_DRAIN_LOCK", tmp_path / "drain.lock")
    monkeypatch.setattr(common, "_BUFFER_LOCK", tmp_path / "buffer.lock")
    events = []
    monkeypatch.setattr(common, "hook_log", lambda ev, detail=None: events.append(ev))
    sent = []
    monkeypatch.setattr(
        common, "remember_entry_via_http", lambda d, s, entry, **k: sent.append(entry) or {}
    )
    entry = {"type": "trace", "origin_function": "Bash", "method_params": {"command": "git"}}
    common.append_warmup_entry("ds", "sid", entry)
    monkeypatch.setenv("COGNEE_CAPTURE_TOOLS", "Bash(rg)")
    assert common.drain_warmup_entries("ds", "sid") == (0, 1)
    assert sent == [] and "capture_tools_invalid" in events
    monkeypatch.setenv("COGNEE_CAPTURE_TOOLS", "Bash(git:*)")
    assert common.drain_warmup_entries("ds", "sid") == (1, 0)
    assert len(sent) == 1


def test_invalid_pattern_is_reported_on_the_next_prompt(suite, isolated_modules, monkeypatch):
    """The store hook runs async with hidden output; the recall hook carries the notice."""
    common = isolated_modules(suite, "_plugin_common")
    assert common.with_base_url_notice({}, "UserPromptSubmit") == {}
    monkeypatch.setenv("COGNEE_CAPTURE_DENY_TOOLS", "Bash(rg)")
    output = common.with_base_url_notice({}, "UserPromptSubmit")
    assert "Bash(rg)" in output["systemMessage"]
    assert "Bash(rg)" in output["hookSpecificOutput"]["additionalContext"]


def test_malformed_json_list_is_reported_with_its_variable_name(policy, monkeypatch):
    monkeypatch.setenv("COGNEE_CAPTURE_TOOLS", "[Bash rg]")
    with pytest.raises(policy.CapturePatternError, match="COGNEE_CAPTURE_TOOLS"):
        policy.allow_tool("Bash", {"command": "git status"})
    assert "COGNEE_CAPTURE_TOOLS" in policy.capture_pattern_error()
