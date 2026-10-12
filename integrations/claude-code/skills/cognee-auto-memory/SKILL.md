---
name: cognee-auto-memory
description: Turn Claude Code's built-in auto-memory (MEMORY.md) off or back on, so Cognee is the only memory. Use when the user asks to disable or re-enable Claude's own memory, or MEMORY.md, alongside Cognee.
---

# Claude Code auto-memory

Claude Code's built-in auto-memory loads `MEMORY.md` into every session. With Cognee installed it duplicates Cognee memory, and the agent can't tell which memory a fact came from.

## Instructions

1. Show the current state:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cognee-auto-memory.py" status
   ```

2. Ask the user what they want, unless they already said: turn it off for all projects (recommended), for this project only, or turn it back on.

3. Run the matching command:

   ```bash
   # Off for all projects (~/.claude/settings.json)
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cognee-auto-memory.py" off --scope user
   # Off for this project only (.claude/settings.local.json)
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cognee-auto-memory.py" off --scope project
   # Back on: removes the setting from that scope
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cognee-auto-memory.py" on --scope user
   ```

4. Tell the user the result in one line. The change applies from the next session. Existing `MEMORY.md` notes stay on disk.

If the output says the setting is still overridden (by `CLAUDE_CODE_DISABLE_AUTO_MEMORY` or a higher settings scope), tell the user which one, since this script doesn't change it.
