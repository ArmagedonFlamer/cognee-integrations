"""Crediting Cognee memory in the answer, as the Claude Code, Codex and Cursor plugins do.

Every recalled item the agent is given gets a short tag (``m1``, ``m2``…) and a
label saying where it came from, so it can tell Cognee's items from everything
else it knows. An instruction above the memory asks the agent to end an answer
that used them with one plain line:

    > Cognee: Mika likes tennis, based on agent_sessions.

The plugin, not the agent, draws the logo: a ``transform_llm_output`` hook
(:func:`render_credit`) swaps ``Cognee:`` for a purple bold COGNEE wordmark in
Hermes Desktop, which renders KaTeX. Asking the agent to copy a logo itself
failed live: it dropped braille cells, lost the colour markup and copied the
placeholder, and Desktop's font draws braille as a grid of circles anyway.
Other platforms keep the plain line for now.

Tags are numbered across the session and a memory recalled again keeps its tag.
When the turn is stored, the credit is noted and stripped, so it is never stored
back into memory as if it were part of the answer.

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

#: The braille logotype an earlier version asked agents to copy; kept so a credit
#: written that way is still recognised and stripped.
LOGOTYPE = ("⡏⠁⡏⡇⡏⡁⡧⣸⢸⠅⡯", "⠓⠂⠓⠃⠓⠃⠃⠙⠘⠂⠓")
#: The Cognee logo's purple (the brand's pixel-logo artwork).
LOGO_COLOR = "#BC9BFF"
#: Platforms whose chat renders inline math (KaTeX), where ``Cognee:`` becomes the
#: pixel logo. Elsewhere the KaTeX would show as source.
COLOR_PLATFORMS = frozenset({"desktop"})

#: The Cognee pixel logo: COGNEE on a five-row grid of squares, as in the brand
#: artwork. ``X`` is a filled square.
LOGO_GRID = {
    "C": ("XXX", "X..", "X..", "X..", "XXX"),
    "O": ("XXX", "X.X", "X.X", "X.X", "XXX"),
    "G": ("XXX", "X..", "X..", "X.X", "XXX"),
    "N": ("X..X", "X..X", "XX.X", "X.XX", "X..X"),
    "E": ("XX", "X.", "XX", "X.", "XX"),
}
#: Square size and the gap between letters (in squares), so the logo stands about
#: a capital letter tall.
LOGO_SQUARE_EM = 0.2
LOGO_LETTER_GAP = 0.6


def _em(value: float) -> str:
    return ("%.3f" % value).rstrip("0").rstrip(".") + "em"


def pixel_logo(word: str = "COGNEE", square: float = LOGO_SQUARE_EM) -> str:
    """The pixel logo as inline KaTeX: one coloured ``\\rule`` per vertical run of squares.

    Each column's runs are raised to their row (the bottom row on the baseline) and
    overlaid with a negative kern; an empty column is a kern. KaTeX positions rules
    exactly, so this draws the logo's own grid wherever the chat renders math.
    """
    parts: list[str] = []
    for index, letter in enumerate(word):
        if index:
            parts.append("\\kern" + _em(LOGO_LETTER_GAP * square))
        rows = LOGO_GRID[letter]
        height = len(rows)
        for column in range(len(rows[0])):
            runs: list[tuple[int, int]] = []
            row = 0
            while row < height:
                if rows[row][column] == "X":
                    start = row
                    while row < height and rows[row][column] == "X":
                        row += 1
                    runs.append((start, row - start))
                else:
                    row += 1
            if not runs:
                parts.append("\\kern" + _em(square))
                continue
            for number, (start, length) in enumerate(runs):
                if number:
                    parts.append("\\kern-" + _em(square))
                raise_by = (height - (start + length)) * square
                parts.append(f"\\rule[{_em(raise_by)}]{{{_em(square)}}}{{{_em(length * square)}}}")
    return "$\\color{%s}{%s}$" % (LOGO_COLOR, "".join(parts))


WORDMARK = pixel_logo()

FIRST_CREDIT_ADDENDUM = (
    "This would be the first Cognee credit in this session, so in that line also say in a "
    "few words how it connects to the earlier work."
)


def _credit_rule(source: str) -> str:
    return (
        'end the answer with one line that starts with "> Cognee:" and says, in your own '
        f"words, what you remembered and that it is based on {source}. For example:\n"
        f"> Cognee: you prefer the server over the CLI, based on {source}.\n"
        "Do not copy the example, and do not write the tags in that line."
    )


def credit_instruction(first_in_session: bool, found: int, source: str = "") -> str:
    noun = "1 memory" if found == 1 else f"{found} memories"
    text = (
        f"Crediting Cognee memory: Cognee found {noun} for this prompt, each tagged below "
        "as [mN · where it came from]. A memory recalled again in this session keeps its "
        "tag. If your answer uses information from a tagged item, "
        f"{_credit_rule(source or 'Cognee memory')} Credit only items Cognee tagged, and "
        "only items your answer actually used. Never credit Cognee for what you know from "
        "project instruction files, your own memory files, the code, or this conversation. "
        "If no tagged item was used, do not mention memory."
    )
    return f"{text} {FIRST_CREDIT_ADDENDUM}" if first_in_session else text


def tool_credit_instruction(source: str = "") -> str:
    """The same instruction for results of the agent's own ``cognee_recall`` call."""
    return (
        "Each result is tagged [mN · where it came from]. If your answer uses one, "
        f"{_credit_rule(source or 'Cognee memory')} Credit only results your answer "
        "actually used."
    )


#: The agent's plain credit line: ``> Cognee:`` then something said.
_PLAIN_CREDIT_RE = re.compile(r"^([ \t]*>[ \t]*)Cognee:[ \t]*(?=\S)", re.M)
#: A credit in any form: the plain line, the rendered wordmark, or the earlier
#: braille logo (plain or KaTeX-coloured), each followed by something said.
_KATEX_OPEN = r"(?:\$\\color\{[^}\n]*\}\{\\text\{)?"
_KATEX_CLOSE = r"(?:\}\}\$)?"
_LEAD = (
    r"(?:Cognee:"
    r"|\$\\color\{[^}\n]*\}\{\\textbf\{COGNEE\}\}\$"
    r"|\$\\color\{[^}\n]*\}\{(?:\\rule\[[^\]\n]*\]\{[^}\n]*\}\{[^}\n]*\}|\\kern-?[\d.]+em)+\}\$"
    rf"|{_KATEX_OPEN}{LOGOTYPE[0]}{_KATEX_CLOSE})"
)
_CREDIT_RE = re.compile(rf"^[ \t]*>[ \t]*{_LEAD}[ \t]*\S", re.M)
#: Tags cited the way the Claude Code and Codex plugins ask (``Cognee memory
#: [m2]``); ``arr[m1]`` in code is not a citation.
_CITATION_RE = re.compile(r"Cognee memory\s*\[(m\d+(?:\s*,\s*m\d+)*)\]")
#: Lines cut before storing: every credit form above, the braille logo's second
#: row, and the older ``🧠 Cognee memory [mN]`` line.
_STRIP_RES = (
    re.compile(rf"^[ \t]*>[ \t]*{_LEAD}[^\n]*(?:\n|$)", re.M),
    re.compile(rf"^[ \t]*>[ \t]*{_KATEX_OPEN}[\u2800-\u28ff]{{6,}}[^\n]*(?:\n|$)", re.M),
    re.compile(r"^[ \t]*(?:>[ \t]*)?🧠?[ \t]*Cognee memory[ \t]*\[m\d+[^\n]*(?:\n|$)", re.M),
)


def render_credit(response_text: str, platform: str = "") -> str | None:
    """The answer with its ``> Cognee:`` line drawn for ``platform``, or None to leave it.

    The ``transform_llm_output`` hook's work: in Hermes Desktop, ``Cognee:`` becomes the
    purple bold COGNEE wordmark. Other platforms are left as written for now.
    """
    if not credit_enabled() or (platform or "").strip().lower() not in COLOR_PLATFORMS:
        return None
    if not response_text or not _PLAIN_CREDIT_RE.search(response_text):
        return None
    return _PLAIN_CREDIT_RE.sub(lambda m: f"{m.group(1)}{WORDMARK} ", response_text)


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
