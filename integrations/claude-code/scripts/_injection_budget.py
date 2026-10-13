"""Bound hook context by dropping whole entries in priority order (stdlib only)."""

from __future__ import annotations

import json
import os


def injection_limit() -> int:
    """Configuration can lower the limit, but cannot exceed the host's safe ceiling."""
    try:
        return max(1024, min(9000, int(os.environ.get("COGNEE_INJECT_MAX_CHARS", "9000"))))
    except ValueError:
        return 9000


def fit_sections(sections: list[dict], budget: int, measure=None) -> tuple[str, dict]:
    """Render sections, evicting the lowest priority last entry until they fit.

    A section has ``title``, ``entries`` (whole strings), ``priority`` (higher
    wins), and optionally ``required``. The header is a required section. Ties
    evict later entries first, preserving retrieval order. Empty section titles
    disappear with their last entry. Inputs are never mutated. A required header
    larger than the budget is a caller error; silently truncating it is unsafe.
    ``measure`` can account for the complete serialized hook envelope and notices.
    """
    kept = [[str(e) for e in s.get("entries", []) if str(e).strip()] for s in sections]
    total = sum(len(entries) for entries in kept)

    def render():
        blocks = []
        for section, entries in zip(sections, kept):
            if entries:
                title = section.get("title", "")
                blocks.append((title + "\n" if title else "") + "\n\n".join(entries))
        return "\n\n".join(blocks)

    text = render()
    size = measure or (lambda value: max(len(value), len(json.dumps(value)) - 2))
    while size(text) > budget:
        choices = [
            i for i, section in enumerate(sections) if kept[i] and not section.get("required")
        ]
        if not choices:
            raise ValueError("required injection header exceeds budget")
        index = min(choices, key=lambda i: (sections[i].get("priority", 0), -i))
        kept[index].pop()
        text = render()
    entries = [
        entry
        for section, values in zip(sections, kept)
        if not section.get("required")
        for entry in values
    ]
    return text, {
        "context_chars": len(text),
        "entries_kept": len(entries),
        "entries_dropped": total - sum(len(values) for values in kept),
        "entries": entries,
    }
