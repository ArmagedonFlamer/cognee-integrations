"""Crediting Cognee memory in the answer, as the Claude Code, Codex and Cursor plugins do.

Every recalled item the agent is given gets a short tag (``m1``, ``m2``…) and a
label saying where it came from. An instruction above the memory asks the agent
to end an answer that used memory with one ``🧠 Cognee memory [mN]: …`` line.
Tags are numbered across the session and a memory recalled again keeps its tag,
so a tag names one memory however many turns later it is cited.

When the turn is stored, the answer's credit line is checked against the tags
handed out (a cited tag never handed out is logged as invented) and stripped, so
the credit is never stored back into memory as if it were part of the answer.

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

CREDIT_INSTRUCTION = (
    "Crediting Cognee memory: Cognee found {found} for this prompt, each tagged below "
    "as [mN · where it came from]. A memory recalled again in this session keeps its "
    "tag. If your answer uses information from a tagged item, end the answer with one "
    "line:\n"
    "> 🧠 Cognee memory [mN]: <what you remembered, and where or when it came from, "
    "as the tag says>\n"
    "Cite several tags as [m1, m3]. Cite only tags Cognee gave you, and only items your "
    "answer actually used. Never credit Cognee for what you know from project "
    "instruction files, your own memory files, the code, or this conversation. If no "
    "tagged item was used, do not mention memory."
)
FIRST_CREDIT_ADDENDUM = (
    "This would be the first Cognee credit in this session, so in that line also say in "
    "a few words how it connects to the earlier work."
)
#: The same instruction for results of the agent's own ``cognee_recall`` call.
TOOL_CREDIT_INSTRUCTION = (
    "Each result is tagged [mN · where it came from]. If your answer uses one, end it "
    "with one line: > 🧠 Cognee memory [mN]: <what you remembered, and where or when it "
    "came from>. Cite only these tags, and only results your answer actually used."
)

#: Only the credit line counts: ``arr[m1]`` in code, or the instruction's own
#: example quoted back, is not a citation.
_CITATION_RE = re.compile(r"Cognee memory\s*\[(m\d+(?:\s*,\s*m\d+)*)\]")
#: A whole credit line, with or without the quote marker, for stripping.
_CREDIT_LINE_RE = re.compile(r"^[ \t]*(?:>[ \t]*)?🧠?[ \t]*Cognee memory[ \t]*\[m\d+[^\n]*$", re.M)
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


def credit_instruction(first_in_session: bool, found: int) -> str:
    noun = "1 memory" if found == 1 else f"{found} memories"
    text = CREDIT_INSTRUCTION.format(found=noun)
    return f"{text} {FIRST_CREDIT_ADDENDUM}" if first_in_session else text


def strip_credit_lines(answer: str) -> str:
    """The answer without its credit line(s), for storing."""
    if not answer or "Cognee memory" not in answer:
        return answer
    return _CREDIT_LINE_RE.sub("", answer).rstrip()


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
        """Compare the tags an answer's credit line cites with the session's tags.

        Returns ``{"cited", "valid", "invented", "earlier", "available"}``;
        ``earlier`` are valid tags not given on this turn, ``available`` how
        many this turn gave. A valid citation marks the session as credited.
        """
        cited: list[str] = []
        for group in _CITATION_RE.findall(answer or ""):
            for tag in re.split(r"\s*,\s*", group):
                if tag not in cited:
                    cited.append(tag)
        with self._lock:
            known = {r["tag"]: r for r in self.records}
            this_turn = {t for t, r in known.items() if r.get("turn") == self.turn}
            valid = [t for t in cited if t in known]
            if valid:
                self.credited = True
        return {
            "cited": cited,
            "valid": valid,
            "invented": [t for t in cited if t not in known],
            "earlier": [t for t in valid if t not in this_turn],
            "available": len(this_turn),
        }
