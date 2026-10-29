"""Crediting Cognee memory in the answer (memory_credit), as the Cursor and Codex plugins do.

Recalled items are tagged ``[mN · origin]`` with one numbering per conversation,
an instruction asks the agent to end an answer that used them with a plain
``> Cognee: …`` line, the ``transform_llm_output`` hook draws it as a purple
COGNEE wordmark in Hermes Desktop, and the credit is noted and stripped before
the turn is stored.
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


PLAIN = "Mika likes Yuji.\n\n> Cognee: Mika likes Yuji and volleyball, based on agent_sessions."
RENDERED = (
    "Mika likes Yuji.\n\n"
    f"{memory_credit.WORDMARK[:-1]}\\enspace$"
    " Mika likes Yuji and volleyball, based on agent_sessions."
)
OLD_BRAILLE = (
    "Mika likes Yuji.\n\n"
    f"> {memory_credit.LOGOTYPE[0]} Mika likes Yuji, based on agent_sessions.\\\n"
    f"> {memory_credit.LOGOTYPE[1]}"
)


def test_the_instruction_asks_for_a_plain_line_with_the_source():
    text = memory_credit.credit_instruction(False, 1, "agent_sessions")
    assert '"> Cognee:"' in text
    assert "> Cognee: you prefer the server over the CLI, based on dataset agent_sessions." in text
    assert 'Write "dataset agent_sessions" exactly so.' in text
    assert "Do not copy the example" in text
    assert "\u2800" not in text and not any("\u2800" <= c <= "\u28ff" for c in text)


def test_desktop_gets_the_pixel_logo(cite_on):
    logo = memory_credit.WORDMARK
    assert logo.startswith("$\\color{#BC9BFF}{") and logo.endswith("}$")
    assert logo.count("\\rule[") > 20
    assert memory_credit.render_credit(PLAIN, "desktop") == RENDERED


def test_the_pixel_logo_follows_the_grid():
    # One rule per vertical run of squares; a single-letter check keeps it readable.
    # E: a full left column, then squares in rows 0, 2 and 4 (raised 0.8, 0.4, 0).
    logo = memory_credit.pixel_logo("E")
    assert logo == (
        "$\\color{#BC9BFF}{\\rule[0em]{0.2em}{1em}"
        "\\rule[0.8em]{0.2em}{0.2em}\\kern-0.2em"
        "\\rule[0.4em]{0.2em}{0.2em}\\kern-0.2em"
        "\\rule[0em]{0.2em}{0.2em}}$"
    )


def test_the_earlier_bold_wordmark_is_still_stripped():
    old = "Mika likes Yuji.\n\n> $\\color{#9F7AEA}{\\textbf{COGNEE}}$ Mika likes Yuji, based on x."
    assert memory_credit.has_credit(old)
    assert memory_credit.strip_credit_lines(old) == "Mika likes Yuji."


@pytest.mark.parametrize("platform", ["telegram", "discord", ""])
def test_other_platforms_keep_the_plain_line_for_now(platform):
    assert memory_credit.render_credit(PLAIN, platform) is None


def test_nothing_to_draw_without_a_credit_line_or_with_credit_off(monkeypatch):
    assert memory_credit.render_credit("Mika likes Yuji.", "desktop") is None
    monkeypatch.setenv("COGNEE_PLUGIN_CITE_MEMORY", "off")
    assert memory_credit.render_credit(PLAIN, "desktop") is None


def test_every_credit_form_counts_and_is_stripped():
    for answer in (PLAIN, RENDERED, OLD_BRAILLE):
        assert memory_credit.has_credit(answer), answer
        assert memory_credit.strip_credit_lines(answer) == "Mika likes Yuji."


def test_a_bare_label_is_not_a_credit():
    assert not memory_credit.has_credit("> Cognee:")
    assert not memory_credit.has_credit("Cognee: is a memory layer")


def test_the_credit_marks_the_session_as_credited():
    tags = memory_credit.MemoryTags()
    tags.begin_turn()
    tags.label("recall", "x", "Mika likes Yuji.")
    assert tags.check(RENDERED)["credited"] is True
    assert tags.credited is True


def test_older_credit_lines_are_stripped_too():
    answer = "Mika likes tennis.\n\n> 🧠 Cognee memory [m3]: from a Codex session."
    assert memory_credit.strip_credit_lines(answer) == "Mika likes tennis."
    assert memory_credit.strip_credit_lines("x = arr[m1]") == "x = arr[m1]"


def test_the_plugin_registers_the_hook(cite_on):
    from cognee_integration_hermes import register, transform_llm_output

    registered = {}

    class Ctx:
        def register_memory_provider(self, provider):
            registered["provider"] = provider

        def register_hook(self, name, callback):
            registered[name] = callback

    register(Ctx())
    assert registered["transform_llm_output"] is transform_llm_output
    assert transform_llm_output(response_text=PLAIN, platform="desktop", session_id="s") == RENDERED


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
    assert '"> Cognee:"' in out["credit"]


def test_the_stored_answer_has_no_credit_line(cite_on):
    with fake_backend() as fake:
        provider = make_provider()
        provider.sync_turn("what does mika like?", RENDERED)
        assert fake.wait("remember_session")
        stored = json.dumps(
            [fake.kwargs_for(name) for name in ("remember_session", "remember_entry")],
            ensure_ascii=False,
        )
    assert "Mika likes Yuji." in stored
    assert "COGNEE" not in stored and "Cognee:" not in stored


def test_the_plugin_root_registers_the_hook_too():
    # Directory installs (the catalog) load the plugin root's __init__.py, not the package.
    import importlib.util

    spec = importlib.util.spec_from_file_location("hermes_plugin_root", ROOT / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    registered = {}

    class Ctx:
        def register_memory_provider(self, provider):
            registered["provider"] = provider

        def register_hook(self, name, callback):
            registered[name] = callback

    module.register(Ctx())
    assert "provider" in registered and "transform_llm_output" in registered


def test_the_drawn_line_is_not_a_blockquote_and_leaves_space(cite_on):
    # Half an em after the logo, about two spaces.
    # Desktop draws a blockquote with a quotation mark, and markdown collapses spaces.
    line = memory_credit.render_credit(PLAIN, "desktop").splitlines()[-1]
    assert line.startswith("$\\color{#BC9BFF}{")
    assert "}\\enspace$ Mika likes" in line
    assert not memory_credit.has_credit("Cognee: is a memory layer")


def test_the_terminal_ui_gets_the_braille_logo(cite_on):
    # The TUI shows each line as written and draws "> " quotes with a bar.
    out = memory_credit.render_credit(PLAIN, "tui")
    top, bottom = memory_credit.LOGOTYPE
    assert out.endswith(
        f"{top}  Mika likes Yuji and volleyball, based on agent_sessions.  \n{bottom}"
    )
    assert "> " not in out


def test_a_braille_credit_counts_and_both_rows_are_stripped(cite_on):
    out = memory_credit.render_credit(PLAIN, "tui")
    assert memory_credit.has_credit(out)
    assert memory_credit.strip_credit_lines(out) == "Mika likes Yuji."
    # A logo row alone, or braille inside a sentence, is not a credit.
    assert not memory_credit.has_credit(memory_credit.LOGOTYPE[0])
    assert not memory_credit.has_credit(f"see {memory_credit.LOGOTYPE[0]} here")


def test_the_streaming_classic_cli_gets_the_logo_appended(cite_on, monkeypatch):
    # A streamed answer is already on screen; Hermes shows only an appended suffix
    # cleanly (a replaced answer would be printed a second time).
    monkeypatch.setattr(memory_credit, "_cli_streams", lambda: True)
    out = memory_credit.render_credit(PLAIN, "cli")
    top, bottom = memory_credit.LOGOTYPE
    assert out == f"{PLAIN}\n{top}\n{bottom}"
    assert memory_credit.strip_credit_lines(out) == "Mika likes Yuji."


def test_the_non_streaming_classic_cli_gets_the_line_replaced(cite_on, monkeypatch):
    monkeypatch.setattr(memory_credit, "_cli_streams", lambda: False)
    out = memory_credit.render_credit(PLAIN, "cli")
    assert out == memory_credit.render_credit(PLAIN, "tui")
    assert "> Cognee:" not in out


def test_streaming_defaults_to_on_outside_hermes():
    # No hermes_cli to ask (tests, a bare install): assume the default, streaming.
    assert memory_credit._cli_streams() is True
