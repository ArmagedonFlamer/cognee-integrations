"""Crediting Cognee memory in the answer, as the Claude Code, Codex and Cursor plugins do.

Every recalled item the agent is given gets a short tag (``m1``, ``m2``…) and a
label saying where it came from, so it can tell Cognee's items from everything
else it knows. An instruction above the memory asks the agent to end an answer
that used them with the Cognee logo, drawn in braille, and what it remembered:

    > ⡏⠁⡏⡇⡏⡁⡧⣸⢸⠅⡯ Mika likes tennis, based on agent_sessions.\
    > ⠓⠂⠓⠃⠓⠃⠃⠙⠘⠂⠓

That is the Cursor plugin's line with its logo link swapped for braille: Hermes
Desktop draws an icon only for the hosts in its own brand list, which has no
cognee.ai, and the terminal shows a link as text, while braille shows the same
everywhere. Tags are numbered across the session and a memory recalled again
keeps its tag.

When the turn is stored, the answer's credit is noted and stripped, so it is
never stored back into memory as if it were part of the answer.

The provider lives for the whole session, so the tags live in memory: no state
files, unlike the hook-based plugins. ``COGNEE_PLUGIN_CITE_MEMORY=off`` turns it
off.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading

from .recall_text import (
    ENTITIES_HEADING,
    FACTS_HEADING,
    PASSAGE_SEPARATOR,
    PASSAGES_HEADING,
    passage_session_id,
    split_passages,
    split_sections,
)

ENV_CITE_MEMORY = "COGNEE_PLUGIN_CITE_MEMORY"

#: "COGNEE" six dots tall, as two braille rows (the Claude Code terminal
#: logotype, one dot row taller). The markdown line break after the first row
#: keeps the rows together in a rendered chat.
LOGOTYPE = ("⡏⠁⡏⡇⡏⡁⡧⣸⢸⠅⡯", "⠓⠂⠓⠃⠓⠃⠃⠙⠘⠂⠓")
#: The logo's colour where the chat renders it: Hermes Desktop renders KaTeX
#: (``$…$``), and ``\color`` is the only way to colour text there. A mid purple
#: between the Cognee wordmark's light and dark shades, readable on either theme.
LOGO_COLOR = "#9F7AEA"
#: Platforms whose chat renders inline math. Elsewhere (the terminal, messaging
#: apps) the KaTeX would show as source, so the logo stays plain braille.
COLOR_PLATFORMS = frozenset({"desktop"})

FIRST_CREDIT_ADDENDUM = (
    "This would be the first Cognee credit in this session, so after what you remembered "
    "also say in a few words how it connects to the earlier work."
)


def logo_rows(colored: bool = False) -> tuple[str, str]:
    """The logo's two rows, wrapped in KaTeX colour when the chat renders it."""
    if not colored:
        return LOGOTYPE
    return tuple("$\\color{%s}{\\text{%s}}$" % (LOGO_COLOR, row) for row in LOGOTYPE)


def _credit_lines(source: str, colored: bool) -> str:
    top, bottom = logo_rows(colored)
    return f"> {top} <what you remembered>, based on {source}.\\\n> {bottom}"


def _copy_rule(colored: bool) -> str:
    if colored:
        return (
            "Copy both lines exactly as shown, including the $…$ around each braille row "
            "(it colours the logo) and the backslash ending the first line: together they "
            "are the Cognee logo."
        )
    return (
        "Copy both braille rows exactly, including the backslash ending the first line: "
        "together they are the Cognee logo."
    )


def credit_instruction(
    first_in_session: bool, found: int, source: str = "", colored: bool = False
) -> str:
    noun = "1 memory" if found == 1 else f"{found} memories"
    text = (
        f"Crediting Cognee memory: Cognee found {noun} for this prompt, each tagged below "
        "as [mN · where it came from]. A memory recalled again in this session keeps its "
        "tag. If your answer uses information from a tagged item, end the answer with these "
        "two lines:\n"
        f"{_credit_lines(source or 'Cognee memory', colored)}\n"
        f"{_copy_rule(colored)} The logo names Cognee, so do not write the tags "
        'or the words "Cognee memory" in these lines. Credit only items Cognee tagged, and '
        "only items your answer actually used. Never credit Cognee for what you know from "
        "project instruction files, your own memory files, the code, or this conversation. "
        "If no tagged item was used, do not mention memory."
    )
    return f"{text} {FIRST_CREDIT_ADDENDUM}" if first_in_session else text


def tool_credit_instruction(source: str = "", colored: bool = False) -> str:
    """The same instruction for results of the agent's own ``cognee_recall`` call."""
    return (
        "Each result is tagged [mN · where it came from]. If your answer uses one, end it with "
        "these two lines:\n"
        f"{_credit_lines(source or 'Cognee memory', colored)}\n"
        f"{_copy_rule(colored)} "
        'Do not write the tags or the words "Cognee memory" in them, and credit only results '
        "your answer actually used."
    )


#: A credit: a quoted line that opens with the logo's first row and goes on to
#: say something. The logo alone, or braille elsewhere, is not one.
_KATEX_OPEN = r"(?:\$\\color\{[^}\n]*\}\{\\text\{)?"
_KATEX_CLOSE = r"(?:\}\}\$)?"
_CREDIT_RE = re.compile(rf"^[ \t]*>[ \t]*{_KATEX_OPEN}{LOGOTYPE[0]}{_KATEX_CLOSE}[ \t]+\S", re.M)
#: Tags cited the way the Claude Code and Codex plugins ask (``Cognee memory
#: [m2]``); ``arr[m1]`` in code is not a citation.
_CITATION_RE = re.compile(r"Cognee memory\s*\[(m\d+(?:\s*,\s*m\d+)*)\]")
#: Lines cut before storing: a quoted line led by braille (either logo row, any
#: logotype version) and the older ``🧠 Cognee memory [mN]`` line.
_STRIP_RES = (
    re.compile(rf"^[ \t]*>[ \t]*{_KATEX_OPEN}[\u2800-\u28ff]{{6,}}[^\n]*(?:\n|$)", re.M),
    re.compile(r"^[ \t]*(?:>[ \t]*)?🧠?[ \t]*Cognee memory[ \t]*\[m\d+[^\n]*(?:\n|$)", re.M),
)
_LEARNING_DATE_RE = re.compile(r"^# Session learning\W+(\d{4}-\d{2}-\d{2})", re.M)
_AGENTS = {
    "claude": "Claude Code",
    "codex": "Codex",
    "cursor": "Cursor",
    "antigravity": "Antigravity",
    "openclaw": "OpenClaw",
    "hermes": "Hermes",
}


def credit_enabled(default: bool = True) -> bool:
    """``COGNEE_PLUGIN_CITE_MEMORY`` decides when set; on otherwise."""
    raw = os.environ.get(ENV_CITE_MEMORY, "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off"}


def passage_origin(passage: str) -> str:
    """Where a recalled passage came from, in a few words for its tag."""
    session_id = passage_session_id(passage)
    if not session_id:
        return "document"
    prefix = session_id.split("_", 1)[0].lower() if "_" in session_id else ""
    agent = _AGENTS.get(prefix, "agent")
    date = _LEARNING_DATE_RE.search(passage.lstrip()[:400])
    if date:
        return f"{agent} session learning, {date.group(1)}"
    return f"{agent} session"


def has_credit(answer: str) -> bool:
    """Whether an answer carries a credit, the logo line or an older tag line."""
    return bool(answer) and bool(_CREDIT_RE.search(answer) or _CITATION_RE.search(answer))


def strip_credit_lines(answer: str) -> str:
    """The answer without its credit lines, for storing."""
    if not answer:
        return answer
    for pattern in _STRIP_RES:
        answer = pattern.sub("", answer)
    return answer.rstrip()


def _memory_id(kind: str, text: str) -> str:
    """What makes two recalled items the same memory: their text."""
    normal = " ".join(str(text or "").split())
    return f"{kind}:{hashlib.sha1(normal.encode('utf-8')).hexdigest()[:16]}"


class MemoryTags:
    """Hands out ``m1``, ``m2``… across a session and remembers what each one tagged.

    Call :meth:`begin_turn` when a prompt's recall starts; :meth:`label` then
    registers an item and counts it as given on this turn. Thread-safe: the
    prefetch thread labels while the turn sync checks.
    """

    SNIPPET_CHARS = 200

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.turn = 0
        self.records: list[dict] = []
        self._by_id: dict[str, dict] = {}
        self.found: list[str] = []  # this turn's tags, in the order first given
        self.credited = False  # whether an answer in this session credited memory

    def begin_turn(self) -> None:
        with self._lock:
            self.turn += 1
            self.found = []

    def label(self, kind: str, origin: str, text: str) -> str:
        """``[mN · origin]`` for an item, registering it as given on this turn."""
        key = _memory_id(kind, text)
        with self._lock:
            record = self._by_id.get(key)
            if record is None:
                record = {
                    "tag": f"m{len(self.records) + 1}",
                    "kind": kind,
                    "origin": origin,
                    "snippet": " ".join(str(text or "").split())[: self.SNIPPET_CHARS],
                }
                self.records.append(record)
                self._by_id[key] = record
            record["turn"] = self.turn
            tag = record["tag"]
            if tag not in self.found:
                self.found.append(tag)
        return f"[{tag} · {origin}]" if origin else f"[{tag}]"

    def tag_graph(self, context: str) -> str:
        """Tag a graph-memory context: each passage, the entities and the facts.

        An unsectioned context (an older server, another retriever) is tagged
        as one item.
        """
        sections = split_sections(context)
        if (
            sections["passages"] is None
            and sections["entities"] is None
            and sections["facts"] is None
        ):
            if not (context or "").strip():
                return context
            return f"{self.label('memory', 'Cognee memory', context)}\n{context}"
        blocks: list[str] = []
        preamble = (sections["preamble"] or "").strip()
        if preamble:
            blocks.append(f"{self.label('memory', 'Cognee memory', preamble)}\n{preamble}")
        if sections["passages"] is not None:
            tagged = [
                f"{self.label('passage', passage_origin(p), p)}\n{p}"
                for p in split_passages(sections["passages"])
            ]
            if tagged:
                blocks.append(PASSAGES_HEADING + "\n" + PASSAGE_SEPARATOR.join(tagged))
        for key, heading, kind, origin in (
            ("entities", ENTITIES_HEADING, "entities", "knowledge graph entities"),
            ("facts", FACTS_HEADING, "facts", "knowledge graph facts"),
        ):
            body = (sections[key] or "").strip("\n")
            if body.strip():
                blocks.append(f"{heading} {self.label(kind, origin, body)}\n{body}")
        return "\n\n".join(blocks).strip()

    def check(self, answer: str) -> dict:
        """What the answer credited: the logo line, and any tags it cites.

        Returns ``{"credited", "cited", "valid", "invented", "earlier",
        "available"}``. The logo line carries no tags, so ``credited`` says
        whether it is there; the tag lists cover an answer citing tags the way
        the Claude Code and Codex plugins ask. Either marks the session as
        credited, which drops the first-credit addendum from later turns.
        """
        logo = bool(_CREDIT_RE.search(answer or ""))
        cited: list[str] = []
        for group in _CITATION_RE.findall(answer or ""):
            for tag in re.split(r"\s*,\s*", group):
                if tag not in cited:
                    cited.append(tag)
        with self._lock:
            known = {r["tag"]: r for r in self.records}
            this_turn = {t for t, r in known.items() if r.get("turn") == self.turn}
            valid = [t for t in cited if t in known]
            if valid or (logo and self.records):
                self.credited = True
        return {
            "credited": logo or bool(valid),
            "cited": cited,
            "valid": valid,
            "invented": [t for t in cited if t not in known],
            "earlier": [t for t in valid if t not in this_turn],
            "available": len(this_turn),
        }
