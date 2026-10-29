# Cognee Hermes Memory Plugin

The Cognee memory plugin has been installed. It does nothing until it is your
memory provider, so finish with one command:

```bash
hermes memory setup cognee
```

This makes Cognee the memory provider and asks for a mode:

- **remote** — Cognee Cloud or a self-hosted cognee server. You'll be asked for
  the service URL and an API key (for Cognee Cloud, both come from
  https://platform.cognee.ai/). Nothing else to install.
- **local** — the plugin runs a cognee server on your machine (shared with the
  Claude Code / Codex / OpenClaw cognee plugins, if you use them). You'll be
  asked for an LLM API key, which cognee uses to build the knowledge graph.
  This needs the `cognee` package (`cognee==1.6.5`) in Hermes's Python
  environment, which the plugin does not install because its dependencies
  conflict with Hermes's own. Without it, local mode works only while another
  cognee plugin's server is running on port 8011.

`hermes plugins enable cognee` and the Desktop toggle do not make Cognee the
memory provider; `hermes memory setup cognee` does.

Start a new `hermes` session afterwards to activate memory, then verify with
`hermes memory status` (Provider: cognee) and `hermes cognee status`.

## Updates

For a Hermes catalog installation, run `hermes plugins update cognee` to get
the latest reviewed commit. Do not overwrite it with a pip or checkout copy.

For a pip installation, run `pip install -U cognee-integration-hermes-agent`
followed by `cognee-hermes-install` to refresh the directory copy. The pip
installer refuses to overwrite a catalog-managed installation.
