# Changelog

All notable changes to the **cognee-grok-bot** plugin are documented here.

The version here must match the `version` field in `.cursor-plugin/plugin.json`.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this
project adheres to [Semantic Versioning](https://semver.org/).

## [0.1.0]

### Added
- Cursor-format plugin for Grok Bot. `mcp.json` attaches the hosted Cognee MCP
  server at `https://mcp.aws.cognee.ai/mcp`; Grok Bot signs the user in to Cognee
  with OAuth, so there is no API key to paste and nothing to install.
- Skills `cognee-remember`, `cognee-recall` and `cognee-forget`, which tell the bot
  when to use each tool, to always name the dataset, and to confirm before deleting.
- Root `.cursor-plugin/marketplace.json` listing the plugin, so the repository can
  be added as a marketplace in Grok Bot or Cursor.
