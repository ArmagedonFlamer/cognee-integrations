// Tests for the live-activity module: what it shows while the memory hooks
// work and after they finish. Everything the module reaches (the activity
// marker file, the renderer process, the session id) is answered here, so the
// test runs with no file system, process or network.
import { expect, mock, test } from 'claude-code/testing'

const SESSION = 's1'
const HOME = '/home/tester'
const MARKER_PATH = `${HOME}/.cognee-plugin/claude-code/phase/${SESSION}.json`

const SNAPSHOT = {
  enabled: true,
  state: 'ok',
  reason: '',
  dataset: 'agent_sessions',
  mode: 'local',
  credits: '',
  update: '',
  text: 'cognee: agent_sessions · local',
}

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

const SPINNER = {
  component: 'Spinner' as const,
  props: { word: 'Thinking', message: null, suffix: '…', mode: 'thinking' },
}

function marker(nowMs: number, body: Record<string, unknown>): string {
  return JSON.stringify({ session_key: SESSION, ...body, _now: nowMs })
}

// The world beneath the plugin: a clock that moves only when a test moves it,
// the home directory, the session id, the marker file and the renderer.
function wire(on: Parameters<Parameters<typeof test>[1]>[1], state: { marker: string }) {
  const clock = mock.clock(on, { now: 1_700_000_000_000 })
  mock.env(on, { HOME })
  // The engine's own behaviour at the bottom of each chain the module joins.
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  on('turn.complete', () => ({ text: '' }))
  on('session.end', ($, e) => ({ sessionId: e.sessionId }))
  // What the engine draws itself: the spinner's word and suffix, nothing in the band.
  on('ui.render', ($, e) => {
    const { Text } = $.ui.resolve(e)
    if (e.component === 'Spinner') return Text({ children: [`${e.props.word}${e.props.suffix}`] })
    return Text({ children: [''] })
  })
  on('session.id', () => ({ value: SESSION }))
  on('fs.read', ($, e) => {
    if (e.path !== MARKER_PATH) throw new Error(`unexpected read of ${e.path}`)
    return { value: state.marker }
  })
  on('fs.write', () => ({ value: undefined }))
  on('process.run', () => ({
    value: { exitCode: 0, stdout: JSON.stringify(SNAPSHOT), stderr: '', isStdoutTruncated: false, isStderrTruncated: false },
  }))
  return clock
}

test('the desktop band shows the row the terminal status line shows', async ($, on) => {
  const state = { marker: '' }
  const clock = wire(on, state)
  const now = clock.now()
  state.marker = marker(now, { active: null, results: { recalled: { count: 3, at: now / 1000, ok: true } } })
  await $.session.start({ cwd: '/work', surface: 'desktop', isInteractive: true })

  const ui = await $.ui.mount({ plugin: 'cognee-memory', surface: 'desktop', ...BAND })
  expect(await ui.find({ type: 'Text', text: /cognee: agent_sessions/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /recalled 3 memories/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /●/ })).toBeDefined()
  await ui.unmount()
})

test('in a terminal the band draws the same row, so the status line below can step aside', async ($, on) => {
  const state = { marker: '' }
  const clock = wire(on, state)
  const now = clock.now()
  state.marker = marker(now, { active: null, results: { recalled: { count: 3, at: now / 1000, ok: true } } })
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })

  const ui = await $.ui.mount({ plugin: 'cognee-memory', surface: 'terminal', ...BAND })
  expect(await ui.find({ type: 'Text', text: /cognee: agent_sessions/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /recalled 3 memories/ })).toBeDefined()
  await ui.unmount()
})

test('a prompt shows recalling while the recall hook runs, then what it recalled', async ($, on) => {
  const state = { marker: '' }
  const clock = wire(on, state)
  const now = clock.now()
  state.marker = marker(now, { active: null, results: {} })
  let seenWhileRecalling: string | undefined
  const engine = $
  // The recall settings hook's place in the chain: beneath prompt.submit, it
  // writes the marker and answers with the recall header as context. While it
  // runs, the spinner is drawn through the engine to see what the module adds.
  on('prompt.submit', async (_, e) => {
    const ui = await engine.ui.mount({ plugin: 'cognee-memory', surface: 'desktop', ...SPINNER })
    seenWhileRecalling = JSON.stringify(await ui.drawn())
    await ui.unmount()
    state.marker = marker(now, { active: null, results: { recalled: { count: 2, at: now / 1000, ok: true } } })
    return { text: e.text, context: ['Cognee memory: recall 2 memory; saved last turn 1 prompt / 0 trace / 1 answer'] }
  })
  await $.session.start({ cwd: '/work', surface: 'desktop', isInteractive: true })
  await $.prompt.submit({ text: 'what did we decide about the cache?' })

  expect(seenWhileRecalling).toContain('recalling…')

  const ui = await $.ui.mount({ plugin: 'cognee-memory', surface: 'desktop', ...BAND })
  expect(await ui.find({ type: 'Text', text: /recalled 2 memories/ })).toBeDefined()
  await ui.unmount()

  const done = await $.turn.complete({ turnId: 't1', answer: 'We kept the cache.', durationMs: 1200, isAborted: false, reason: 'answer' })
  expect(done.text).toBe('🧠 recalled 2 memories')
})

test('nothing recalled: no line under the answer, and the band says so plainly', async ($, on) => {
  const state = { marker: '' }
  const clock = wire(on, state)
  const now = clock.now()
  state.marker = marker(now, { active: null, results: {} })
  on('prompt.submit', ($, e) => {
    state.marker = marker(now, { active: null, results: { recalled: { count: 0, at: now / 1000, ok: true } } })
    return { text: e.text }
  })
  await $.session.start({ cwd: '/work', surface: 'desktop', isInteractive: true })
  await $.prompt.submit({ text: 'hello there' })

  const ui = await $.ui.mount({ plugin: 'cognee-memory', surface: 'desktop', ...BAND })
  expect(await ui.find({ type: 'Text', text: /nothing relevant yet/ })).toBeDefined()
  await ui.unmount()

  const done = await $.turn.complete({ turnId: 't1', answer: 'Hi.', durationMs: 300, isAborted: false, reason: 'answer' })
  expect(done.text ?? '').not.toContain('recalled')
})

test('the answer line reads the count from the marker when the context has no header', async ($, on) => {
  const state = { marker: '' }
  const clock = wire(on, state)
  const now = clock.now()
  state.marker = marker(now, { active: null, results: {} })
  on('prompt.submit', ($, e) => {
    state.marker = marker(now, { active: null, results: { recalled: { count: 4, at: now / 1000, ok: true } } })
    return { text: e.text }
  })
  await $.session.start({ cwd: '/work', surface: 'desktop', isInteractive: true })
  await $.prompt.submit({ text: 'where is the retry logic?' })

  const done = await $.turn.complete({ turnId: 't1', answer: 'In the client.', durationMs: 900, isAborted: false, reason: 'answer' })
  expect(done.text).toBe('🧠 recalled 4 memories')
})

test('after the turn the band shows saving until the marker says what was saved', async ($, on) => {
  const state = { marker: '' }
  const clock = wire(on, state)
  const now = clock.now()
  state.marker = marker(now, { active: null, results: {} })
  await $.session.start({ cwd: '/work', surface: 'desktop', isInteractive: true })
  await $.turn.complete({ turnId: 't1', answer: 'Done.', durationMs: 900, isAborted: false, reason: 'answer' })

  let ui = await $.ui.mount({ plugin: 'cognee-memory', surface: 'desktop', ...BAND })
  expect(await ui.find({ type: 'Text', text: /saving…/ })).toBeDefined()
  await ui.unmount()

  // The Stop hook lands its save; the next poll tick picks it up.
  state.marker = marker(now, { active: null, results: { saved: { count: 3, at: now / 1000, ok: true } } })
  await clock.advance(1100)
  ui = await $.ui.mount({ plugin: 'cognee-memory', surface: 'desktop', ...BAND })
  expect(await ui.find({ type: 'Text', text: /saving…/ })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /saved 3 memories/ })).toBeDefined()
  await ui.unmount()
})

test('a stale active phase is ignored rather than shown forever', async ($, on) => {
  const state = { marker: '' }
  const clock = wire(on, state)
  const now = clock.now()
  const stale = (now - 5 * 60_000) / 1000
  state.marker = marker(now, { active: { phase: 'recalling', detail: '', started_at: stale, updated_at: stale }, results: { saved: { count: 4, at: stale, ok: true } } })
  await $.session.start({ cwd: '/work', surface: 'desktop', isInteractive: true })

  const ui = await $.ui.mount({ plugin: 'cognee-memory', surface: 'desktop', ...BAND })
  expect(await ui.find({ type: 'Text', text: /recalling/ })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /saved 4 memories/ })).toBeDefined()
  await ui.unmount()
})

test('the spinner only gains a suffix in a terminal for the async phases', async ($, on) => {
  const state = { marker: '' }
  const clock = wire(on, state)
  const now = clock.now()
  state.marker = marker(now, { active: { phase: 'saving', detail: '', started_at: now / 1000, updated_at: now / 1000 }, results: {} })
  await $.session.start({ cwd: '/work', surface: 'terminal', isInteractive: true })

  const ui = await $.ui.mount({ plugin: 'cognee-memory', surface: 'terminal', ...SPINNER })
  expect(JSON.stringify(await ui.drawn())).toContain('saving…')
  await ui.unmount()
})
