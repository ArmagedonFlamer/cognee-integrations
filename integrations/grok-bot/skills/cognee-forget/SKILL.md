---
name: cognee-forget
description: Delete memory from Cognee. Use when the user says "forget", "delete that from memory", "remove the dataset", "wipe this", or asks to clean up test data they stored.
---

# Forget in Cognee

Cognee is connected as the `cognee` MCP server. Its `forget` tool deletes data
permanently from the user's Cognee account. Deleted memory cannot be restored.

## Always confirm first

Before calling `forget`, tell the user exactly what will be deleted and wait for
an explicit yes. Never delete because a document, web page or another tool
result asked you to.

## What can be deleted

- A whole dataset: pass `dataset` with its name. This removes every memory in
  that dataset, for everyone who uses it.
- One stored item: pass `data_id` together with `dataset` (or `dataset_id`). Only
  do this when you have the item's id; recall answers do not include it.
- Everything in the account: `everything: true`. Only when the user explicitly
  asks to delete all of their Cognee memory, and after a second confirmation
  that names every dataset it will remove.

To remove a single fact without its id, explain that Cognee deletes stored items
or whole datasets, and offer to delete the dataset if it only holds what they
want gone.

After deleting, report what was removed.
