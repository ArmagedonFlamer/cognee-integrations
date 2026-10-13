"""Ledger behavior across repeated recall and host context resets."""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def ledger(suite, isolated_modules):
    if suite.name != "claude-code":
        pytest.skip("Claude Code cloud ledger")
    return isolated_modules(suite, "_injection_ledger")


def test_repeat_then_compact_reinjects(ledger):
    sections = [
        {"entries": ["Project uses Postgres.", "Release requires approval."], "priority": 40}
    ]
    first, before, _ = ledger.render("session-a", "Cognee memory", sections, 9000)
    second, after, _ = ledger.render("session-a", "Cognee memory", sections, 9000)
    assert before["entries_kept"] == 2
    assert second == "Cognee memory; 0 new, 2 already in context"
    assert len(second) < len(first)
    assert after["entries_deduped"] == 2
    ledger.reset("session-a")
    assert ledger.render("session-a", "Cognee memory", sections, 9000)[0] == first


def test_guidance_injected_only_when_changed_including_reversion(ledger):
    def render(value):
        return ledger.render("session-a", "header", [{"entries": [value], "priority": 30}], 9000)[1]

    assert render("Use SQLite")["entries_kept"] == 1
    assert render("Use SQLite")["entries_kept"] == 0
    assert render("Use Postgres")["entries_kept"] == 1
    assert render("Use SQLite")["entries_kept"] == 1


def test_only_fitted_entries_are_recorded(ledger):
    sections = [{"entries": ["tiny", "x" * 10000], "priority": 40}]
    ledger.render("session-a", "header", sections, 100)
    data = json.loads((ledger.session_dir("session-a") / "injected.json").read_text())
    assert data["entry_hashes"] == [ledger.entry_hash("tiny")]
    _, stats, _ = ledger.render("session-a", "header", sections, 20000)
    assert stats["entries_kept"] == 1
    assert stats["entries_deduped"] == 1


def test_isolation_normalization_and_corrupt_file(ledger):
    assert ledger.entry_hash("a \n b") == ledger.entry_hash("a b")
    sections = [{"entries": ["fact"], "priority": 40}]
    ledger.render("session-a", "header", sections, 9000)
    assert ledger.render("session-b", "header", sections, 9000)[1]["entries_kept"] == 1
    (ledger.session_dir("session-a") / "injected.json").write_text("{broken")
    assert ledger.render("session-a", "header", sections, 9000)[1]["entries_kept"] == 1
    with pytest.raises(ValueError):
        ledger.reset("../outside")


def test_digest_seed(ledger):
    ledger.seed("session-a", [ledger.entry_hash("fact")])
    _, stats, _ = ledger.render("session-a", "header", [{"entries": ["fact"]}], 9000)
    assert stats["entries_kept"] == 0
