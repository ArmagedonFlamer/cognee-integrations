// Cognee memory: the in-process layer of the plugin.
//
// The settings hooks in hooks.json run the memory pipeline (recall, capture,
// learning from the session, sync) exactly as before. This module adds what
// only an in-process module can:
//
//   * the status row, above the prompt on every surface: it runs the plugin's
//     own status-line renderer in `--json` mode and lays the result out, so
//     the band shows the row the settings-file status line used to show. In a
//     terminal the module marks its session (`band/<session>.json`) and the
//     renderer then prints nothing for it, so the row is never drawn twice;
//     with the `band` option off the settings status line is back below;
//   * live activity: it reads the per-session marker the hooks write
//     (`~/.cognee-plugin/claude-code/phase/<session>.json`) and shows it on
//     the row, after the spinner, and under answers;
//   * the dataset name on the row is a picker: choosing another writable
//     dataset, or typing the name of a new one, switches the session there
//     without a model turn (`/cognee-dataset <name>` does the same from the
//     prompt);
//   * two tools the model calls directly, `mcp__cognee-memory__search` and
//     `mcp__cognee-memory__remember`, served by the plugin's own shell
//     wrappers: no skill file to read, no Bash row, structured arguments.
//
// Everything the module runs is one of the plugin's own scripts, as a local
// process; the only network traffic is theirs, on the person's or the model's
// explicit action, plus one listing of the writable datasets for the picker. Nothing here runs on a fixed timer while the session is
// idle: the marker is read on the events the hooks fire, and polled once a
// second only while an operation is running or for a short window after one
// was started.
//
// Only native events are hooked (prompt.submit, tool.call, turn.complete,
// session.compact, session.start, session.end), never the classic.* mirrors
// of the settings hooks: the built-in guard bypasses a user-installed mod's
// classic.* hooks for Team and Enterprise sign-ins and wherever managed
// settings exist, so nothing here would run for those users.
import type { Register, RenderElement } from 'claude-code'

type Activity = { kind: 'active' | 'result'; phase: string; text: string; age: number; ok: boolean; count?: number }

type Snapshot = {
  enabled: boolean
  state: string
  reason: string
  dataset: string
  mode: string
  credits: string
  update: string
  text: string
}

type PhaseResult = { count?: unknown; at?: unknown; ok?: unknown }
type PhaseMarker = {
  active?: { phase?: unknown; detail?: unknown; updated_at?: unknown } | null
  results?: Record<string, PhaseResult>
}

const FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
const FRAME_MS = 120
const POLL_MS = 1000
// How long after a hook fired the marker keeps being polled for the async
// work (saving, learning from the session) that hook started.
const POLL_WINDOW_MS = 30_000
// Mirrors the renderer: an active phase older than this is a hook that died.
const STALE_MS = 90_000
const DIM_AFTER_MS = 60_000
const RENDERER_TIMEOUT_MS = 5000
// Phases whose settings hook runs async, so the terminal spinner shows no
// statusMessage for them; the mod adds its own suffix there. The sync hooks
// (recall, anchor, session start) already announce themselves in a terminal.
const ASYNC_PHASES = ['saving', 'consolidating', 'indexing', 'syncing']

const ACTIVE_LABELS = {
  connecting: 'connecting…',
  setting_up: 'setting up memory',
  recalling: 'recalling…',
  saving: 'saving…',
  consolidating: 'learning from this session…',
  indexing: 'indexing repository…',
  anchoring: 'preserving context…',
  syncing: 'saving session…',
  // The module's own work, never written to the marker.
  searching: 'searching memory…',
  remembering: 'remembering…',
  switching: 'switching dataset…',
} as const
type Phase = keyof typeof ACTIVE_LABELS
// What the module runs itself shows after a terminal spinner too: the tool row
// there says only which tool runs.
const OWN_PHASES = ['searching', 'remembering', 'switching']

const SEARCH_TIMEOUT_MS = 60_000
const REMEMBER_TIMEOUT_MS = 90_000
const LIST_TIMEOUT_MS = 20_000
// A switch syncs the session it leaves first; that can take minutes.
const SWITCH_TIMEOUT_MS = 600_000
const NODE_SETS: Record<string, string> = {
  user: 'user_context',
  project: 'project_docs',
  agent: 'agent_actions',
}

// ── module state (reset on a reload, which is fine: it is all redrawn) ──────
let sessionId = ''
let phasePath = ''
let bandPath = ''
let rendererPython: string | null = null
let rendererFailures = 0
let snapshot: Snapshot | null = null
let active: Activity | null = null
let result: Activity | null = null
let frame = 0
let spin: { cancel: () => void } | null = null
let poll: { cancel: () => void } | null = null
let pollUntil = 0
let recalledThisTurn = -1
let icon = '🧠 '
let bandOn = true
let spinnerOn = true
let answerLineOn = true
let toolsOn = true

// The dataset picker on the band.
type DatasetRow = { name: string; id: string; current: boolean }
let switchTargets: DatasetRow[] = []
let switching = false
// The picker's last entry: picking it turns the picker into a text field for
// a dataset that does not exist yet (the switch script creates it). Spaces
// make the value one no dataset can be named, so it never collides.
const NEW_DATASET = '+ new dataset…'
let namingDataset = false
// One listing per session, refreshed after a switch or by the pane's refresh.
let datasetsListed = false
let datasetsListing = false

// ── pure helpers ────────────────────────────────────────────────────────────
function plural(n: number, word: string): string {
  if (n === 1) return `${n} ${word}`
  return `${n} ${word.endsWith('y') ? word.slice(0, -1) + 'ies' : word + 's'}`
}

function toNumber(value: unknown): number | null {
  const n = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(n) ? n : null
}

function activeLabel(phase: string, detail: string): string {
  const label = (ACTIVE_LABELS as Record<string, string>)[phase] ?? ''
  if (!label) return ''
  if (phase === 'setting_up') return detail ? `${label} · ${detail}…` : `${label}…`
  return label
}

function resultLabel(kind: string, entry: PhaseResult): string {
  const ok = entry.ok !== false
  const count = toNumber(entry.count)
  switch (kind) {
    case 'recalled':
      if (!ok) return 'recall failed'
      if (!count) return 'nothing relevant yet'
      return `recalled ${plural(count, 'memory')}`
    case 'saved':
      if (!ok && !count) return 'save failed'
      return `saved ${plural(count ?? 0, 'memory')}`
    case 'ready':
      return ok ? 'memory ready' : 'memory unavailable'
    case 'consolidated':
      return ok ? 'memory updated' : 'memory update failed'
    case 'indexed':
      return ok ? 'repository indexed' : 'indexing failed'
    case 'anchored':
      return 'context preserved'
    case 'synced':
      return 'session saved'
    default:
      return ''
  }
}

// The same choice the renderer makes: an active phase wins, else the most
// recent finished result; a stale active phase is ignored.
function pickActivity(marker: PhaseMarker, nowMs: number): Activity | null {
  const current = marker.active
  if (current && typeof current === 'object' && typeof current.phase === 'string') {
    const updated = toNumber(current.updated_at)
    const ageMs = updated === null ? STALE_MS : nowMs - updated * 1000
    if (ageMs < STALE_MS) {
      const text = activeLabel(current.phase, typeof current.detail === 'string' ? current.detail : '')
      if (text) return { kind: 'active', phase: current.phase, text, age: ageMs / 1000, ok: true }
    }
  }
  const results = marker.results
  if (!results || typeof results !== 'object') return null
  let latestKind = ''
  let latest: PhaseResult = {}
  let latestAt = -1
  for (const [kind, entry] of Object.entries(results)) {
    if (!entry || typeof entry !== 'object') continue
    const at = toNumber(entry.at)
    if (at !== null && at > latestAt) {
      latestKind = kind
      latest = entry
      latestAt = at
    }
  }
  const text = latestKind ? resultLabel(latestKind, latest) : ''
  if (!text) return null
  const count = toNumber(latest.count)
  return {
    kind: 'result',
    phase: latestKind,
    text,
    age: Math.max(0, (nowMs - latestAt * 1000) / 1000),
    ok: latest.ok !== false,
    ...(count === null ? {} : { count }),
  }
}

function parseRecallHeader(context: readonly string[] | undefined): number {
  for (const block of context ?? []) {
    const match = /Cognee memory: recall (\d+) memory/.exec(block)
    if (match) return Number(match[1])
  }
  return -1
}

function activity(phase: Phase): Activity {
  return { kind: 'active', phase, text: ACTIVE_LABELS[phase], age: 0, ok: true }
}

// Whether a tree draws anything: text that is not blank, or a control. The
// engine's own band is often an empty element, which still takes a row in a
// terminal when stacked under ours, so an empty one is left out.
function drawsSomething(tree: unknown): boolean {
  if (tree === null || tree === undefined || tree === false) return false
  if (typeof tree === 'string') return tree.trim().length > 0
  if (typeof tree === 'number') return true
  if (Array.isArray(tree)) return tree.some(drawsSomething)
  if (typeof tree !== 'object') return false
  const el = tree as { type?: unknown; props?: { children?: unknown } }
  if (el.type !== 'Box' && el.type !== 'Text') return true
  return drawsSomething(el.props?.children)
}

function asText(value: unknown): string {
  if (typeof value === 'string') return value
  if (value === null || value === undefined) return ''
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

// One recall entry (the wrapper's /api/v1/recall item) as text.
function hitText(entry: Record<string, unknown>): string {
  const source = asText(entry.source)
  if (source === 'trace') {
    const parts = [`${asText(entry.origin_function) || '?'} — ${asText(entry.status)}`]
    if (entry.session_feedback) parts.push(`feedback: ${asText(entry.session_feedback)}`)
    if (entry.method_return_value) parts.push(`output: ${asText(entry.method_return_value).slice(0, 600)}`)
    return parts.join('\n')
  }
  const text = asText(entry.text) || asText(entry.content)
  if (text) return text
  const lines: string[] = []
  if (entry.question) lines.push(`Q: ${asText(entry.question)}`)
  if (entry.answer) lines.push(`A: ${asText(entry.answer)}`)
  return lines.join('\n')
}

// What a search wrapper printed, as the model and the pane read it.
function formatSearch(stdout: string, exitCode: number): { text: string; count: number; isError: boolean } {
  const raw = stdout.trim()
  if (!raw || raw === 'UNREACHABLE' || (exitCode !== 0 && !raw.startsWith('[') && !raw.startsWith('{'))) {
    return { text: 'Cognee server unreachable: the search did not run. Retry once memory is ready.', count: 0, isError: true }
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return { text: raw, count: raw ? 1 : 0, isError: exitCode !== 0 }
  }
  if (Array.isArray(parsed)) {
    const entries = parsed.filter(x => x && typeof x === 'object') as Record<string, unknown>[]
    if (entries.length === 0) return { text: 'No memories matched.', count: 0, isError: false }
    const blocks = entries.map((entry, i) => {
      const source = asText(entry.source) || 'memory'
      return `### ${i + 1}. ${source}\n${hitText(entry).trim()}`
    })
    return { text: blocks.join('\n\n'), count: entries.length, isError: false }
  }
  if (parsed && typeof parsed === 'object' && 'error' in parsed) {
    return { text: `Cognee search failed: ${asText((parsed as { error: unknown }).error)}`, count: 0, isError: true }
  }
  return { text: raw, count: 1, isError: false }
}

// What the remember wrapper printed, as one line for the model.
function formatRemember(stdout: string, exitCode: number, dataset: string): { text: string; isError: boolean } {
  const raw = stdout.trim()
  if (!raw || raw === 'UNREACHABLE') {
    return { text: 'Cognee server unreachable: nothing was stored. Retry once memory is ready.', isError: true }
  }
  let parsed: Record<string, unknown> = {}
  try {
    const value = JSON.parse(raw)
    if (value && typeof value === 'object') parsed = value as Record<string, unknown>
  } catch {
    return { text: raw, isError: exitCode !== 0 }
  }
  if (parsed.error) return { text: `Cognee remember failed: ${asText(parsed.error)}`, isError: true }
  if (parsed.ok === false) return { text: `Cognee rejected the write: ${raw}`, isError: true }
  const where = dataset || asText(parsed.dataset) || 'the active dataset'
  if (parsed.queryable === true) return { text: `Stored in ${where}; it is in the graph and recall will find it.`, isError: false }
  const outcome = asText(parsed.wait_outcome)
  if (outcome === 'errored') return { text: `Stored in ${where}, but building the graph from it failed; check the server log.`, isError: true }
  return { text: `Stored in ${where}; the graph is still being built from it, so recall finds it in a moment.`, isError: false }
}

function datasetRows(value: unknown): DatasetRow[] {
  if (!value || typeof value !== 'object') return []
  const rows = (value as { datasets?: unknown }).datasets
  if (!Array.isArray(rows)) return []
  return rows
    .filter(row => row && typeof row === 'object' && typeof (row as { name?: unknown }).name === 'string')
    .map(row => {
      const r = row as { name: string; id?: unknown; current?: unknown }
      return { name: r.name, id: asText(r.id), current: r.current === true }
    })
}

// ── helpers that reach the engine ───────────────────────────────────────────
async function stateDir($: any): Promise<string> {
  const home = (await $.env.get('HOME')) || (await $.env.get('USERPROFILE')) || ''
  return `${home}/.cognee-plugin/claude-code`
}

async function bindSession($: any): Promise<void> {
  sessionId = await $.session.id()
  const dir = await stateDir($)
  phasePath = `${dir}/phase/${sessionId}.json`
  bandPath = `${dir}/band/${sessionId}.json`
  await markBand($)
}

// Tell the settings-file status line that this session's row is drawn above
// the prompt: the renderer prints nothing for a session with this marker, so
// the row is not shown twice. Written only while the band is on.
async function markBand($: any): Promise<void> {
  if (!bandOn || !bandPath) return
  try {
    await $.fs.write(bandPath, JSON.stringify({ session_id: sessionId, at: (await $.clock.now()) / 1000, plugin: 'cognee-memory' }))
  } catch {
    // the status line then stays; nothing else depends on the marker
  }
}

function redraw($: any): void {
  $.ui.invalidate('ui.render')
}

function setActive($: any, next: Activity): void {
  active = next
  if (!spin) {
    spin = $.clock.every(FRAME_MS, () => {
      frame = (frame + 1) % FRAMES.length
      $.ui.invalidate('ui.render')
    })
  }
  redraw($)
}

function clearActive($: any): void {
  active = null
  if (spin) {
    spin.cancel()
    spin = null
  }
  redraw($)
}

async function readPhase($: any): Promise<void> {
  if (!phasePath) return
  let marker: PhaseMarker
  try {
    marker = JSON.parse(await $.fs.read(phasePath)) as PhaseMarker
  } catch {
    return
  }
  const shown = pickActivity(marker, await $.clock.now())
  if (shown?.kind === 'active') {
    if (!active || active.phase !== shown.phase) setActive($, shown)
    return
  }
  result = shown
  if (active) clearActive($)
  else redraw($)
}

// Poll the marker once a second while something runs, and for a short window
// after a hook fired (its async work lands within seconds). Stops by itself.
async function startPoll($: any, windowMs: number): Promise<void> {
  const now = await $.clock.now()
  pollUntil = Math.max(pollUntil, now + windowMs)
  if (poll) return
  poll = $.clock.every(POLL_MS, async () => {
    await readPhase($)
    const at = await $.clock.now()
    if (!active && at > pollUntil && poll) {
      poll.cancel()
      poll = null
      await refreshSnapshot($)
    }
  })
}

type Ran = { exitCode: number; stdout: string; stderr: string }

// One of the plugin's Python scripts, under whichever interpreter answers
// first (python3 on macOS and Linux, python or py on Windows). Null when none
// could start it.
async function runPython($: any, args: string[], init: { stdin?: string; timeoutMs: number }): Promise<Ran | null> {
  const candidates = rendererPython ? [rendererPython] : ['python3', 'python', 'py']
  for (const python of candidates) {
    try {
      const ran = (await $.process.run([python, ...args], init)) as Ran
      rendererPython = python
      return ran
    } catch {
      // the next candidate
    }
  }
  return null
}

// One of the plugin's shell wrappers (the same ones the skills run).
async function runWrapper($: any, script: string, args: string[], timeoutMs: number): Promise<Ran | null> {
  try {
    return (await $.process.run(['bash', `${$.plugin.root}/scripts/${script}`, ...args], { timeoutMs })) as Ran
  } catch {
    return null
  }
}

// A search, trimmed the way the prompt hook trims what it injects: a graph
// item on cognee >= 1.6.0 carries the whole prompt the server's LLM would
// have read, and only its retrieved context is worth showing. The trim is the
// plugin's own Python (recall-trim.py); when it cannot run, the raw answer is
// shown rather than nothing.
async function runSearch($: any, query: string, args: string[]): Promise<Ran | null> {
  const ran = await runWrapper($, 'cognee-search.sh', [query, ...args], SEARCH_TIMEOUT_MS)
  if (!ran || ran.exitCode !== 0 || !ran.stdout.trimStart().startsWith('[')) return ran
  const trimmed = await runPython($, [`${$.plugin.root}/scripts/recall-trim.py`, '--query', query], {
    stdin: ran.stdout,
    timeoutMs: RENDERER_TIMEOUT_MS,
  })
  if (!trimmed || trimmed.exitCode !== 0 || !trimmed.stdout.trim()) return ran
  return { ...ran, stdout: trimmed.stdout }
}

// One run of the plugin's own renderer in --json mode: the dataset, mode,
// connection state, credits and update nudge, as the terminal bar shows them.
// Local process, no network. Runs only on events, never on a timer.
async function refreshSnapshot($: any): Promise<void> {
  if (!sessionId || rendererFailures >= 3) return
  const script = `${$.plugin.root}/scripts/cognee_statusline_render.py`
  const stdin = JSON.stringify({ session_id: sessionId })
  const ran = await runPython($, [script, '--json'], { stdin, timeoutMs: RENDERER_TIMEOUT_MS })
  if (ran && ran.exitCode === 0 && ran.stdout.trim()) {
    try {
      snapshot = JSON.parse(ran.stdout) as Snapshot
      rendererFailures = 0
      redraw($)
      return
    } catch {
      // fall through
    }
  }
  rendererFailures += 1
}

// ── the dataset picker ──────────────────────────────────────────────────────
function sessionKeyArgs(): string[] {
  return sessionId ? ['--session-key', sessionId] : []
}

// The datasets the picker offers: the writable ones, from the plugin's own
// switch script, once when the band is first drawn and after each switch.
async function loadDatasets($: any): Promise<void> {
  if (datasetsListing) return
  datasetsListing = true
  const switchable = await runPython($, [`${$.plugin.root}/scripts/switch-dataset.py`, '--list', '--json', ...sessionKeyArgs()], {
    timeoutMs: LIST_TIMEOUT_MS,
  })
  try {
    const parsed = switchable && switchable.stdout.trim() ? JSON.parse(switchable.stdout) : {}
    if (!parsed.error) switchTargets = datasetRows(parsed)
  } catch {
    // the name stays plain text
  }
  datasetsListed = true
  datasetsListing = false
  redraw($)
}

// Move the session to another dataset: the plugin's switch script, which
// syncs the session it leaves and starts a new one. No model turn.
async function switchDataset($: any, target: string): Promise<string> {
  const name = target.trim()
  if (!name) return 'name a dataset'
  if (switching) return 'a switch is already running'
  switching = true
  setActive($, activity('switching'))
  let line = ''
  try {
    const ran = await runPython($, [`${$.plugin.root}/scripts/switch-dataset.py`, name, '--json', ...sessionKeyArgs()], {
      timeoutMs: SWITCH_TIMEOUT_MS,
    })
    let parsed: Record<string, unknown> = {}
    try {
      parsed = ran && ran.stdout.trim() ? (JSON.parse(ran.stdout) as Record<string, unknown>) : {}
    } catch {
      parsed = {}
    }
    if (!ran) line = 'could not start the switch: no python interpreter found'
    else if (parsed.error) line = asText(parsed.error)
    else if (parsed.switched === false) line = `already on ${asText(parsed.dataset) || name}`
    else if (parsed.switched === true) {
      const previous = (parsed.previous ?? {}) as { dataset?: unknown; synced?: unknown }
      const synced = previous.synced === false ? ' (previous session NOT synced)' : ''
      line = `switched to ${asText(parsed.dataset) || name}${synced}`
    } else line = `switch failed${ran.stderr.trim() ? `: ${ran.stderr.trim().split('\n').pop()}` : ''}`
    const failed = !parsed.switched && parsed.switched !== false
    $.ui.toast(failed ? `${icon}✕ ${line}` : `${icon}${line}`)
  } finally {
    switching = false
    clearActive($)
  }
  await refreshSnapshot($)
  void loadDatasets($)
  return line
}

export const register: Register = (on, options) => {
  icon = options.icon === 'none' ? '' : '🧠 '
  bandOn = options.band !== false
  spinnerOn = options.spinner !== false
  answerLineOn = options.answerLine !== false
  toolsOn = options.tools !== false

  on('session.start', async ($, e, next) => {
    await bindSession($)
    await readPhase($)
    await refreshSnapshot($)
    // The band's dataset picker needs the writable datasets: one listing, not
    // awaited (the session must not wait on a slow server). The Desktop app
    // reports no surface at start (it attaches after), so the band's first
    // draw starts the listing too; this covers a session that does say desktop.
    if (bandOn && e.surface === 'desktop') void loadDatasets($)
    await $.command.register({
      name: 'cognee-dataset',
      description: 'Switch this session to another Cognee dataset; without a name, list them.',
      argumentHint: '[name]',
    })
    if (toolsOn) {
      await $.tool.register({
        name: 'search',
        description:
          'Search Cognee memory: the permanent knowledge graph of this session\'s dataset (scope "graph", the default), ' +
          'or the indexed repository\'s code graph (scope "code"). Memory is already recalled automatically on every prompt; ' +
          'call this for a deeper or differently worded search, for more results, or to read another dataset by its id. ' +
          'Returns the matching memories as text; an empty result from a reachable server is authoritative.',
        inputSchema: {
          type: 'object',
          properties: {
            query: { type: 'string', description: 'What to look for, as a question or a few keywords.' },
            top_k: { type: 'integer', minimum: 1, maximum: 50, default: 10, description: 'How many results to return.' },
            scope: { type: 'string', enum: ['graph', 'code'], default: 'graph', description: 'graph: memory; code: the indexed repository.' },
            dataset_id: {
              type: 'string',
              description: 'Read another dataset by its UUID (from the "Other Cognee datasets" hint). Leaves the session on the active dataset.',
            },
          },
          required: ['query'],
        },
      })
      await $.tool.register({
        name: 'remember',
        description:
          'Store something permanently in Cognee memory (the active dataset\'s knowledge graph). ' +
          'Category "user" for the person\'s preferences, corrections and facts about them; "project" for facts about the codebase, ' +
          'docs and decisions; "agent" for your own findings. Pass "file" instead of "content" to store a file under its real name ' +
          '(code then rides the code path). Routine tool calls are captured automatically; do not store those.',
        inputSchema: {
          type: 'object',
          properties: {
            content: { type: 'string', description: 'The text to remember.' },
            category: { type: 'string', enum: ['user', 'project', 'agent'], default: 'user' },
            file: { type: 'string', description: 'A file path to store instead of inline content.' },
          },
        },
      })
    }
    // A session start still connecting or bootstrapping: watch it land.
    if (active) await startPoll($, POLL_WINDOW_MS)
    return next(e)
  })

  // /cognee-dataset <name> switches from the prompt, on every surface; alone
  // it lists the datasets the picker offers.
  on('command.run', { command: 'cognee-dataset' }, async ($, e) => {
    const name = e.args.trim()
    if (!name) {
      datasetsListed = false
      await loadDatasets($)
      if (switchTargets.length === 0) return { text: 'Cognee: no datasets listed (is memory connected?).' }
      const rows = switchTargets.map(r => (r.current ? `  * ${r.name} (current)` : `  - ${r.name}`)).join('\n')
      return { text: `Cognee datasets you can write to:\n${rows}\n\n/cognee-dataset <name> switches this session.` }
    }
    const line = await switchDataset($, name)
    return { text: `Cognee: ${line}.` }
  })

  // The model's direct line to memory: the same wrappers the skills run,
  // without the skill file and the Bash row. A registered tool answers with
  // the text the model reads (the engine takes a string or an array, never an
  // object); a failure is a deny, which the model reads as an error result.
  on('tool.call', { tool: 'mcp__cognee-memory__search' }, async ($, e) => {
    const query = asText(e.query).trim()
    if (!query) return { deny: 'query is required' }
    const topK = Math.max(1, Math.min(50, toNumber(e.top_k) ?? 10))
    const scope = e.scope === 'code' ? '--code' : '--graph'
    const datasetId = asText(e.dataset_id).trim()
    setActive($, activity('searching'))
    let shown
    try {
      const ran = await runSearch($, query, [String(topK), scope, ...(datasetId ? ['--dataset-id', datasetId] : [])])
      shown = ran ? formatSearch(ran.stdout, ran.exitCode) : formatSearch('', 1)
    } finally {
      clearActive($)
    }
    const where = datasetId ? `dataset ${datasetId}` : scope === '--code' ? 'the code graph' : 'the active dataset'
    if (shown.isError) return { deny: shown.text }
    return { result: `${plural(shown.count, 'result')} from ${where}.\n\n${shown.text}` }
  }).catch($ => {
    clearActive($)
    return { deny: 'Cognee search failed inside the plugin; try the cognee-search skill.' }
  })

  on('tool.call', { tool: 'mcp__cognee-memory__remember' }, async ($, e) => {
    const content = asText(e.content).trim()
    const file = asText(e.file).trim()
    if (!content && !file) return { deny: 'content or file is required' }
    const nodeSet = NODE_SETS[asText(e.category)] ?? 'user_context'
    setActive($, activity('remembering'))
    let shown
    try {
      const args = file ? ['--file', file, '--node-set', nodeSet] : [content, '--node-set', nodeSet]
      const ran = await runWrapper($, 'cognee-remember.sh', args, REMEMBER_TIMEOUT_MS)
      shown = ran ? formatRemember(ran.stdout, ran.exitCode, snapshot?.dataset ?? '') : formatRemember('', 1, '')
    } finally {
      clearActive($)
    }
    if (shown.isError) return { deny: shown.text }
    return { result: shown.text }
  }).catch($ => {
    clearActive($)
    return { deny: 'Cognee remember failed inside the plugin; nothing was stored. Try the cognee-remember skill.' }
  })

  // Fires at each prompt, before the turn starts; next(e) runs the plugin's
  // recall hook (UserPromptSubmit), whose header "Cognee memory: recall N
  // memory ..." may come back in the context. The marker the hook wrote is
  // the fallback. A /clear or /resume changes the session id, so rebind here.
  on('prompt.submit', async ($, e, next) => {
    const id = await $.session.id()
    if (id !== sessionId) await bindSession($)
    recalledThisTurn = -1
    setActive($, activity('recalling'))
    let ran
    try {
      ran = await next(e)
    } finally {
      clearActive($)
    }
    if (ran && 'drop' in ran) return ran
    recalledThisTurn = parseRecallHeader(ran?.context)
    await readPhase($)
    if (recalledThisTurn < 0 && result?.phase === 'recalled' && result.count !== undefined) {
      recalledThisTurn = result.count
    }
    if (!result && recalledThisTurn >= 0) {
      result = {
        kind: 'result',
        phase: 'recalled',
        text: resultLabel('recalled', { count: recalledThisTurn, ok: true }),
        age: 0,
        ok: true,
        count: recalledThisTurn,
      }
      redraw($)
    }
    await refreshSnapshot($)
    // The turn's tool calls are saved as they happen; watch for them.
    await startPoll($, POLL_WINDOW_MS)
    return ran
  }).catch(($, e, next) => next(e))

  // Each tool call's PostToolUse hook saves a trace in the background once the
  // tool ran; next(e) is that run. Watch the marker for the save landing.
  on('tool.call', async ($, e, next) => {
    const ran = await next(e)
    await startPoll($, POLL_WINDOW_MS)
    return ran
  }).catch(($, e, next) => next(e))

  // Compaction runs the plugin's PreCompact hook (the memory anchor) inside next(e).
  on('session.compact', async ($, e, next) => {
    setActive($, activity('anchoring'))
    try {
      return await next(e)
    } finally {
      clearActive($)
      await readPhase($)
    }
  }).catch(($, e, next) => next(e))

  // /clear and /resume end the conversation and start another under a new id,
  // with no session.start: forget what was shown, and let the next prompt rebind.
  on('session.end', async ($, e, next) => {
    if (e.reason === 'clear' || e.reason === 'resume') {
      if (active) clearActive($)
      result = null
      snapshot = null
      recalledThisTurn = -1
      namingDataset = false
      sessionId = ''
    }
    return next(e)
  })

  // One line under the answer: how many memories this turn was answered with.
  // Quiet when nothing was recalled, and never for a subagent's turn.
  // The Stop hook has started saving the answer by now; show it as it lands.
  on('turn.complete', async ($, e, next) => {
    const ran = await next(e)
    if (e.agentId) return ran
    setActive($, activity('saving'))
    await startPoll($, POLL_WINDOW_MS)
    if (!answerLineOn || recalledThisTurn <= 0) return ran
    return { ...ran, text: `${icon}recalled ${plural(recalledThisTurn, 'memory')}` }
  })

  // The status row, above the prompt on the desktop and in a terminal (where
  // the settings-file status line is told to stay quiet for this session), the
  // dataset name a picker. Focus the band with its focus chord (ctrl+x tab) or
  // a click; arrows move, Enter picks, Escape returns to the prompt.
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const theirs = await next(e)
    if (!bandOn || e.props.hasSurvey) return theirs
    if (e.surface !== 'desktop' && e.surface !== 'terminal') return theirs
    const { Box, Text, Select, Input, Button } = $.ui.resolve(e)
    const snap = snapshot
    const parts: RenderElement[] = []
    if (!datasetsListed && !datasetsListing) void loadDatasets($)
    const current = switchTargets.find(r => r.current)?.name ?? snap?.dataset ?? ''
    const canPick = switchTargets.length > 0 && switchTargets.some(r => r.name === current)
    const picker = () =>
      Select({
        key: 'dataset',
        options: [...switchTargets.map(r => ({ value: r.name, label: r.name })), { value: NEW_DATASET, label: NEW_DATASET }],
        value: current,
        onSelect: (value: string) => {
          if (value === NEW_DATASET) {
            namingDataset = true
            redraw($)
            return
          }
          if (value !== current) void switchDataset($, value)
        },
      })
    // The text field the "new dataset…" entry turns the picker into. Enter
    // with a name switches there (creating it); Enter on nothing, or the ✕,
    // brings the picker back.
    const namer = () =>
      Box({
        flexDirection: 'row',
        children: [
          Input({
            key: 'newDataset',
            placeholder: 'new dataset name',
            submitLabel: 'create',
            autoFocus: true,
            onSubmit: (value: string) => {
              namingDataset = false
              if (value.trim()) void switchDataset($, value)
              else redraw($)
            },
          }),
          Text({ children: [' '] }),
          Button({
            key: 'cancelNew',
            label: '✕',
            plain: true,
            dimColor: true,
            onPress: () => {
              namingDataset = false
              redraw($)
            },
          }),
        ],
      })


    if (active) {
      parts.push(Text({ color: 'yellow', children: [FRAMES[frame % FRAMES.length] ?? '⠋'] }))
    } else if (snap?.state === 'fail') {
      parts.push(Text({ color: 'red', bold: true, children: [`✕ (${snap.reason})`] }))
    } else if (snap?.state === 'ok') {
      parts.push(Text({ color: 'green', bold: true, children: ['●'] }))
    } else {
      parts.push(Text({ dimColor: true, children: ['○'] }))
    }
    if (canPick) {
      // The name is the picker: choosing another writable dataset switches
      // the session there, no model turn.
      parts.push(Text({ children: [` ${icon}cognee: `] }))
      parts.push(namingDataset ? namer() : picker())
      parts.push(Text({ children: [' · '] }))
    } else {
      parts.push(Text({ children: [` ${icon}cognee: ${snap?.dataset ?? 'memory'} · `] }))
    }
    parts.push(
      Text({ bold: true, color: snap?.mode === 'cloud' ? 'magenta' : 'cyan', children: [snap?.mode ?? '…'] }),
    )
    if (snap?.credits) parts.push(Text({ children: [` · ${snap.credits}`] }))
    if (active) {
      parts.push(Text({ color: 'yellow', children: [` · ${active.text}`] }))
    } else if (result) {
      parts.push(Text({ dimColor: result.age * 1000 >= DIM_AFTER_MS, children: [` · ${result.text}`] }))
    }
    if (snap?.update) parts.push(Text({ color: 'yellow', children: [` · ${snap.update}`] }))

    const row = Box({ flexDirection: 'row', width: e.props.bodyColumns, children: parts })
    return drawsSomething(theirs) ? Box({ flexDirection: 'column', children: [row, theirs as RenderElement] }) : row
  })

  // While Claude works: "Thinking · 🧠 recalling…". On the desktop for every
  // phase; in a terminal only for the async phases, whose settings hooks show
  // no statusMessage of their own there.
  on('ui.render', { component: 'Spinner' }, async ($, e, next) => {
    if (!spinnerOn || !active) return next(e)
    if (e.surface !== 'desktop' && !ASYNC_PHASES.includes(active.phase) && !OWN_PHASES.includes(active.phase)) return next(e)
    return next({ ...e, props: { ...e.props, suffix: ` · ${icon}${active.text}` } })
  })
}
