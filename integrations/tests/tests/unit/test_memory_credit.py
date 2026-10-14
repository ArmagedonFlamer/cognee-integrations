"""Crediting Cognee memory in the answer (SDK-1071).

The recall hook tags every injected item (``m1``, ``m2``…) with where it came
from and asks the agent to end an answer that used memory with one
``🧠 Cognee memory [mN]: …`` line. Tags are numbered across the session and a
memory recalled again keeps its tag, and the Stop hook checks the answer's
credit line against the session's tags: valid credits and invented ones are
both logged, which is how we measure whether agents credit memory reliably.

Contract:
  * graph memory is tagged per passage, plus one tag each for entities and
    facts; a passage's label names the agent session or document it came from;
  * a memory recalled again in the session keeps its tag: an earlier turn or
    trace by its server id, anything else by its text;
  * the instruction rides only on turns that injected something, says how many
    memories this prompt found, and keeps the longer first-credit wording until
    an answer has credited memory once;
  * only the credit line counts: ``arr[m1]`` in code is not a citation;
  * every prompt starts a new turn before recall can return early, so a cited
    tag from an earlier turn is logged as ``earlier``, never as this turn's;
  * an empty recall injects no header;
  * ``COGNEE_PLUGIN_CITE_MEMORY=off`` turns all of it off, and Codex starts
    with it off.

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


def test_a_memory_recalled_again_keeps_its_tag(rt, tmp_path):
    first = rt.MemoryTags()
    first.tag_graph(CONTEXT)
    first.save(tmp_path, SESSION_KEY)
    rt.begin_turn(tmp_path, SESSION_KEY)

    again = rt.MemoryTags.load(tmp_path, SESSION_KEY)
    new = "Session ID: codex_new\n\nQuestion: tabs?\n\nAnswer: spaces."
    text = again.tag_graph("## Relevant passages\n" + "\n---\n".join([PASSAGE_DOC, new]))
    assert "[m3 · document]" in text  # seen last turn
    assert "[m6 · Codex session]" in text  # new: the next number
    assert again.found == ["m3", "m6"]


def test_a_server_id_identifies_a_memory_whatever_its_text(rt):
    tags = rt.MemoryTags()
    assert tags.label("session", "earlier session turn", "Q: a\nA: b", "qa-1") == (
        "[m1 · earlier session turn]"
    )
    assert tags.add("session", "earlier session turn", "Q: a\nA: b, edited", "qa-1") == "m1"
    assert tags.add("session", "earlier session turn", "Q: a\nA: b, edited") == "m2"


def test_unsectioned_context_is_one_item(rt):
    tags = rt.MemoryTags()
    assert tags.tag_graph("bare context") == "[m1 · Cognee memory]\nbare context"
    assert tags.tag_graph("  ") == "  "
    assert len(tags.records) == 1


def _session_tags(rt, this_turn=("m1", "m2"), earlier=("m3",)):
    """Tags of a session on turn 2: ``this_turn`` injected now, ``earlier`` on turn 1."""
    records = [{"tag": t, "id": t, "turn": 2} for t in this_turn]
    records += [{"tag": t, "id": t, "turn": 1} for t in earlier]
    return rt.MemoryTags({"turn": 2, "tags": records})


@pytest.mark.parametrize(
    ("answer", "valid", "invented", "earlier"),
    [
        ("done.\n> 🧠 Cognee memory [m2]: you chose the server.", ["m2"], [], []),
        ("> 🧠 Cognee memory [m1, m3]: …", ["m1", "m3"], [], ["m3"]),
        ("> 🧠 Cognee memory [m9]: made up", [], ["m9"], []),
        ("Cognee memory [m1] and again Cognee memory [m1]", ["m1"], [], []),
        ("no credit here [x1] [m]", [], [], []),
        # Not a credit line: code, and the instruction's own example quoted back.
        ("use `arr[m1]`; cite several tags as [m1, m3]", [], [], []),
    ],
)
def test_check_credits(rt, answer, valid, invented, earlier):
    result = rt.check_credits(answer, _session_tags(rt))
    assert (result["valid"], result["invented"], result["earlier"]) == (valid, invented, earlier)
    assert result["available"] == 2


def test_credit_state_round_trip(rt, tmp_path):
    assert not rt.has_credited(tmp_path, SESSION_KEY)
    rt.record_credit(tmp_path, SESSION_KEY, {"valid": []})
    assert not rt.has_credited(tmp_path, SESSION_KEY)  # an invented-only turn doesn't count
    rt.record_credit(tmp_path, SESSION_KEY, {"valid": ["m1"]})
    assert rt.has_credited(tmp_path, SESSION_KEY)
    assert (tmp_path / "session-state" / SESSION_KEY / "credit.json").is_file()


@pytest.mark.parametrize("key", ["../escape", "..", "", "a/b"])
def test_unsafe_session_keys_write_nothing(rt, tmp_path, key):
    state = tmp_path / "state"
    tags = rt.MemoryTags()
    tags.add("memory", "Cognee memory", "x")
    tags.save(state, key)
    rt.begin_turn(state, key)
    rt.record_credit(state, key, {"valid": ["m1"]})
    assert not any(tmp_path.rglob("*.json"))


def test_idle_session_folders_are_pruned(rt, tmp_path):
    import os
    import time

    for key in ("old", "fresh"):
        tags = rt.MemoryTags()
        tags.add("memory", "Cognee memory", key)
        tags.save(tmp_path, key)
    old = tmp_path / "session-state" / "old"
    stale = time.time() - rt.SESSION_STATE_MAX_AGE_SECONDS - 60
    for path in (old / "tags.json", old):
        os.utime(path, (stale, stale))
    assert rt.prune_session_state(tmp_path) == 1
    assert not old.exists()
    assert (tmp_path / "session-state" / "fresh" / "tags.json").is_file()


# ── the recall hook ────────────────────────────────────────────────────────


@pytest.fixture
def lookup(suite, hook_module, monkeypatch, rt):
    # ``rt`` first: loading a module scrubs COGNEE_* from the environment.
    if suite.name not in ("claude-code", "codex"):
        pytest.skip(f"{suite.name}: recall hook does not credit memory")
    module = hook_module(suite, "session-context-lookup.py")
    monkeypatch.setattr(module, "get_session_key", lambda: SESSION_KEY)
    monkeypatch.setenv("COGNEE_PLUGIN_CITE_MEMORY", "on")  # Codex starts with it off
    return module


def _context(run) -> str:
    return run.output["hookSpecificOutput"]["additionalContext"]


def _state_dir(lookup):
    return lookup._plugin_common._PLUGIN_DIR


def _saved(rt, lookup):
    return rt.MemoryTags.load(_state_dir(lookup), SESSION_KEY)


def test_hit_is_tagged_and_carries_the_first_credit_instruction(lookup, monkeypatch, rt):
    ctx = _context(drive_recall(lookup, monkeypatch, recall=HIT))
    assert "[m1 · Codex session]" in ctx
    assert "Cognee found 5 memories for this prompt" in ctx
    assert "> 🧠 Cognee memory [mN]:" in ctx
    assert "first Cognee credit in this session" in ctx
    assert [r["tag"] for r in _saved(rt, lookup).records] == ["m1", "m2", "m3", "m4", "m5"]


def test_the_next_prompt_reuses_the_tags_of_memories_seen_before(lookup, monkeypatch, rt):
    drive_recall(lookup, monkeypatch, recall=HIT)
    ctx = _context(drive_recall(lookup, monkeypatch, recall=HIT))
    assert "[m1 · Codex session]" in ctx and "[m6" not in ctx
    assert "Cognee found 5 memories for this prompt" in ctx
    assert len(_saved(rt, lookup).records) == 5


def test_an_earlier_turn_keeps_its_tag_by_server_id(lookup, monkeypatch, rt):
    turn = {"source": "session", "qa_id": "qa-7", "question": "q", "answer": "a"}
    edited = dict(turn, answer="a, with feedback")
    first = _context(drive_recall(lookup, monkeypatch, recall={"session_context": [turn]}))
    second = _context(drive_recall(lookup, monkeypatch, recall={"session_context": [edited]}))
    assert "[m1 · earlier session turn]" in first
    assert "[m1 · earlier session turn]" in second


def test_after_a_credit_the_short_instruction(lookup, monkeypatch, rt):
    rt.record_credit(_state_dir(lookup), SESSION_KEY, {"valid": ["m1"]})
    ctx = _context(drive_recall(lookup, monkeypatch, recall=HIT))
    assert "> 🧠 Cognee memory [mN]:" in ctx
    assert "first Cognee credit" not in ctx


def test_empty_recall_injects_nothing_and_leaves_no_tags_for_the_turn(lookup, monkeypatch, rt):
    drive_recall(lookup, monkeypatch, recall=HIT)
    run = drive_recall(lookup, monkeypatch, recall={})
    assert _context(run) == ""
    assert run.output["hookSpecificOutput"].get("systemMessage") or run.output.get("systemMessage")
    assert rt.check_credits("", _saved(rt, lookup))["available"] == 0


def test_a_recall_that_returns_early_still_starts_a_new_turn(lookup, monkeypatch, rt):
    """Server down, no session or a timeout: the hook returns before tagging, and
    the previous turn's tags must not pass for this turn's."""
    import asyncio

    drive_recall(lookup, monkeypatch, recall=HIT)
    monkeypatch.setattr(lookup, "_load_session_id", lambda: "")
    assert asyncio.run(lookup._run("next prompt")) is None
    result = rt.check_credits("> 🧠 Cognee memory [m2]: …", _saved(rt, lookup))
    assert (result["valid"], result["earlier"], result["available"]) == (["m2"], ["m2"], 0)


def test_opt_out(lookup, monkeypatch, rt):
    monkeypatch.setenv("COGNEE_PLUGIN_CITE_MEMORY", "off")
    ctx = _context(drive_recall(lookup, monkeypatch, recall=HIT))
    assert "[m1" not in ctx and "🧠" not in ctx
    assert PASSAGE_CODEX in ctx  # memory itself is unchanged


def test_default_per_host(suite, lookup, monkeypatch):
    """On by default in Claude Code; off in Codex, whose terminal shows the injected
    instruction, until it has been tried live."""
    monkeypatch.delenv("COGNEE_PLUGIN_CITE_MEMORY")
    ctx = _context(drive_recall(lookup, monkeypatch, recall=HIT))
    assert ("[m1 ·" in ctx) is suite.cites_memory_by_default


# ── the Stop hook ──────────────────────────────────────────────────────────


@pytest.fixture
def stop(suite, hook_module, monkeypatch, rt):
    if suite.name not in ("claude-code", "codex"):
        pytest.skip(f"{suite.name}: no memory credit check")
    module = hook_module(suite, "store-to-session.py")
    events: list = []
    monkeypatch.setenv("COGNEE_PLUGIN_CITE_MEMORY", "on")
    monkeypatch.setattr(module, "get_session_key", lambda: SESSION_KEY)
    monkeypatch.setattr(module, "hook_log", lambda e, d=None: events.append((e, d or {})))
    module.events = events
    return module


def _turn(rt, stop, answer, this_turn=("m1", "m2"), earlier=()):
    import _plugin_common

    state = _plugin_common._PLUGIN_DIR
    _session_tags(rt, this_turn, earlier).save(state, SESSION_KEY)
    stop._check_memory_credit({"last_assistant_message": answer})
    return state


def test_stop_logs_valid_and_invented_credits(stop, rt):
    state = _turn(rt, stop, "ok\n> 🧠 Cognee memory [m2, m7]: you chose the server.")
    assert stop.events == [
        (
            "memory_credit",
            {
                "cited": ["m2", "m7"],
                "valid": ["m2"],
                "invented": ["m7"],
                "earlier": [],
                "available": 2,
            },
        )
    ]
    assert rt.has_credited(state, SESSION_KEY)


def test_stop_checks_an_earlier_turns_tag_against_its_own_memory(stop, rt):
    _turn(rt, stop, "> 🧠 Cognee memory [m3]: …", this_turn=(), earlier=("m3",))
    assert stop.events[0][1]["valid"] == ["m3"]
    assert stop.events[0][1]["earlier"] == ["m3"]


def test_stop_without_credit_logs_an_uncredited_turn(stop, rt):
    state = _turn(rt, stop, "a plain answer")
    assert stop.events[0][1]["cited"] == []
    assert not rt.has_credited(state, SESSION_KEY)


def test_stop_is_silent_when_nothing_was_injected_or_cited(stop, rt):
    _turn(rt, stop, "a plain answer", this_turn=())
    assert stop.events == []


def test_stop_logs_a_citation_on_a_turn_without_memory_as_invented(stop, rt):
    _turn(rt, stop, "> 🧠 Cognee memory [m1]: …", this_turn=())
    assert stop.events[0][1]["invented"] == ["m1"]


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
        "🧠 This conversation was sent to Cognee's long-term memory when you left at "
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

    monkeypatch.setenv("COGNEE_PLUGIN_CITE_MEMORY", "on")
    monkeypatch.setattr(config, "get_session_id", lambda *a, **k: "claude_resumed")
    return module


RESUME = {"source": "resume"}


def test_resume_shows_the_notice_once(start):
    import _plugin_common

    _plugin_common.record_improve_success("claude_resumed", "agent_sessions", "final")
    out = start._apply_sync_notice(
        {"hookSpecificOutput": {"hookEventName": "SessionStart"}}, RESUME
    )
    # Top level is what Claude Code shows the user; additionalContext reaches the agent.
    assert out["systemMessage"].startswith("🧠 This conversation was sent to Cognee")
    assert out["hookSpecificOutput"]["additionalContext"] == out["systemMessage"]
    again = start._apply_sync_notice({}, RESUME)
    assert again == {}


@pytest.mark.parametrize("source", ["compact", "startup", "clear", ""])
def test_only_a_resume_shows_the_notice(start, source):
    """A compaction keeps the session too, but lands mid-task."""
    import _plugin_common

    _plugin_common.record_improve_success("claude_resumed", "agent_sessions", "idle")
    assert start._apply_sync_notice({}, {"source": source}) == {}
    assert start._apply_sync_notice({}, RESUME)["systemMessage"]  # still due


def test_a_new_chat_shows_nothing(start):
    assert start._apply_sync_notice({}, RESUME) == {}
