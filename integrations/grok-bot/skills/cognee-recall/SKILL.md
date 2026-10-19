---
name: cognee-recall
description: Look something up in Cognee memory before answering. Use when the user asks what was decided, said or saved before, asks about people, events or facts from their community or team, says "do you remember", "what do we know about", "check memory", or when an answer would depend on context from earlier chats.
---

# Recall from Cognee

Cognee is connected as the `cognee` MCP server. Its `recall` tool searches the
user's Cognee knowledge graph and returns answers built from what was stored.

## Search the right dataset

Call `recall` with:

- `query`: a plain-language question, e.g. "Who organises the Thursday
  meetups?"
- `datasets`: the dataset this conversation uses (the one passed to
  `remember`). Several datasets can be given comma-separated.

Leave `datasets` out only when the user asks to search everything. A search with
no dataset runs once per dataset in the account, is slower, and returns many
"no information" answers from unrelated datasets.

If you do not know the dataset yet, ask the user which one to search.

## Read the results

- The first line summarises the hits, e.g. `3 memories found (...)`. It names at
  most three datasets, so a hit can come from a dataset it does not list.
- Each following line is one answer. Answers that say the context has no
  relevant information are not hits; ignore them.
- If a memory was saved moments ago and is missing, check
  `check_ingestion_status` for that dataset; it may still be processing.

Answer from what recall returned and say that it came from Cognee memory. If
nothing relevant came back, say so instead of guessing.
