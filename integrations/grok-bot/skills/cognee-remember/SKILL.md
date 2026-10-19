---
name: cognee-remember
description: Save something to Cognee memory so it can be recalled in later chats. Use when the user says "remember", "save this", "note that", "keep this for later", shares facts about their community, team, members, events or decisions worth keeping, or uploads a document to keep.
---

# Remember with Cognee

Cognee is connected as the `cognee` MCP server. Its `remember` tool stores text
or a file permanently in the user's Cognee knowledge graph.

## Pick the dataset first

Every memory goes into a named dataset. Always pass `dataset_name`; never leave
it out. Without it the server picks a name from the client (for example
`cursor_memory`), which the user will not expect to find.

- If the user or an earlier turn already named a dataset, use that one.
- Otherwise ask once, suggesting a short name for what they are working on, such
  as `london_community` or `hackathon_team`. Use lowercase letters, digits and
  underscores.
- Keep using the same dataset for the rest of the conversation. A team that
  wants shared memory has to agree on one dataset name.

## Store it

Call `remember` with:

- `data`: the text to keep. Write it as complete, self-contained statements
  ("Ana runs the Thursday meetups at Expedite"), not as a reply to the chat.
- `dataset_name`: the dataset chosen above.

For a file, pass `filename` and `content_base64` instead of `data` (up to 10 MB).

Ingestion builds the knowledge graph in the background and usually takes under
a minute. Before relying on the new memory, call `check_ingestion_status` with
the same `dataset_name` until it reports `DATASET_PROCESSING_COMPLETED`. The
first call after a quiet period can take longer while the user's Cognee
workspace starts.

Tell the user what was stored and in which dataset.

## Do not store

- Passwords, API keys, tokens or other secrets.
- Anything the user asked you not to keep.
