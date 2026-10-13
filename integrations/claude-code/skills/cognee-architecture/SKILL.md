---
name: cognee-architecture
description: Explain repository architecture, inspect hotspots, draw a code graph diagram, or review a change's impact using Cognee's Enola code graph.
---

# Explain architecture from code facts

Run `"${CLAUDE_PLUGIN_ROOT}/scripts/cognee-doctor.sh" --json` and check
`cognee_server` before these queries. `architecture`, `insights`, and diagram output
require server **1.6.3 or newer**. For an older or unknown version, explain the
requirement and stop these operations; do not silently replace them with invented
architecture. Index with the existing `cognee-code` skill if no graph exists.

Cloud indexing requires a git URL, for example:

```bash
"${CLAUDE_PLUGIN_ROOT}/scripts/cognee-index-repo.sh" https://github.com/org/repo --wait 60
```

Cloud facts reflect pushed commits. Identify that revision/freshness when local
changes have not been pushed and re-indexed. Cloud servers cannot read local paths.

Use these existing search commands (the code dataset resolves from the checkout;
add `--dataset <name>` for a different indexed repository):

```bash
"${CLAUDE_PLUGIN_ROOT}/scripts/cognee-search.sh" "architecture" --code --code-query '{"operation":"architecture","diagram":"mermaid"}'
"${CLAUDE_PLUGIN_ROOT}/scripts/cognee-search.sh" "hotspots" --code --code-query '{"operation":"insights","diagram":"mermaid"}'
"${CLAUDE_PLUGIN_ROOT}/scripts/cognee-search.sh" "process_payment" --code --code-query '{"operation":"impact_analysis","targets":["process_payment"],"diagram":"mermaid"}'
"${CLAUDE_PLUGIN_ROOT}/scripts/cognee-search.sh" "changes" --code --code-query '{"operation":"delta"}'
```

`architecture` gives the module, route, storage, and service map. `insights` gives
Enola explainer findings and their supporting facts. `impact_analysis` follows the
named target's dependencies; replace the example with the user's actual symbol.
`delta` describes changes from the last index, not an arbitrary unindexed diff.

Cite fact names, files, and lines when explaining findings. Separate structural
facts from your interpretation of risk. Render `diagram.source` in a Mermaid block
when requested; if the result has no diagram, say so. Ask for an exact candidate
when a seed is ambiguous. Verify important claims against source before editing.
