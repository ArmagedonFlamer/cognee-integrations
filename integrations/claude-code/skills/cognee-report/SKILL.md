---
name: cognee-report
description: Report how Cognee performed in this session or over a date range using local telemetry, including recall hit rate, latency, saves, improves, injected context, and an evidence-based grade.
---

# Cognee performance report

Use the bundled Python 3.9+ evaluator, ported from team-skills PR 3. It reads local
plugin logs and Claude transcripts and produces aggregates. It does not send data
anywhere unless an explicit export or server option is requested.

For “how did Cognee perform?”, report the current session and the last seven days.
Default to seven days when no range is supplied; use the user's range when given.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/cognee-report/scripts/plugin_eval.py" report --since 7d --compare
```

Use `--session <cognee-session-id> --since all` for the current session. Resolve the
session from the current hook context or the host's entry in
`~/.cognee-plugin/claude-code/sessions/<host-session-id>.json`; do not assume a machine-wide last-recall
marker belongs to this terminal. When present, read this host session's
`sessions/<host-session-id>/stats.json` for exact injected/deduped counters.
`--format json` exposes the complete scorecard. `--no-transcripts` is faster but
cannot measure transcript-only latency and token usage. `markers` lists available
date ranges and update times; `--since last-update --compare` evaluates an update.

Present the computed hit rate, recall and SessionStart latency, saves, improves,
injected characters, and grade. Quote evidence from the output, including sample
counts. Mark absent or low-sample metrics as unmeasured. Grades are withheld when
gates fail or evidence is insufficient; never convert that into a positive grade.
Token counts are estimates at four characters per token. Older audit logs are
clipped; say so if exact context counters are unavailable. A recalled or injected
item is not proof that it changed an answer.

Keep recommendations tied to measured failures. Compare equal windows when
possible. Do not infer seven-day post-release results from a short smoke test.
Do not use export flags unless the user asks to export the report.

Exact current-session and recent session totals are also available without reading
transcripts:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/_session_stats.py" --session <host-session-id> --days 7
```

These totals cover sessions that **started** within the window; use the evaluator's
event timestamps for exact calendar-window recall metrics.
