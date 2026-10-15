"""Visible Cognee status for Codex app-server clients.

Codex TUI renders a hook's ``systemMessage``. Orca currently consumes that
field as hidden developer context, so its users see neither the Cognee status
nor recall counts. The Codex plugin detects Orca from the transcript path and
adds a compact, model-rendered receipt while leaving the full memory context
unchanged.
"""

from __future__ import annotations

import io
import sys

from utils.suites import CODEX


def _lookup(hook_module):
    return hook_module(CODEX, "session-context-lookup.py")


def test_orca_transcript_enables_visible_status(hook_module, monkeypatch):
    lookup = _lookup(hook_module)
    monkeypatch.delenv("COGNEE_CODEX_VISIBLE_STATUS", raising=False)
    payload = {
        "transcript_path": (
            "/Users/me/Library/Application Support/orca/codex-accounts/account/home/"
            "sessions/rollout.jsonl"
        )
    }
    assert lookup._visible_status_enabled(payload)


def test_regular_codex_transcript_does_not_duplicate_visible_status(hook_module, monkeypatch):
    lookup = _lookup(hook_module)
    monkeypatch.delenv("COGNEE_CODEX_VISIBLE_STATUS", raising=False)
    assert not lookup._visible_status_enabled(
        {"transcript_path": "/Users/me/.codex/sessions/rollout.jsonl"}
    )


def test_explicit_setting_overrides_host_detection(hook_module, monkeypatch):
    lookup = _lookup(hook_module)
    orca = {
        "transcript_path": (
            "C:\\Users\\me\\Application Support\\orca\\codex-accounts\\account\\rollout.jsonl"
        )
    }
    monkeypatch.setenv("COGNEE_CODEX_VISIBLE_STATUS", "off")
    assert not lookup._visible_status_enabled(orca)
    monkeypatch.setenv("COGNEE_CODEX_VISIBLE_STATUS", "on")
    assert lookup._visible_status_enabled({})


def test_receipt_is_exact_and_preserves_private_context(hook_module):
    lookup = _lookup(hook_module)
    output = {
        "systemMessage": (
            "● cognee: agent_sessions · local\n"
            "Cognee memory: 2 memory hits · 1/1 turns had hits this session"
        ),
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "private recalled context",
        },
    }

    actual = lookup._with_visible_status_receipt(output, True)
    context = actual["hookSpecificOutput"]["additionalContext"]
    assert (
        "> 🧠 ● cognee: agent_sessions · local · "
        "Cognee memory: 2 memory hits · 1/1 turns had hits this session"
    ) in context
    assert context.endswith("private recalled context")
    assert actual["systemMessage"].startswith("● cognee:")


def test_main_applies_receipt_for_orca_payload(hook_module, monkeypatch):
    lookup = _lookup(hook_module)
    monkeypatch.delenv("COGNEE_CODEX_VISIBLE_STATUS", raising=False)
    payload = {
        "session_id": "codex_session",
        "turn_id": "turn-1",
        "prompt": "show me the status",
        "cwd": "/repo",
        "transcript_path": (
            "/Users/me/Library/Application Support/orca/codex-accounts/account/rollout.jsonl"
        ),
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(__import__("json").dumps(payload)))
    monkeypatch.setattr(
        lookup, "resolve_session_key_from_payload", lambda value: ("session", "payload.session_id")
    )
    monkeypatch.setattr(lookup, "set_session_key", lambda value: None)
    monkeypatch.setattr(lookup, "get_session_key", lambda: "session")
    monkeypatch.setattr(lookup, "hook_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(lookup, "with_base_url_notice", lambda output, event: output)

    async def fake_run(prompt, cwd):
        return {
            "systemMessage": "cognee: ds · local\nCognee memory: 1 memory hit",
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": "memory",
            },
        }

    monkeypatch.setattr(lookup, "_run", fake_run)
    output = lookup.main()
    assert (
        "> 🧠 cognee: ds · local · Cognee memory: 1 memory hit"
        in output["hookSpecificOutput"]["additionalContext"]
    )
