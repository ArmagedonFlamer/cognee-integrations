"""Cloud hook subprocesses stay usable with no network or background worker."""

import json
import time

import pytest


def test_offline_start_capture_and_end_are_local(suite, run_hook, temp_home):
    if suite.name != "claude-code":
        pytest.skip("Claude cloud hooks")
    env = {"COGNEE_BACKEND": "cloud", "COGNEE_IDLE_DISABLED": "1"}
    url = "http://127.0.0.1:1"
    payload = {"session_id": "offline-host", "cwd": str(temp_home), "source": "startup"}
    samples = []
    for _ in range(5):
        started = time.monotonic()
        result = run_hook(suite, "session-start.py", stdin=payload, service_url=url, env=env)
        samples.append(time.monotonic() - started)
        assert result.returncode == 0, result.stderr
        assert "background" in result.stdout
    assert max(samples) < 1.0, samples
    prompt = {**payload, "prompt": "Remember our reliable offline session."}
    assert (
        run_hook(suite, "store-user-prompt.py", stdin=prompt, service_url=url, env=env).returncode
        == 0
    )
    answer = {**payload, "last_assistant_message": "The project uses Postgres."}
    assert (
        run_hook(
            suite, "store-to-session.py", "--stop", stdin=answer, service_url=url, env=env
        ).returncode
        == 0
    )
    root = temp_home / ".cognee-plugin" / "claude-code"
    paths = list((root / "spool").glob("*.json"))
    assert len(paths) == 1
    item = json.loads(paths[0].read_text())
    assert item["entry"]["question"] == prompt["prompt"]
    assert item["entry"]["answer"] == answer["last_assistant_message"]
    end = run_hook(
        suite, "sync-session-to-graph.py", "--session-end", stdin=payload, service_url=url, env=env
    )
    assert end.returncode == 0, end.stderr
    wanted = list((root / "improve-wanted").glob("*.json"))
    assert wanted and json.loads(wanted[0].read_text())["reason"] == "final"
    assert not (root / "final-sync-once").exists()
