---
name: cognee-handoff
description: Save a concise project handoff in Cognee so a later session can recover goals, outcomes, decisions, and open work.
---

# Save a handoff

Draft a short note from the current work: goal, outcome, decisions and reasons,
open items, and concrete next steps. Include repository/branch and relevant paths
or PR links. Separate verified results from untested assumptions. Omit credentials
and raw tool output.

Store the note as project knowledge using a temporary UTF-8 Markdown file and the
existing wrapper. Use shell-safe paths; do not interpolate the note into a command.

```bash
"${CLAUDE_PLUGIN_ROOT}/scripts/cognee-remember.sh" --file /tmp/cognee-handoff.md --node-set project_docs
```

Report the write's actual status: queued is not yet queryable. If the wrapper says
`queryable: true`, the next background digest refresh can pick it up. Otherwise
retain the note and report that ingestion is pending or failed. The next session
can explicitly retrieve it with `cognee-search.sh "<project> handoff" --graph`.
A warm digest is cached and may not include a just-written handoff immediately.
