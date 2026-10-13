"""Whole-entry budgeting for Claude Code hooks."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "claude-code/scripts/_injection_budget.py"
spec = importlib.util.spec_from_file_location("claude_injection_budget", SCRIPT)
budget = importlib.util.module_from_spec(spec)
spec.loader.exec_module(budget)


def test_thirty_large_passages_keep_header_and_whole_entries():
    passages = [f"passage {i}\n```python\n" + "x" * 1100 + "\n```" for i in range(30)]
    text, stats = budget.fit_sections(
        [
            {"entries": ["Cognee memory: 30 hits"], "required": True},
            {"title": "=== Memory ===", "entries": passages, "priority": 40},
        ],
        9000,
    )
    assert len(text) <= 9000
    assert text.startswith("Cognee memory: 30 hits")
    assert 0 < stats["entries_kept"] < 30
    assert stats["entries_dropped"] + stats["entries_kept"] == 30
    assert text.count("```") == stats["entries_kept"] * 2
    assert all(entry in passages for entry in stats["entries"])


def test_priority_and_empty_headings():
    text, stats = budget.fit_sections(
        [
            {"entries": ["header"], "required": True},
            {"title": "code", "entries": ["code fact"], "priority": 50},
            {"title": "trace", "entries": ["trace " * 1000], "priority": 10},
        ],
        50,
    )
    assert text == "header\n\ncode\ncode fact"
    assert stats["entries_dropped"] == 1


def test_header_survives_entry_larger_than_budget():
    text, stats = budget.fit_sections(
        [
            {"entries": ["header"], "required": True},
            {"entries": ["x" * 20000]},
        ],
        9000,
    )
    assert text == "header"
    assert stats["entries_kept"] == 0


def test_required_header_larger_than_budget_is_explicit_error():
    with pytest.raises(ValueError, match="header"):
        budget.fit_sections([{"entries": ["header"], "required": True}], 1)


@pytest.mark.parametrize("value,expected", [("999999", 9000), ("5000", 5000), ("bad", 9000)])
def test_limit_never_exceeds_host_ceiling(monkeypatch, value, expected):
    monkeypatch.setenv("COGNEE_INJECT_MAX_CHARS", value)
    assert budget.injection_limit() == expected


def test_serialized_unicode_and_backslashes_stay_bounded():
    import json

    text, _ = budget.fit_sections(
        [
            {"entries": ["header"], "required": True},
            {"entries": ["🧠" * 300 + "\\" * 100] * 30},
        ],
        9000,
    )
    assert len(json.dumps(text)) - 2 <= 9000
