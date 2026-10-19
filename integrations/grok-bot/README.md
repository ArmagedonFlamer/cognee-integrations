# Cognee Plugin for Grok Bot

Gives [Grok Bot](https://x.ai) persistent knowledge-graph memory through the hosted
Cognee MCP server. Grok Bot signs you in with your Cognee account, so there is no
API key to paste and nothing to install.

| Part | What it does |
|---|---|
| `mcp.json` | Attaches the hosted Cognee MCP server, `https://mcp.aws.cognee.ai/mcp`, as the `cognee` server |
| `skills/cognee-remember` | When to store something, and which dataset to store it in |
| `skills/cognee-recall` | When to search memory, and how to read the results |
| `skills/cognee-forget` | How to delete memory, always after the user confirms |

Grok Bot runs in the cloud and does not run plugin hooks, so memory is not saved or
recalled automatically: the bot decides when to call the tools, and the skills tell
it when. For automatic capture in a coding agent, see the
[Claude Code](../claude-code/README.md) and [Codex](../codex/README.md) plugins.

## Before you start

You need a [Cognee Cloud](https://platform.cognee.ai) account with a workspace. The
MCP server answers for the workspace your account belongs to.

## Install

Either:

- **Add this repository as a marketplace** in Grok Bot's plugin settings (or a
  Cursor team marketplace: Dashboard → Plugins & MCPs → Team Marketplaces → Import
  from Repo) using `https://github.com/topoteretes/cognee-integrations`, then
  install **cognee-grok-bot**. The repository's `.cursor-plugin/marketplace.json`
  lists it.
- **Or skip the plugin** and ask Grok Bot to connect the remote MCP server
  `https://mcp.aws.cognee.ai/mcp` with OAuth. You get the same tools without the
  skills.

The first time a tool is used, Grok Bot shows a connect card. Sign in with your
Cognee account and approve access.

## Tools

The hosted server exposes four tools:

| Tool | Use |
|---|---|
| `remember` | Store text (`data`) or a file (`filename` + `content_base64`, up to 10 MB) in `dataset_name` |
| `recall` | Search memory with `query`, scoped to `datasets` (comma-separated) |
| `forget` | Delete a dataset (`dataset`), one item (`data_id` + `dataset`), or everything (`everything: true`) |
| `check_ingestion_status` | Whether a dataset finished processing after `remember` |

Always name the dataset. Without `dataset_name`, `remember` writes to a dataset
named after the MCP client, which for Grok Bot is `cursor_memory`. Without
`datasets`, `recall` searches every dataset in the account and returns one answer
per dataset. A team that wants shared memory agrees on one dataset name.

## Access

The hosted server acts as the signed-in user, with all of that user's datasets.
To give a bot narrower access, run
[`cognee-mcp`](https://github.com/topoteretes/cognee/tree/main/cognee-mcp) in API
mode with an API key that only has the permissions you want, and connect Grok Bot
to that instead.

## Try it

```
Remember in dataset "hackathon_test": the London hackathon is on 22 October 2026 at Expedite London HQ.
```

Wait for `check_ingestion_status` to report `DATASET_PROCESSING_COMPLETED` (about a
minute for a short note), then in a new chat:

```
Recall from dataset "hackathon_test": when and where is the London hackathon?
```

Clean up with `Forget the dataset "hackathon_test"`.
