"""A ``<private>`` span and a recognised secret never leave the hooks (SDK-1100).

One turn runs through the real UserPromptSubmit, PostToolUse and Stop scripts
against the mock server. Neither the span's content nor the secret may show up
in any plugin file (pending/, bridge/, spool/, hook.log, recall-audit.log, …)
or in any request body, while the public text around them still arrives.
"""

from __future__ import annotations

import json

import pytest

SPAN_SECRET = "SENTINEL-9f3a"
CREDENTIAL = "hunter2"
PROMPT = (
    f"Deploy notes <private>the vault code is {SPAN_SECRET}</private> and password={CREDENTIAL}"
)


def _plugin_text(home) -> str:
    root = home / ".cognee-plugin"
    return "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )


@pytest.mark.parametrize("route", ["direct", "cloud-spool", "offline-buffer"])
def test_private_span_and_secret_never_leave_the_hooks(
    suite, run_hook, payloads, mock_server, temp_home, closed_port_url, route
):
    """The same turn through each write path: sent at once, spooled, or buffered."""
    if route == "cloud-spool" and suite.name != "claude-code":
        pytest.skip("only claude-code has the cloud spool worker")
    mock_server.set_recall_results([{"question": "Deploy?", "answer": "Use the runbook."}])
    # Offline: the session starts against the server, which then goes away.
    url = closed_port_url if route == "offline-buffer" else mock_server.url
    env = {"COGNEE_BACKEND": "cloud"} if route == "cloud-spool" else None

    def run(script, *args, stdin, service_url=None):
        result = run_hook(
            suite, script, *args, stdin=stdin, service_url=service_url or url, env=env
        )
        assert result.returncode == 0, result.stderr
        return result

    run("session-start.py", stdin=payloads.session_start(), service_url=mock_server.url)
    run("session-context-lookup.py", stdin=payloads.user_prompt(prompt=PROMPT))
    run("store-user-prompt.py", stdin=payloads.user_prompt(prompt=PROMPT))
    run(
        "store-to-session.py",
        stdin=payloads.post_tool_use(
            tool_input={"command": f"echo '<private>{SPAN_SECRET}</private>'"},
            tool_response=f"password={CREDENTIAL}",
            tool_output=f"password={CREDENTIAL}",
        ),
    )
    run(
        "store-to-session.py",
        "--stop",
        stdin=payloads.stop(assistant_message=f"Done. <private>{SPAN_SECRET}</private> stays out."),
    )

    sent = json.dumps(mock_server.calls)
    on_disk = _plugin_text(temp_home)
    for secret in (SPAN_SECRET, CREDENTIAL):
        assert secret not in sent
        assert secret not in on_disk

    # Not vacuous: the turn really took this route, public text intact.
    state = temp_home / ".cognee-plugin"
    if route == "direct":
        queries = [
            call["json"].get("query", "")
            for call in mock_server.calls
            if call["path"] == "/api/v1/recall" and isinstance(call.get("json"), dict)
        ]
        assert any("Deploy notes [private]" in query for query in queries), queries
        stored = [
            json.dumps(call["json"])
            for call in mock_server.calls
            if call["path"] == "/api/v1/remember/entry"
        ]
        assert any("Deploy notes [private]" in body for body in stored), stored
        assert list(state.rglob("recall-audit.log"))
    else:
        buffer = "spool" if route == "cloud-spool" else "bridge"
        held = [p for p in state.rglob("*") if p.is_file() and buffer in p.parts]
        held_text = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in held)
        sent_text = sent if route == "cloud-spool" else ""
        assert "Deploy notes [private]" in held_text + sent_text, [str(p) for p in held]
