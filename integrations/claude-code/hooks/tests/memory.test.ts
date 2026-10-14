// Tests for the dataset picker, the /cognee-dataset command and the two tools the model
// calls directly. Every process the module would run (the plugin's own
// scripts) is answered here by argv, so nothing touches a server or a file.
import { expect, mock, test } from 'claude-code/testing'

const SESSION = 's1'
const HOME = '/home/tester'
const PLUGIN = 'cognee-memory'

const SNAPSHOT = {
  enabled: true,
  state: 'ok',
  reason: '',
  dataset: 'agent_sessions',
  mode: 'local',
  credits: '',
  update: '',
  text: 'cognee: agent_sessions · local',
  counts: {
    reminiscence: 0,
    ideas: null,
    imprints: null,
    bar: '',
    tokens_saved: 'tokens saved n/a',
    tokens_saved_short: 'saved n/a',
    show_counts: true,
  },
}

const SWITCH_LIST = {
  current: 'agent_sessions',
  session_id: 'cognee-1',
  datasets: [
    { name: 'agent_sessions', id: 'a1', writable: true, current: true },
    { name: 'demo', id: 'd2', writable: true, current: false },
  ],
  hidden_readonly: 0,
  filtered: true,
}

const HITS = [
  { source: 'graph', text: 'Project tempo replicates with raft.' },
  { source: 'graph', text: 'The cache was kept after the migration.' },
]

const BAND = {
  component: 'AbovePrompt' as const,
  props: {
    hasSurvey: false,
    isWorking: false,
    maxRows: 4,
    bodyColumns: 120,
    scroll: { offset: 0, bodyRows: 4 },
    view: {},
  },
}

type Call = { argv: readonly string[]; stdin?: string }
type Write = { path: string; text: string }

function ok(stdout: string) {
  return { value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
}

// The world beneath the plugin, with every script answered by its name.
function wire(
  on: Parameters<Parameters<typeof test>[1]>[1],
  calls: Call[],
  answers: Partial<Record<string, (argv: readonly string[]) => { value: unknown }>> = {},
  writes: Write[] = [],
  registrations: { tools: string[]; commands: string[] } = { tools: [], commands: [] },
) {
  const clock = mock.clock(on, { now: 1_700_000_000_000 })
  mock.env(on, { HOME })
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  on('turn.complete', () => ({ text: '' }))
  on('session.end', ($, e) => ({ sessionId: e.sessionId }))
  on('ui.render', ($, e) => {
    const { Text } = $.ui.resolve(e)
    return Text({ children: [''] })
  })
  on('session.id', () => ({ value: SESSION }))
  // No marker and no env file: an empty read (a throw from a bottom hook
  // inside a timer would end the timer in the kit, which no real file does).
  on('fs.read', () => ({ value: '' }))
  // The registrations session start makes; a test that inspects them
  // passes `registrations` and reads it back.
  on('tool.register', ($, e) => {
    registrations.tools.push(e.name)
    return { value: { tool: `mcp__${PLUGIN}__${e.name}` } }
  })
  on('command.register', ($, e) => {
    registrations.commands.push(e.name)
    return { value: { command: e.name } }
  })
  on('fs.write', ($, e) => {
    writes.push({ path: e.path, text: e.text })
    return { value: undefined }
  })
  on('process.run', ($, e) => {
    calls.push({ argv: e.argv, stdin: e.init?.stdin })
    const script = e.argv.find(a => a.includes('/scripts/')) ?? ''
    const name = script.slice(script.lastIndexOf('/') + 1)
    const answer = answers[name]
    if (answer) return answer(e.argv)
    switch (name) {
      case 'cognee_statusline_render.py':
        return ok(JSON.stringify(SNAPSHOT))
      case 'switch-dataset.py':
        if (e.argv.includes('--list')) return ok(JSON.stringify(SWITCH_LIST))
        return ok(JSON.stringify({ switched: true, dataset: e.argv[2], session_id: 'cognee-2', previous: { dataset: 'agent_sessions', synced: true } }))
      case 'cognee-search.sh':
        return ok(JSON.stringify(HITS))
      case 'recall-trim.py':
        // The trimmer answers with the list it was given, each text marked.
        return ok(JSON.stringify((JSON.parse(e.init?.stdin ?? '[]') as { text: string }[]).map(h => ({ ...h, text: `${h.text} [trimmed]` }))))
      case 'cognee-remember.sh':
        return ok(JSON.stringify({ ok: true, dataset_id: 'a1', queryable: true, wait_outcome: 'completed' }))
      default:
        throw new Error(`unexpected process: ${e.argv.join(' ')}`)
    }
  })
  return clock
}

// The plugin's cross-session store, as a map the test can read back.
function fakeStore(on: Parameters<Parameters<typeof test>[1]>[1], entries: Record<string, unknown> = {}) {
  const store = new Map(Object.entries(entries))
  on('store.get', ($, e) => ({ value: store.get(e.key) }))
  on('store.set', ($, e) => {
    store.set(e.key, e.value)
    return { value: undefined }
  })
  return store
}

function argvOf(calls: Call[], script: string): readonly string[] | undefined {
  return calls.find(c => c.argv.some(a => a.endsWith(`/scripts/${script}`)))?.argv
}

function argsAfter(argv: readonly string[], script: string): string[] {
  const i = argv.findIndex(a => a.endsWith(`/scripts/${script}`))
  return argv.slice(i + 1)
}

// A registered tool answers with the text the model reads; a failure is a
// deny, which reaches the model as an error result.
function resultText(ran: { result?: unknown; deny?: string; text?: string; isError?: unknown }): { text: string; isError: boolean } {
  if (typeof ran.deny === 'string') return { text: ran.deny, isError: true }
  if (ran.isError) return { text: ran.text ?? String(ran.result ?? ''), isError: true }
  return { text: typeof ran.result === 'string' ? ran.result : String(ran.text ?? ''), isError: false }
}

test('session start registers the two tools and the commands', async ($, on) => {
  const calls: Call[] = []
  const registrations = { tools: [] as string[], commands: [] as string[] }
  wire(on, calls, {}, [], registrations)
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
  expect(registrations.tools).toEqual(['search', 'remember'])
  expect(registrations.commands).toEqual(['cognee-dataset', 'cognee-tour'])
})

test('the tools row can be switched off', { options: { tools: false } }, async ($, on) => {
  const calls: Call[] = []
  const registrations = { tools: [] as string[], commands: [] as string[] }
  wire(on, calls, {}, [], registrations)
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
  expect(registrations.tools).toEqual([])
})

test('the search tool runs the search wrapper and hands the model the hits as text', async ($, on) => {
  const calls: Call[] = []
  wire(on, calls)
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })

  const ran = await $.tool.call({ tool: `mcp__${PLUGIN}__search`, query: 'how does tempo replicate?', top_k: 5 })
  const argv = argvOf(calls, 'cognee-search.sh')
  expect(argv).toBeDefined()
  expect(argv![0]).toBe('bash')
  expect(argsAfter(argv!, 'cognee-search.sh')).toEqual(['how does tempo replicate?', '5', '--graph'])

  const shown = resultText(ran)
  expect(shown.isError).toBe(false)
  expect(shown.text).toContain('2 results from the active dataset.')
  // Trimmed the way the prompt hook trims, by the plugin's own script, with the query.
  expect(shown.text).toContain('Project tempo replicates with raft. [trimmed]')
  const trim = argvOf(calls, 'recall-trim.py')
  expect(argsAfter(trim!, 'recall-trim.py')).toEqual(['--query', 'how does tempo replicate?'])
})

test('the search tool reads another dataset by id and the code graph by scope', async ($, on) => {
  const calls: Call[] = []
  wire(on, calls)
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })

  await $.tool.call({ tool: `mcp__${PLUGIN}__search`, query: 'raft', dataset_id: 'd2' })
  expect(argsAfter(argvOf(calls, 'cognee-search.sh')!, 'cognee-search.sh')).toEqual(['raft', '10', '--graph', '--dataset-id', 'd2'])

  calls.length = 0
  const ran = await $.tool.call({ tool: `mcp__${PLUGIN}__search`, query: 'process_payment', scope: 'code' })
  expect(argsAfter(argvOf(calls, 'cognee-search.sh')!, 'cognee-search.sh')).toEqual(['process_payment', '10', '--code'])
  expect(resultText(ran).text).toContain('from the code graph')
})

test('an unreachable server is an error result, never an empty answer', async ($, on) => {
  const calls: Call[] = []
  wire(on, calls, { 'cognee-search.sh': () => ({ value: { exitCode: 1, stdout: 'UNREACHABLE\n', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }) })
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })

  const ran = await $.tool.call({ tool: `mcp__${PLUGIN}__search`, query: 'anything' })
  const shown = resultText(ran)
  expect(shown.isError).toBe(true)
  expect(shown.text).toContain('unreachable')
})

test('an empty list from a reachable server is authoritative', async ($, on) => {
  const calls: Call[] = []
  wire(on, calls, { 'cognee-search.sh': () => ok('[]') })
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })

  const shown = resultText(await $.tool.call({ tool: `mcp__${PLUGIN}__search`, query: 'nothing here' }))
  expect(shown.isError).toBe(false)
  expect(shown.text).toContain('No memories matched.')
})

test('the remember tool maps the category to its node set and reports the write', async ($, on) => {
  const calls: Call[] = []
  wire(on, calls)
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })

  const ran = await $.tool.call({ tool: `mcp__${PLUGIN}__remember`, content: 'The retry budget lives in the client.', category: 'project' })
  expect(argsAfter(argvOf(calls, 'cognee-remember.sh')!, 'cognee-remember.sh')).toEqual([
    'The retry budget lives in the client.',
    '--node-set',
    'project_docs',
  ])
  const shown = resultText(ran)
  expect(shown.isError).toBe(false)
  expect(shown.text).toContain('Stored in agent_sessions')
  expect(shown.text).toContain('recall will find it')
})

test('the remember tool stores a file under its real name', async ($, on) => {
  const calls: Call[] = []
  wire(on, calls)
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })

  await $.tool.call({ tool: `mcp__${PLUGIN}__remember`, file: 'src/payments.py', category: 'project' })
  expect(argsAfter(argvOf(calls, 'cognee-remember.sh')!, 'cognee-remember.sh')).toEqual(['--file', 'src/payments.py', '--node-set', 'project_docs'])
})

test('a write the server never received is reported as not stored', async ($, on) => {
  const calls: Call[] = []
  wire(on, calls, { 'cognee-remember.sh': () => ({ value: { exitCode: 1, stdout: 'UNREACHABLE\n', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }) })
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })

  const shown = resultText(await $.tool.call({ tool: `mcp__${PLUGIN}__remember`, content: 'x' }))
  expect(shown.isError).toBe(true)
  expect(shown.text).toContain('nothing was stored')
})

test('/cognee-dataset <name> switches from the prompt', async ($, on) => {
  const calls: Call[] = []
  wire(on, calls)
  const toasts: string[] = []
  on('ui.toast', ($, e) => {
    toasts.push(e.text)
    return { value: undefined }
  })
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })

  const answer = await $.command.run({ command: 'cognee-dataset', args: 'demo' })
  expect(answer.text).toBe('Cognee: switched to demo.')
  expect(argsAfter(argvOf(calls, 'switch-dataset.py')!, 'switch-dataset.py')).toEqual(['demo', '--json', '--session-key', SESSION])
  expect(toasts.some(t => t.includes('switched to demo'))).toBe(true)
  // The status row is re-read after a switch.
  expect(argvOf(calls, 'cognee_statusline_render.py')).toBeDefined()
})

test('/cognee-dataset alone lists the datasets the picker offers', async ($, on) => {
  const calls: Call[] = []
  wire(on, calls)
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })

  const answer = await $.command.run({ command: 'cognee-dataset', args: '' })
  expect(answer.text).toContain('* agent_sessions (current)')
  expect(answer.text).toContain('- demo')
  expect(argvOf(calls, 'switch-dataset.py')).toContain('--list')
})

test('a switch the script refuses is reported, not hidden', async ($, on) => {
  const calls: Call[] = []
  wire(on, calls, {
    'switch-dataset.py': argv =>
      argv.includes('--list') ? ok(JSON.stringify(SWITCH_LIST)) : ok(JSON.stringify({ error: 'Selected dataset is not writable', code: 5 })),
  })
  const toasts: string[] = []
  on('ui.toast', ($, e) => {
    toasts.push(e.text)
    return { value: undefined }
  })
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })

  const answer = await $.command.run({ command: 'cognee-dataset', args: 'demo' })
  expect(answer.text).toBe('Cognee: Selected dataset is not writable.')
  expect(toasts.some(t => t.includes('✕ Selected dataset is not writable'))).toBe(true)
})

test('on the desktop band the dataset name is a picker that switches on pick', async ($, on) => {
  const calls: Call[] = []
  wire(on, calls)
  const toasts: string[] = []
  on('ui.toast', ($, e) => {
    toasts.push(e.text)
    return { value: undefined }
  })
  await $.session.start({ cwd: '/work', surface: 'desktop', isInteractive: true })
  // The listing started at session start, unawaited; let it land.
  expect(argvOf(calls, 'switch-dataset.py')).toContain('--list')

  const ui = await $.ui.mount({ plugin: PLUGIN, surface: 'desktop', ...BAND })
  const picker = await ui.find({ type: 'Select', key: 'dataset' })
  expect(picker).toBeDefined()
  expect(picker!.props.value).toBe('agent_sessions')
  expect((picker!.props.options as { value: string }[]).map(o => o.value)).toEqual(['agent_sessions', 'demo', '+ new dataset…'])
  // The name is drawn once, as the picker, not again as text.
  expect(await ui.find({ type: 'Text', text: /agent_sessions/ })).toBeUndefined()

  calls.length = 0
  await $.ui.select({ plugin: PLUGIN, key: 'dataset', value: 'demo' })
  expect(argsAfter(argvOf(calls, 'switch-dataset.py')!, 'switch-dataset.py')).toEqual(['demo', '--json', '--session-key', SESSION])
  expect(toasts.some(t => t.includes('switched to demo'))).toBe(true)
  await ui.unmount()
})

test('the picker ends with a "new dataset" entry that turns it into a text field', async ($, on) => {
  const calls: Call[] = []
  wire(on, calls)
  const toasts: string[] = []
  on('ui.toast', ($, e) => {
    toasts.push(e.text)
    return { value: undefined }
  })
  await $.session.start({ cwd: '/work', surface: 'desktop', isInteractive: true })

  let ui = await $.ui.mount({ plugin: PLUGIN, surface: 'desktop', ...BAND })
  const picker = await ui.find({ type: 'Select', key: 'dataset' })
  const values = (picker!.props.options as { value: string }[]).map(o => o.value)
  expect(values).toEqual(['agent_sessions', 'demo', '+ new dataset…'])
  expect(await ui.find({ type: 'Input', key: 'newDataset' })).toBeUndefined()

  // Picking it switches nothing: it asks for a name.
  calls.length = 0
  await $.ui.select({ plugin: PLUGIN, key: 'dataset', value: '+ new dataset…' })
  expect(argvOf(calls, 'switch-dataset.py')).toBeUndefined()
  expect(await ui.find({ type: 'Input', key: 'newDataset' })).toBeDefined()
  expect(await ui.find({ type: 'Select', key: 'dataset' })).toBeUndefined()

  // The name typed is the switch (the script creates a dataset that does not exist).
  await $.ui.input({ plugin: PLUGIN, key: 'newDataset', text: 'fresh-start' })
  expect(argsAfter(argvOf(calls, 'switch-dataset.py')!, 'switch-dataset.py')).toEqual(['fresh-start', '--json', '--session-key', SESSION])
  expect(toasts.some(t => t.includes('switched to fresh-start'))).toBe(true)
  expect(await ui.find({ type: 'Select', key: 'dataset' })).toBeDefined()
  expect(await ui.find({ type: 'Input', key: 'newDataset' })).toBeUndefined()
  await ui.unmount()
})

test('an empty name, or the cancel mark, brings the picker back without a switch', async ($, on) => {
  const calls: Call[] = []
  wire(on, calls)
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ plugin: PLUGIN, surface: 'terminal', ...BAND })
  await ui.unmount()
  const again = await $.ui.mount({ plugin: PLUGIN, surface: 'terminal', ...BAND })

  await $.ui.select({ plugin: PLUGIN, key: 'dataset', value: '+ new dataset…' })
  calls.length = 0
  await $.ui.input({ plugin: PLUGIN, key: 'newDataset', text: '   ' })
  expect(argvOf(calls, 'switch-dataset.py')).toBeUndefined()
  expect(await again.find({ type: 'Select', key: 'dataset' })).toBeDefined()

  await $.ui.select({ plugin: PLUGIN, key: 'dataset', value: '+ new dataset…' })
  expect(await again.find({ type: 'Input', key: 'newDataset' })).toBeDefined()
  await $.ui.press({ plugin: PLUGIN, key: 'cancelNew' })
  expect(argvOf(calls, 'switch-dataset.py')).toBeUndefined()
  expect(await again.find({ type: 'Select', key: 'dataset' })).toBeDefined()
  await again.unmount()
})

test('a name the switch script refuses is reported and the picker is back', async ($, on) => {
  const calls: Call[] = []
  wire(on, calls, {
    'switch-dataset.py': argv =>
      argv.includes('--list')
        ? ok(JSON.stringify(SWITCH_LIST))
        : ok(JSON.stringify({ error: "invalid dataset name 'my notes': cognee rejects spaces and dots (try 'my_notes')", code: 1 })),
  })
  const toasts: string[] = []
  on('ui.toast', ($, e) => {
    toasts.push(e.text)
    return { value: undefined }
  })
  await $.session.start({ cwd: '/work', surface: 'desktop', isInteractive: true })
  const ui = await $.ui.mount({ plugin: PLUGIN, surface: 'desktop', ...BAND })
  await $.ui.select({ plugin: PLUGIN, key: 'dataset', value: '+ new dataset…' })
  await $.ui.input({ plugin: PLUGIN, key: 'newDataset', text: 'my notes' })
  expect(toasts.some(t => t.includes("✕ invalid dataset name 'my notes'") && t.includes("try 'my_notes'"))).toBe(true)
  expect(await ui.find({ type: 'Select', key: 'dataset' })).toBeDefined()
  await ui.unmount()
})

test('the row is the whole band when the engine draws nothing of its own, so no blank line', async ($, on) => {
  const calls: Call[] = []
  wire(on, calls)
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ plugin: PLUGIN, surface: 'terminal', ...BAND })
  const drawn = (await ui.drawn()) as { type?: string; props?: { flexDirection?: string } }
  // The wire's engine band is an empty Text: it is left out, so the tree is
  // our one row, not a column with an empty row under it.
  expect(drawn.props?.flexDirection).toBe('row')
  await ui.unmount()
})

test('the first session on a machine gets the tour under the row, once', async ($, on) => {
  const calls: Call[] = []
  wire(on, calls)
  const store = fakeStore(on)
  const toasts: string[] = []
  on('ui.toast', ($, e) => {
    toasts.push(e.text)
    return { value: undefined }
  })
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })

  const ui = await $.ui.mount({ plugin: PLUGIN, surface: 'terminal', ...BAND })
  expect(await ui.find({ type: 'Text', text: /the dataset name is a picker/ })).toBeDefined()
  expect(await ui.find({ type: 'Button', key: 'tourNext' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: / cognee / })).toBeDefined()

  await $.ui.press({ plugin: PLUGIN, key: 'tourNext' })
  expect(await ui.find({ type: 'Text', text: /means memory is connected/ })).toBeDefined()
  await $.ui.press({ plugin: PLUGIN, key: 'tourNext' })
  expect(await ui.find({ type: 'Text', text: /\/cognee-tour replays this/ })).toBeDefined()
  expect((await ui.find({ type: 'Button', key: 'tourNext' }))!.props.label).toBe('done')
  await $.ui.press({ plugin: PLUGIN, key: 'tourNext' })
  expect(await ui.find({ type: 'Button', key: 'tourNext' })).toBeUndefined()
  expect(toasts.some(t => t.includes('/cognee-tour replays it'))).toBe(true)
  await ui.unmount()

  const seen = store.get('tour') as { version: number; finished: boolean }
  expect(seen.version).toBe(1)
  expect(seen.finished).toBe(true)
})

test('skip ends the tour and it is not shown again', async ($, on) => {
  const calls: Call[] = []
  wire(on, calls)
  const store = fakeStore(on)
  await $.session.start({ cwd: '/work', surface: 'desktop', isInteractive: true })
  const ui = await $.ui.mount({ plugin: PLUGIN, surface: 'desktop', ...BAND })
  expect(await ui.find({ type: 'Button', key: 'tourSkip' })).toBeDefined()
  await $.ui.press({ plugin: PLUGIN, key: 'tourSkip' })
  expect(await ui.find({ type: 'Button', key: 'tourSkip' })).toBeUndefined()
  await ui.unmount()
  const seen = store.get('tour') as { version: number; finished: boolean }
  expect(seen.finished).toBe(false)
})

test('a machine that has seen this tour gets no hints', async ($, on) => {
  const calls: Call[] = []
  wire(on, calls)
  fakeStore(on, { tour: { version: 1, at: 1, finished: true } })
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ plugin: PLUGIN, surface: 'terminal', ...BAND })
  expect(await ui.find({ type: 'Button', key: 'tourNext' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: / cognee / })).toBeDefined()
  await ui.unmount()
})

test('/cognee-tour replays it, and the tour row in /config turns it off', async ($, on) => {
  const calls: Call[] = []
  wire(on, calls)
  fakeStore(on, { tour: { version: 1, at: 1, finished: true } })
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
  const answer = await $.command.run({ command: 'cognee-tour', args: '' })
  expect(answer.text).toContain('tour')
  const ui = await $.ui.mount({ plugin: PLUGIN, surface: 'terminal', ...BAND })
  expect(await ui.find({ type: 'Text', text: /the dataset name is a picker/ })).toBeDefined()
  await ui.unmount()
})

test('with the tour off a first session gets no hints', { options: { tour: false } }, async ($, on) => {
  const calls: Call[] = []
  wire(on, calls)
  fakeStore(on)
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ plugin: PLUGIN, surface: 'terminal', ...BAND })
  expect(await ui.find({ type: 'Button', key: 'tourNext' })).toBeUndefined()
  await ui.unmount()
})

const FAIL_SNAPSHOT = { ...SNAPSHOT, state: 'fail', reason: 'incorrect_cognee_api_key', text: '✕ (incorrect_cognee_api_key) cognee: agent_sessions · local' }
const FIX_PROPS = { title: 'Fix memory', isFocused: true, bodyColumns: 100, placement: 'inline' as const, scroll: { offset: 0, bodyRows: 18 }, view: {} }

test('a red ✕ (reason) on the row has a see-fix button beside it that opens the guide', async ($, on) => {
  const calls: Call[] = []
  wire(on, calls, { 'cognee_statusline_render.py': () => ok(JSON.stringify(FAIL_SNAPSHOT)) })
  const opened: string[] = []
  on('ui.open', ($, e) => {
    opened.push(e.id)
    return { value: { isPlaced: true } }
  })
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
  const band = await $.ui.mount({ plugin: PLUGIN, surface: 'terminal', ...BAND })
  // The verdict stays red text; the small button beside it is the press.
  const verdict = await band.find({ type: 'Text', text: /✕ \(incorrect_cognee_api_key\)/ })
  expect(verdict!.props.color).toBe('red')
  const button = await band.find({ type: 'Button', key: 'fix' })
  expect(button!.props.label).toBe('see fix')
  expect(button!.props.variant).toBe('secondary')
  await $.ui.press({ plugin: PLUGIN, key: 'fix' })
  expect(opened).toEqual(['cognee-fix'])
  await band.unmount()

  for (const surface of ['terminal', 'desktop'] as const) {
    const pane = await $.ui.mount({ plugin: PLUGIN, surface, component: 'Pane', requestId: 'cognee-fix', props: FIX_PROPS })
    expect(await pane.find({ type: 'Text', text: /rejected the plugin's API key/ })).toBeDefined()
    expect(await pane.find({ type: 'Markdown', text: /delete `~\/.cognee-plugin\/api_key.json`/ })).toBeDefined()
    expect(await pane.find({ type: 'Input', key: 'secret' })).toBeDefined()
    expect(await pane.find({ type: 'Button', key: 'doctor' })).toBeDefined()
    await pane.unmount()
  }
})

test('a healthy row has no fix button', async ($, on) => {
  const calls: Call[] = []
  wire(on, calls)
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
  const band = await $.ui.mount({ plugin: PLUGIN, surface: 'terminal', ...BAND })
  expect(await band.find({ type: 'Button', key: 'fix' })).toBeUndefined()
  await band.unmount()
})

test('the fix pane runs the doctor and shows its report', async ($, on) => {
  const calls: Call[] = []
  wire(on, calls, {
    'cognee_statusline_render.py': () => ok(JSON.stringify({ ...FAIL_SNAPSHOT, reason: 'unreachable' })),
    'cognee-doctor.sh': () => ok('Mode: Local\nServer: http://localhost:8011\nHealth: unreachable\n'),
  })
  on('ui.open', () => ({ value: { isPlaced: true } }))
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
  const band = await $.ui.mount({ plugin: PLUGIN, surface: 'terminal', ...BAND })
  await $.ui.press({ plugin: PLUGIN, key: 'fix' })
  await band.unmount()

  const pane = await $.ui.mount({ plugin: PLUGIN, surface: 'desktop', component: 'Pane', requestId: 'cognee-fix', props: FIX_PROPS })
  expect(await pane.find({ type: 'Text', text: /cannot be reached/ })).toBeDefined()
  // No key field for a reason no single value fixes.
  expect(await pane.find({ type: 'Input', key: 'secret' })).toBeUndefined()
  await $.ui.press({ plugin: PLUGIN, key: 'doctor' })
  expect(argvOf(calls, 'cognee-doctor.sh')).toBeDefined()
  expect(await pane.find({ type: 'Code', text: /Health: unreachable/ })).toBeDefined()
  await pane.unmount()
})

test('a key pasted into the fix pane is appended to ~/.cognee/.env', async ($, on) => {
  const calls: Call[] = []
  const writes: Write[] = []
  wire(on, calls, { 'cognee_statusline_render.py': () => ok(JSON.stringify(FAIL_SNAPSHOT)) }, writes)
  on('ui.open', () => ({ value: { isPlaced: true } }))
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
  const band = await $.ui.mount({ plugin: PLUGIN, surface: 'terminal', ...BAND })
  await $.ui.press({ plugin: PLUGIN, key: 'fix' })
  await band.unmount()

  const pane = await $.ui.mount({ plugin: PLUGIN, surface: 'terminal', component: 'Pane', requestId: 'cognee-fix', props: FIX_PROPS })
  writes.length = 0
  await $.ui.input({ plugin: PLUGIN, key: 'secret', text: '  ck_live_123  ' })
  const env = writes.find(w => w.path === `${HOME}/.cognee/.env`)
  expect(env).toBeDefined()
  // The file did not exist (the wire's fs.read throws), so it is created with the one line.
  expect(env!.text).toBe('COGNEE_API_KEY="ck_live_123"\n')
  expect(await pane.find({ type: 'Text', text: /COGNEE_API_KEY saved/ })).toBeDefined()
  await pane.unmount()
})

test('the row keeps checking after start until the connection marker lands', async ($, on) => {
  const calls: Call[] = []
  let renders = 0
  const clock = wire(on, calls, {
    // The first snapshot comes before the SessionStart hook wrote its marker.
    'cognee_statusline_render.py': () => {
      renders += 1
      return ok(JSON.stringify(renders < 3 ? { ...SNAPSHOT, state: '' } : SNAPSHOT))
    },
  })
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })

  let ui = await $.ui.mount({ plugin: PLUGIN, surface: 'terminal', ...BAND })
  expect(await ui.find({ type: 'Text', text: /○/ })).toBeDefined()
  await ui.unmount()

  // Ten seconds on, the snapshot was retaken and the marker is there.
  for (let i = 0; i < 11; i += 1) await clock.advance(1000)
  ui = await $.ui.mount({ plugin: PLUGIN, surface: 'terminal', ...BAND })
  expect(await ui.find({ type: 'Text', text: /●/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /○/ })).toBeUndefined()
  await ui.unmount()
  expect(renders).toBeGreaterThanOrEqual(3)
})

test('a session that reports no surface at start (the Desktop app) lists when the band is first drawn', async ($, on) => {
  const calls: Call[] = []
  wire(on, calls)
  await $.session.start({ cwd: '/work', surface: null, isInteractive: true })
  expect(argvOf(calls, 'switch-dataset.py')).toBeUndefined()

  let ui = await $.ui.mount({ plugin: PLUGIN, surface: 'desktop', ...BAND })
  await ui.unmount()
  expect(argvOf(calls, 'switch-dataset.py')).toContain('--list')
  ui = await $.ui.mount({ plugin: PLUGIN, surface: 'desktop', ...BAND })
  expect(await ui.find({ type: 'Select', key: 'dataset' })).toBeDefined()
  await ui.unmount()
  // Drawn again, the band does not list again.
  expect(calls.filter(c => c.argv.some(a => a.endsWith('/scripts/switch-dataset.py'))).length).toBe(1)
})

test('in a terminal the band is the whole status row, the dataset name a picker', async ($, on) => {
  const calls: Call[] = []
  wire(on, calls)
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })

  let ui = await $.ui.mount({ plugin: PLUGIN, surface: 'terminal', ...BAND })
  // The first draw starts the listing (which lands before the mount settles).
  expect(await ui.find({ type: 'Text', text: / cognee / })).toBeDefined()
  await ui.unmount()
  expect(argvOf(calls, 'switch-dataset.py')).toContain('--list')

  ui = await $.ui.mount({ plugin: PLUGIN, surface: 'terminal', ...BAND })
  const picker = await ui.find({ type: 'Select', key: 'dataset' })
  expect(picker).toBeDefined()
  expect(picker!.props.value).toBe('agent_sessions')
  expect(await ui.find({ type: 'Text', text: /local/ })).toBeDefined()
  
  calls.length = 0
  await $.ui.select({ plugin: PLUGIN, key: 'dataset', value: 'demo' })
  expect(argsAfter(argvOf(calls, 'switch-dataset.py')!, 'switch-dataset.py')).toEqual(['demo', '--json', '--session-key', SESSION])
  await ui.unmount()
})

test('a terminal session is marked so the settings status line stays quiet for it', async ($, on) => {
  const calls: Call[] = []
  const writes: Write[] = []
  wire(on, calls, {}, writes)
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
  const marker = writes.find(w => w.path === `${HOME}/.cognee-plugin/claude-code/band/${SESSION}.json`)
  expect(marker).toBeDefined()
  expect(JSON.parse(marker!.text).session_id).toBe(SESSION)
})

test('with the band off no session is marked: the settings status line stays', { options: { band: false } }, async ($, on) => {
  const calls: Call[] = []
  const writes: Write[] = []
  wire(on, calls, {}, writes)
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
  expect(writes).toEqual([])
  const ui = await $.ui.mount({ plugin: PLUGIN, surface: 'terminal', ...BAND })
  expect(await ui.find({ type: 'Text', text: /cognee/ })).toBeUndefined()
  await ui.unmount()
})

test('in a terminal nothing is listed at session start: the first draw of the band does that', async ($, on) => {
  const calls: Call[] = []
  wire(on, calls)
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })
  expect(argvOf(calls, 'switch-dataset.py')).toBeUndefined()
})

test('the band falls back to the name as text until the datasets are listed', async ($, on) => {
  const calls: Call[] = []
  wire(on, calls, { 'switch-dataset.py': () => ok(JSON.stringify({ error: 'no launch record found', code: 2 })) })
  await $.session.start({ cwd: '/work', surface: 'desktop', isInteractive: true })

  const ui = await $.ui.mount({ plugin: PLUGIN, surface: 'desktop', ...BAND })
  expect(await ui.find({ type: 'Select', key: 'dataset' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: / cognee / })).toBeDefined()
  await ui.unmount()
})
