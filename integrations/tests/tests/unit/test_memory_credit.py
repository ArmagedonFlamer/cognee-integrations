"""Crediting Cognee memory in the answer (SDK-1071).

The recall hook tags every injected item (``m1``, ``m2``…) with where it came
from and asks the agent to end an answer that used memory with one
``🧠 Cognee memory [mN]: …`` line. The turn's tags are saved, and the Stop hook
checks the answer's citations against them: valid credits and invented ones are
both logged, which is how we measure whether agents credit memory reliably.

Contract:
  * graph memory is tagged per passage, plus one tag each for entities and
    facts; a passage's label names the agent session or document it came from;
  * the instruction rides only on turns that injected something, with the
    longer first-credit wording until an answer has credited memory once;
  * an empty recall injects no header and clears the previous turn's tags;
  * ``COGNEE_PLUGIN_CITE_MEMORY=off`` turns all of it off.

``_recall_text`` is byte-identical in every suite; the hook tests run for the
suites whose recall hook uses it (claude-code and codex).
"""

from __future__ import annotations

import pytest
from utils.recall import drive_recall

SESSION_KEY = "sess-credit-1"
CODEX_SID = "codex_01a0cdda-8d14-7291-bae3-c7eeb7fce251"
CLAUDE_SID = "claude_18c06095-db5a-4356-9d7b-16127c2f3190"

PASSAGE_CODEX = f"Session ID: {CODEX_SID}\n\nQuestion: CLI or server?\n\nAnswer: prefer the server."
PASSAGE_LEARNING = (
    f"# Session learning — 2026-10-03 (session {CLAUDE_SID})\n\nRun ruff before committing."
)
PASSAGE_DOC = "Cognee blog: memory for agents."
CONTEXT = (
    "## Relevant passages\n"
    + "\n---\n".join([PASSAGE_CODEX, PASSAGE_LEARNING, PASSAGE_DOC])
    + "\n\n## Relevant entities\n### server\n- server is preferred over the CLI.\n"
    + "\n## Related facts\n- the plugin prefers the server."
)
HIT = {"graph": [{"source": "graph", "text": CONTEXT}]}


@pytest.fixture
def rt(suite, isolated_modules):
    return isolated_modules(suite, "_recall_text")


# ── tagging ────────────────────────────────────────────────────────────────


def test_graph_memory_is_tagged_per_passage_with_its_origin(rt):
    tags = rt.MemoryTags()
    text = tags.tag_graph(CONTEXT)
    assert f"[m1 · Codex session]\n{PASSAGE_CODEX}" in text
    assert f"[m2 · Claude Code session learning, 2026-10-03]\n{PASSAGE_LEARNING}" in text
    assert f"[m3 · document]\n{PASSAGE_DOC}" in text
    assert "## Relevant entities [m4 · knowledge graph entities]\n### server" in text
    assert "## Related facts [m5 · knowledge graph facts]\n- the plugin prefers" in text
    assert [r["tag"] for r in tags.records] == ["m1", "m2", "m3", "m4", "m5"]
    assert tags.records[0]["snippet"].startswith("Session ID: codex_")


def test_unsectioned_context_is_one_item(rt):
    tags = rt.MemoryTags()
    assert tags.tag_graph("bare context") == "[m1 · Cognee memory]\nbare context"
    assert tags.tag_graph("  ") == "  "
    assert len(tags.records) == 1


@pytest.mark.parametrize(
    ("answer", "valid", "invented"),
    [
        ("done.\n> 🧠 Cognee memory [m2]: you chose the server.", ["m2"], []),
        ("> 🧠 Cognee memory [m1, m3]: …", ["m1", "m3"], []),
        ("> 🧠 Cognee memory [m9]: made up", [], ["m9"]),
        ("[m1] and again [m1]", ["m1"], []),
        ("no credit here [x1] [m]", [], []),
    ],
)
def test_check_credits(rt, answer, valid, invented):
    records = [{"tag": t} for t in ("m1", "m2", "m3")]
    result = rt.check_credits(answer, records)
    assert (result["valid"], result["invented"], result["available"]) == (valid, invented, 3)


def test_credit_state_round_trip(rt, tmp_path):
    assert not rt.has_credited(tmp_path, SESSION_KEY)
    rt.record_credit(tmp_path, SESSION_KEY, {"valid": []})
    assert not rt.has_credited(tmp_path, SESSION_KEY)  # an invented-only turn doesn't count
    rt.record_credit(tmp_path, SESSION_KEY, {"valid": ["m1"]})
    assert rt.has_credited(tmp_path, SESSION_KEY)
    rt.save_turn_tags(tmp_path, "../escape", [{"tag": "m1"}])  # unsafe keys are ignored
    assert not (tmp_path.parent / "escape.json").exists()


# ── the recall hook ────────────────────────────────────────────────────────


@pytest.fixture
def lookup(suite, hook_module, monkeypatch):
    if suite.name not in ("claude-code", "codex"):
        pytest.skip(f"{suite.name}: recall hook does not credit memory")
    module = hook_module(suite, "session-context-lookup.py")
    monkeypatch.setattr(module, "get_session_key", lambda: SESSION_KEY)
    return module


def _context(run) -> str:
    return run.output["hookSpecificOutput"]["additionalContext"]


def _state_dir(lookup):
    return lookup._plugin_common._PLUGIN_DIR


def test_hit_is_tagged_and_carries_the_first_credit_instruction(lookup, monkeypatch, rt):
    ctx = _context(drive_recall(lookup, monkeypatch, recall=HIT))
    assert "[m1 · Codex session]" in ctx
    assert "> 🧠 Cognee memory [mN]:" in ctx
    assert "first Cognee credit in this session" in ctx
    saved = rt.load_turn_tags(_state_dir(lookup), SESSION_KEY)
    assert [r["tag"] for r in saved] == ["m1", "m2", "m3", "m4", "m5"]


def test_after_a_credit_the_short_instruction(lookup, monkeypatch, rt):
    rt.record_credit(_state_dir(lookup), SESSION_KEY, {"valid": ["m1"]})
    ctx = _context(drive_recall(lookup, monkeypatch, recall=HIT))
    assert "> 🧠 Cognee memory [mN]:" in ctx
    assert "first Cognee credit" not in ctx


def test_empty_recall_injects_nothing_and_clears_tags(lookup, monkeypatch, rt):
    drive_recall(lookup, monkeypatch, recall=HIT)
    run = drive_recall(lookup, monkeypatch, recall={})
    assert _context(run) == ""
    assert run.output["hookSpecificOutput"].get("systemMessage") or run.output.get("systemMessage")
    assert rt.load_turn_tags(_state_dir(lookup), SESSION_KEY) == []


def test_opt_out(lookup, monkeypatch, rt):
    monkeypatch.setenv("COGNEE_PLUGIN_CITE_MEMORY", "off")
    ctx = _context(drive_recall(lookup, monkeypatch, recall=HIT))
    assert "[m1" not in ctx and "🧠" not in ctx
    assert PASSAGE_CODEX in ctx  # memory itself is unchanged


# ── the Stop hook ──────────────────────────────────────────────────────────


@pytest.fixture
def stop(suite, hook_module, monkeypatch):
    if suite.name not in ("claude-code", "codex"):
        pytest.skip(f"{suite.name}: no memory credit check")
    module = hook_module(suite, "store-to-session.py")
    events: list = []
    monkeypatch.setattr(module, "get_session_key", lambda: SESSION_KEY)
    monkeypatch.setattr(module, "hook_log", lambda e, d=None: events.append((e, d or {})))
    module.events = events
    return module


def _turn(rt, stop, answer, tags=("m1", "m2")):
    import _plugin_common

    state = _plugin_common._PLUGIN_DIR
    rt.save_turn_tags(state, SESSION_KEY, [{"tag": t} for t in tags])
    stop._check_memory_credit({"last_assistant_message": answer})
    return state


def test_stop_logs_valid_and_invented_credits(stop, rt):
    state = _turn(rt, stop, "ok\n> 🧠 Cognee memory [m2, m7]: you chose the server.")
    assert stop.events == [
        (
            "memory_credit",
            {"cited": ["m2", "m7"], "valid": ["m2"], "invented": ["m7"], "available": 2},
        )
    ]
    assert rt.has_credited(state, SESSION_KEY)


def test_stop_without_credit_logs_an_uncredited_turn(stop, rt):
    state = _turn(rt, stop, "a plain answer")
    assert stop.events[0][1]["cited"] == []
    assert not rt.has_credited(state, SESSION_KEY)


def test_stop_is_silent_when_nothing_was_injected(stop, rt):
    _turn(rt, stop, "> 🧠 Cognee memory [m1]: …", tags=())
    assert stop.events == []


def test_instruction_survives_claude_codes_preview_cut(lookup, monkeypatch):
    """Claude Code shows the model only a 2 KB preview of hook context over 10,000
    characters, so the instruction must come before the memory, not after it."""
    big = {"graph": [{"source": "graph", "text": CONTEXT + "\n- filler fact." * 1500}]}
    ctx = _context(drive_recall(lookup, monkeypatch, recall=big))
    assert len(ctx) > 10_000
    at = ctx.index("> 🧠 Cognee memory [mN]:")
    assert at < ctx.index("[m1 ·")
    assert ctx.index("first Cognee credit") < 2_000


# ── "this conversation was saved" on resume ────────────────────────────────

NOW = 1_791_820_000.0  # a fixed "now"; earlier times below are the same day


def test_notice_for_a_final_save(rt):
    message, ts = rt.sync_notice({"last_improved_at": NOW - 600, "trigger": "final"}, now=NOW)
    assert message.startswith(
        "🧠 Cognee saved this conversation to long-term memory when you left at "
    )
    assert ts == NOW - 600


def test_notice_for_an_idle_save(rt):
    message, _ = rt.sync_notice({"last_improved_at": NOW - 600, "trigger": "idle"}, now=NOW)
    assert "long-term memory at " in message and "when you left" not in message


def test_notice_for_a_failed_final_save_mentions_the_earlier_one(rt):
    state = {
        "last_improved_at": NOW - 3600,
        "trigger": "idle",
        "last_failed_at": NOW - 600,
        "last_failure_reason": "unreachable",
        "last_failure_trigger": "final",
    }
    message, ts = rt.sync_notice(state, now=NOW)
    assert message.startswith("🧠 This conversation wasn't saved to Cognee when you left at ")
    assert "the Cognee server was unreachable" in message
    assert "An earlier save at " in message
    assert ts == NOW - 600


def test_no_record_no_notice(rt):
    assert rt.sync_notice({}, now=NOW) == ("", 0.0)
    assert rt.sync_notice({"last_improved_at": "junk"}, now=NOW) == ("", 0.0)


def test_another_day_shows_the_date(rt):
    message, _ = rt.sync_notice({"last_improved_at": NOW - 3 * 86400, "trigger": "final"}, now=NOW)
    assert any(
        month in message
        for month in (
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        )
    )


def test_notice_is_shown_once_per_save(rt, tmp_path):
    assert rt.notice_due(tmp_path, "claude_s1", 100.0)
    rt.mark_notice_shown(tmp_path, "claude_s1", 100.0)
    assert not rt.notice_due(tmp_path, "claude_s1", 100.0)
    assert rt.notice_due(tmp_path, "claude_s1", 200.0)  # a later save is news again
    assert not rt.notice_due(tmp_path, "", 100.0)


@pytest.fixture
def start(suite, hook_module, monkeypatch):
    if suite.name not in ("claude-code", "codex"):
        pytest.skip(f"{suite.name}: no resume notice")
    module = hook_module(suite, "session-start.py")
    import config

    monkeypatch.setattr(config, "get_session_id", lambda *a, **k: "claude_resumed")
    return module


def test_resume_shows_the_notice_once(start):
    import _plugin_common

    _plugin_common.record_improve_success("claude_resumed", "agent_sessions", "final")
    out = start._apply_sync_notice({"hookSpecificOutput": {"hookEventName": "SessionStart"}})
    # Top level is what Claude Code shows the user; additionalContext reaches the agent.
    assert out["systemMessage"].startswith("🧠 Cognee saved this conversation")
    assert out["hookSpecificOutput"]["additionalContext"] == out["systemMessage"]
    again = start._apply_sync_notice({})
    assert again == {}


def test_a_new_chat_shows_nothing(start):
    assert start._apply_sync_notice({}) == {}
