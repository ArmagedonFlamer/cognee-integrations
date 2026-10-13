"""Durable cloud work: outage, recovery, retention, and truthful improve outcomes."""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def worker(suite, isolated_modules, monkeypatch):
    if suite.name != "claude-code":
        pytest.skip("Cloud worker")
    worker = isolated_modules(suite, "_cloud_worker")
    monkeypatch.setenv("COGNEE_SESSION_KEY", "host-a")
    monkeypatch.setattr(
        worker, "target", lambda: {"base_url": "https://cloud.example", "principal": "key"}
    )
    monkeypatch.setattr(worker, "ensure_worker", lambda: None)
    monkeypatch.setattr(worker, "log", lambda *a, **kw: None)
    return worker


def test_capture_outage_retains_then_recovers_in_order(worker):
    paths = [
        worker.enqueue("ds", "sid", {"type": "qa", "answer": str(i)}, "answer") for i in range(3)
    ]
    created = worker.read(paths[0])["created_at"]

    def offline(item):
        raise OSError("offline")

    assert worker.drain(offline, now=created) == 0
    assert all(path.exists() for path in paths)
    assert worker.read(paths[0])["attempts"] == 1
    delivered = []

    def send(item):
        delivered.append(item["entry"]["answer"])
        return {"entry_id": "saved"}

    assert worker.drain(send, now=created + 6) == 3
    assert delivered == ["0", "1", "2"]
    assert not any(path.exists() for path in paths)


def test_expiry_archives_and_logs_without_silent_deletion(worker, monkeypatch):
    events = []
    monkeypatch.setattr(worker, "log", lambda event, **kw: events.append(event))
    path = worker.enqueue("ds", "sid", {"type": "qa", "answer": "retain me"}, "answer")
    worker.drain(
        lambda _: pytest.fail("expired event was sent"),
        now=worker.read(path)["created_at"] + 7 * 86400 + 1,
    )
    archived = path.parent / "expired" / path.name
    assert worker.read(archived)["entry"]["answer"] == "retain me"
    assert "cloud_work_expired" in events


def test_ambiguous_success_is_verified_before_retry(worker):
    path = worker.enqueue("ds", "sid", {"type": "qa", "answer": "once"}, "answer")
    item = worker.read(path)
    item["ambiguous"] = True
    worker.atomic_json(path, item)
    assert worker.drain(lambda _: pytest.fail("duplicate POST"), verify=lambda _: True) == 1


def test_idempotent_retry_keeps_event_and_request_after_lost_ack(worker):
    paths = [
        worker.enqueue("ds", "sid", {"type": "qa", "answer": "same"}, "answer") for _ in range(2)
    ]
    ids = [worker.read(path)["client_event_id"] for path in paths]
    assert len(set(ids)) == 2
    requests = []
    receipts = {}

    def prepare(item):
        return {
            "idempotent": True,
            "request": {"entry": item["entry"], "client_event_id": item["client_event_id"]},
        }

    def send(item):
        requests.append(item["request"])
        event = item["client_event_id"]
        if event not in receipts:
            receipts[event] = {"entry_id": event, "client_event_id": event, "payload_hash": "hash"}
            raise TimeoutError("server committed before ACK")
        return receipts[event]

    def never_verify(_):
        pytest.fail("content matching must not suppress distinct events")

    assert worker.drain(send, prepare=prepare, verify=never_verify) == 0
    first = worker.read(paths[0])
    assert first["ambiguous"] and first["idempotent"]
    assert (
        worker.drain(send, now=first["next_attempt"] + 1, verify=never_verify, prepare=prepare) == 1
    )
    second = worker.read(paths[1])
    assert (
        worker.drain(send, now=second["next_attempt"] + 1, verify=never_verify, prepare=prepare)
        == 1
    )
    assert requests[0] == requests[1] and requests[2] == requests[3]
    assert len(receipts) == 2 and not any(p.exists() for p in paths)


def test_idempotent_capture_requires_matching_receipt(worker):
    path = worker.enqueue("ds", "sid", {"type": "qa", "answer": "saved?"}, "answer")
    item = worker.read(path)
    item["idempotent"] = True
    worker.atomic_json(path, item)
    assert worker.drain(lambda _: {"entry_id": "old-server-response"}) == 0
    assert path.exists()


def test_changed_payload_conflict_is_quarantined(worker):
    import io
    import urllib.error

    path = worker.enqueue("ds", "sid", {"type": "qa", "answer": "changed"}, "answer")
    item = worker.read(path)
    item["idempotent"] = True
    worker.atomic_json(path, item)

    def conflict(_):
        raise urllib.error.HTTPError(
            "https://example",
            409,
            "conflict",
            {},
            io.BytesIO(b'{"code":"capture_payload_conflict"}'),
        )

    assert worker.drain(conflict) == 0
    assert not path.exists() and (path.parent / "expired" / path.name).exists()


def test_wrong_tenant_or_principal_never_receives_spool(worker, monkeypatch):
    path = worker.enqueue("ds", "sid", {"type": "qa", "answer": "private"}, "answer")
    monkeypatch.setattr(
        worker, "target", lambda: {"base_url": "https://other.example", "principal": "other"}
    )
    assert worker.drain(lambda _: pytest.fail("wrong tenant")) == 0
    assert path.exists()


@pytest.mark.parametrize(
    "result,ok,reason",
    [
        ({}, False, "busy"),
        ({"ok": True}, False, "unsupported_result"),
        ({"stages": [{"status": "completed"}], "finished": False}, False, "running"),
        ({"stages": [{"status": "errored"}]}, False, "errored"),
        ({"stages": [{"status": "skipped", "reason": "lock_held"}]}, False, "busy"),
        ({"stages": [{"status": "skipped", "reason": "no_session_ids"}]}, False, "skipped"),
        (
            {
                "stages": [
                    {"status": "completed"},
                    {"status": "skipped", "reason": "disabled_by_config"},
                ]
            },
            True,
            "completed",
        ),
    ],
)
def test_improve_result_contract(worker, result, ok, reason):
    assert worker.improve_outcome(result)["ok"] is ok
    assert worker.improve_outcome(result)["reason"] == reason


def test_improve_waits_for_capture_and_retries_unconfirmed(worker):
    capture = worker.enqueue("ds", "sid", {"type": "qa", "answer": "fact"}, "answer")
    worker.request_improve("ds", "sid", "final")
    assert worker.process_improves(lambda _: pytest.fail("capture is still pending")) == 0
    capture.unlink()
    assert worker.process_improves(lambda _: {"ok": True}) == 0
    marker = next((worker.root() / "improve-wanted").glob("*.json"))
    due = worker.read(marker)["next_attempt"]
    assert (
        worker.process_improves(lambda _: {"stages": [{"status": "completed"}]}, now=due + 1) == 1
    )
    assert not marker.exists()
    data = json.loads((worker.session_dir("host-a") / "stats.json").read_text())
    assert data["improves_landed"] == 1


def test_concurrent_request_survives_inflight_completion(worker):
    worker.request_improve("ds", "sid", "final")

    def improve(item):
        worker.request_improve("ds", "sid", "final")
        return {"stages": [{"status": "completed"}]}

    worker.process_improves(improve)
    assert list((worker.root() / "improve-wanted").glob("*.json"))


def test_idle_does_not_downgrade_final_request(worker):
    worker.request_improve("ds", "sid", "final")
    worker.request_improve("ds", "sid", "idle")
    marker = next((worker.root() / "improve-wanted").glob("*.json"))
    assert worker.read(marker)["reason"] == "final"
