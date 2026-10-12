#!/usr/bin/env python3
"""Turn Claude Code's built-in auto memory (MEMORY.md) off or back on.

Usage:
    cognee-auto-memory.py status
    cognee-auto-memory.py off --scope user|project [--project-dir DIR]
    cognee-auto-memory.py on  --scope user|project [--project-dir DIR]
    cognee-auto-memory.py keep

``off`` writes ``autoMemoryEnabled: false`` to ``~/.claude/settings.json``
(user) or ``<project>/.claude/settings.local.json`` (project). ``on`` removes
that key again. ``keep`` only records that the user declined, so SessionStart
stops asking. Claude Code reads the setting at launch: changes apply from the
next session. Existing MEMORY.md notes stay on disk either way.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _auto_memory as am  # noqa: E402


def _describe(status: dict) -> str:
    state = "on" if status["enabled"] else "off"
    if status["scope"] == "default":
        return f"Claude Code auto-memory: {state} (default)"
    return f"Claude Code auto-memory: {state} ({status['source']})"


def _cmd_status(args) -> int:
    print(_describe(am.auto_memory_status(args.project_dir)))
    return 0


def _cmd_set(args, enabled: bool | None) -> int:
    try:
        path = am.write_setting(args.scope, enabled, args.project_dir)
    except (OSError, ValueError) as exc:
        print(f"Could not update settings: {exc}", file=sys.stderr)
        return 1
    status = am.auto_memory_status(args.project_dir)
    action = "Turned off" if enabled is False else "Removed the override for"
    print(f"{action} Claude Code auto-memory in {path}. Applies from the next session.")
    wanted_on = enabled is None
    if status["enabled"] != wanted_on:
        # A higher-precedence scope or the env var still decides.
        print(f"Note: still {'on' if status['enabled'] else 'off'} because of {status['source']}.")
    if enabled is False:
        am.record_offer(decision=f"off_{args.scope}", decided_at=am.now())
    return 0


def _cmd_keep(args) -> int:
    am.record_offer(decision="kept", decided_at=am.now())
    print(
        "Keeping Claude Code auto-memory on. To turn it off later, run "
        "/cognee-memory:cognee-auto-memory."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "off", "on", "keep"):
        p = sub.add_parser(name)
        p.add_argument("--project-dir", default=None)
        if name in ("off", "on"):
            p.add_argument("--scope", choices=("user", "project"), required=True)
    args = parser.parse_args(argv)
    if args.command == "status":
        return _cmd_status(args)
    if args.command == "off":
        return _cmd_set(args, False)
    if args.command == "on":
        return _cmd_set(args, None)
    return _cmd_keep(args)


if __name__ == "__main__":
    sys.exit(main())
