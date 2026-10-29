"""Crediting Cognee memory in the answer (memory_credit), as the Cursor and Codex plugins do.

Recalled items are tagged ``[mN · origin]`` with one numbering per conversation,
an instruction asks the agent to end an answer that used them with the Cognee
logo in braille and what it remembered, and that credit is noted and stripped
before the turn is stored.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from _char_helpers import fake_backend, make_provider  # noqa: E402
from cognee_integration_hermes import memory_credit  # noqa: E402

GRAPH_CONTEXT = (
    "## Relevant passages\n"
    "Session ID: codex_abc\n\nQuestion: what editor?\nAnswer: Helix."
    "\n---\n"
    "# Session learning 2026-10-03 (session claude_x)\n\nUse the server, not the CLI."
    "\n\n## Relevant entities\n### mika (person)\n- Mika likes tennis."
    "\n\n## Related facts\n- Mika plays on Saturdays."
)


@pytest.fixture
def cite_on(monkeypatch):
    monkeypatch.setenv("COGNEE_PLUGIN_CITE_MEMORY", "on")


def test_each_item_gets_a_tag_and_its_origin():
    tags = memory_credit.MemoryTags()
    tags.begin_turn()
    out = tags.tag_graph(GRAPH_CONTEXT)
    assert "[m1 · Codex session]" in out
    assert "[m2 · Claude Code session learning, 2026-10-03]" in out
    assert "## Relevant entities [m3 · knowledge graph entities]" in out
    assert "## Related facts [m4 · knowledge graph facts]" in out
    assert tags.found == ["m1", "m2", "m3", "m4"]


def test_a_memory_recalled_again_keeps_its_tag():
    tags = memory_credit.MemoryTags()
    tags.begin_turn()
    tags.tag_graph(GRAPH_CONTEXT)
    tags.begin_turn()
    assert tags.found == []
    again = tags.label("facts", "knowledge graph facts", "- Mika plays on Saturdays.")
    new = tags.label("recall", "Cognee recall", "something new")
    assert again == "[m4 · knowledge graph facts]"
    assert new == "[m5 · Cognee recall]"


def test_check_separates_valid_invented_and_earlier_tags():
    tags = memory_credit.MemoryTags()
    tags.begin_turn()
    tags.label("recall", "x", "first")
    tags.begin_turn()
    tags.label("recall", "x", "second")
    result = tags.check("Done.\n> 🧠 Cognee memory [m2, m1, m9]: you said so.")
    assert result == {
        "credited": True,
        "cited": ["m2", "m1", "m9"],
        "valid": ["m2", "m1"],
        "invented": ["m9"],
        "earlier": ["m1"],
        "available": 1,
    }
    assert tags.credited is True


def test_code_that_looks_like_a_tag_is_not_a_citation():
    tags = memory_credit.MemoryTags()
    tags.begin_turn()
    tags.label("recall", "x", "first")
    assert tags.check("print(arr[m1])")["cited"] == []
    assert tags.credited is False


LOGO_CREDIT = (
    "Mika likes tennis.\n\n"
    f"> {memory_credit.LOGOTYPE[0]} Mika likes tennis, based on agent_sessions.\\\n"
    f"> {memory_credit.LOGOTYPE[1]}"
)


def test_the_instruction_shows_both_logo_rows_and_the_source():
    text = memory_credit.credit_instruction(False, 1, "agent_sessions")
    first_row = f"> {memory_credit.LOGOTYPE[0]} <what you remembered>, based on agent_sessions."
    assert first_row + "\\\n" in text
    assert f"> {memory_credit.LOGOTYPE[1]}" in text
    assert 'do not write the tags or the words "Cognee memory"' in text


def test_the_logo_line_counts_as_a_credit():
    tags = memory_credit.MemoryTags()
    tags.begin_turn()
    tags.label("recall", "x", "Mika likes tennis.")
    assert memory_credit.has_credit(LOGO_CREDIT)
    assert tags.check(LOGO_CREDIT)["credited"] is True
    assert tags.credited is True


def test_braille_without_text_or_quote_is_not_a_credit():
    assert not memory_credit.has_credit(f"> {memory_credit.LOGOTYPE[0]}")
    assert not memory_credit.has_credit(f"see {memory_credit.LOGOTYPE[0]} here")


def test_both_logo_rows_are_stripped_and_the_answer_kept():
    assert memory_credit.strip_credit_lines(LOGO_CREDIT) == "Mika likes tennis."


def test_older_credit_lines_are_stripped_too():
    answer = "Mika likes tennis.\n\n> 🧠 Cognee memory [m3]: from a Codex session."
    assert memory_credit.strip_credit_lines(answer) == "Mika likes tennis."
    assert memory_credit.strip_credit_lines("x = arr[m1]") == "x = arr[m1]"


def test_the_first_credit_asks_how_it_connects():
    assert memory_credit.FIRST_CREDIT_ADDENDUM in memory_credit.credit_instruction(True, 2)
    assert memory_credit.FIRST_CREDIT_ADDENDUM not in memory_credit.credit_instruction(False, 1)
    assert "found 1 memory " in memory_credit.credit_instruction(False, 1)


def test_off_switch(monkeypatch):
    monkeypatch.setenv("COGNEE_PLUGIN_CITE_MEMORY", "off")
    assert memory_credit.credit_enabled() is False
    monkeypatch.delenv("COGNEE_PLUGIN_CITE_MEMORY")
    assert memory_credit.credit_enabled() is True  # on by default, as in Cursor


def test_auto_recall_carries_tags_and_the_instruction(cite_on):
    with fake_backend() as fake:
        fake.results["recall"] = [{"text": "Mika likes tennis.", "source": "graph"}]
        provider = make_provider()
        provider.queue_prefetch("what does mika like")
        out = provider.prefetch("what does mika like")
    assert "[m1 · Cognee memory]" in out
    assert "Crediting Cognee memory: Cognee found 1 memory" in out
    assert memory_credit.FIRST_CREDIT_ADDENDUM in out


def test_nothing_recalled_means_no_instruction(cite_on):
    with fake_backend() as fake:
        fake.results["recall"] = []
        provider = make_provider()
        provider.queue_prefetch("q")
        assert provider.prefetch("q") == ""


def test_recall_tool_results_are_tagged(cite_on):
    with fake_backend() as fake:
        fake.results["recall"] = [{"text": "Mika likes tennis.", "source": "graph"}]
        provider = make_provider()
        out = json.loads(provider.handle_tool_call("cognee_recall", {"query": "mika"}))
    assert out["results"][0]["tag"] == "[m1 · Cognee recall]"
    assert memory_credit.LOGOTYPE[0] in out["credit"] and memory_credit.LOGOTYPE[1] in out["credit"]


def test_the_stored_answer_has_no_credit_line(cite_on):
    with fake_backend() as fake:
        provider = make_provider()
        provider.sync_turn("what does mika like?", LOGO_CREDIT)
        assert fake.wait("remember_session")
        stored = json.dumps(
            [fake.kwargs_for(name) for name in ("remember_session", "remember_entry")],
            ensure_ascii=False,
        )
    assert "Mika likes tennis." in stored
    assert memory_credit.LOGOTYPE[0] not in stored and memory_credit.LOGOTYPE[1] not in stored
