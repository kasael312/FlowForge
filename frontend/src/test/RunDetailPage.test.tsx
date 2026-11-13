import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { LogEvent, RunDetail, StepRun } from '../api/types'
import { RunDetailPage } from '../pages/RunDetailPage'

/** Minimal WebSocket double: tests push events with `emit`. */
class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  url: string
  onopen: (() => void) | null = null
  onmessage: ((m: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  closed = false
  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
    queueMicrotask(() => this.onopen?.())
  }
  emit(ev: LogEvent) {
    this.onmessage?.({ data: JSON.stringify(ev) })
  }
  close() {
    this.closed = true
    this.onclose?.()
  }
}

const detail = (status: RunDetail['status'], extra: Partial<RunDetail> = {}): RunDetail => ({
  id: 'run12345',
  flow_id: 'f1',
  repo_url: 'https://github.com/a/b',
  branch: 'main',
  task_input: 'fix it',
  status,
  error: null,
  started_at: '2026-11-09T10:00:00Z',
  finished_at: null,
  created_at: '2026-11-09T10:00:00Z',
  cost_tokens: 0,
  cost_usd: 0,
  work_branch: 'flowforge/run12345',
  pr_url: null,
  inputs: null,
  trigger_id: null,
  outputs: null,
  sandbox: null,
  final_diff: null,
  ...extra,
})

const stepRow: StepRun = {
  id: 'sr1',
  run_id: 'run12345',
  step_id: 'plan',
  step_type: 'plan',
  attempt: 1,
  status: 'success',
  messages: [],
  tool_calls: [],
  output: 'ok',
  duration: 1.5,
  tokens: 42,
  model: 'anthropic/claude-test',
  input_tokens: 30,
  output_tokens: 12,
  cost_usd: 0.0003,
  skills: [
    { name: 'researcher', kind: 'persona' },
    { name: 'citation-format', kind: 'skill' },
  ],
  started_at: '2026-11-09T10:00:01Z',
  finished_at: '2026-11-09T10:00:02Z',
}

const json = (body: unknown) => Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } }))

describe('RunDetailPage', () => {
  const fetchMock = vi.fn<typeof fetch>()
  beforeEach(() => {
    FakeWebSocket.instances = []
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('WebSocket', FakeWebSocket)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('renders streamed logs per step, then reloads the diff when the run ends', async () => {
    let runStatus: RunDetail['status'] = 'running'
    fetchMock.mockImplementation((input, init) => {
      const url = String(input)
      if (url.endsWith('/steps')) return json(runStatus === 'running' ? [] : [stepRow])
      if (init?.method === 'POST') return json(detail('cancelled'))
      return json(runStatus === 'running' ? detail('running') : detail('success', { final_diff: 'diff --git a/x b/x\n+added\n-removed', cost_tokens: 42 }))
    })
    const router = createMemoryRouter([{ path: '/runs/:id', element: <RunDetailPage /> }], { initialEntries: ['/runs/run12345'] })
    render(<RouterProvider router={router} />)

    expect(await screen.findByText('Cancel')).toBeInTheDocument()
    await waitFor(() => expect(FakeWebSocket.instances.length).toBe(1))
    const ws = FakeWebSocket.instances[0]!
    expect(ws.url).toMatch(/^ws:\/\/.*\/runs\/run12345\/logs$/)

    act(() => {
      ws.emit({ event: 'run_status', status: 'running' })
      ws.emit({ event: 'step_start', step_id: 'plan', step_run_id: 'sr1', type: 'plan', label: 'Plan it', attempt: 1 })
      ws.emit({ event: 'text', step_id: 'plan', step_run_id: 'sr1', text: 'Reading the code' })
      ws.emit({ event: 'tool_call', step_id: 'plan', step_run_id: 'sr1', tool: 'read_file', input: { path: 'a.py' } })
      ws.emit({ event: 'tool_result', step_id: 'plan', step_run_id: 'sr1', tool: 'read_file', output: 'x = 1', is_error: false, duration: 0.05 })
    })
    expect(screen.getByText('Plan it')).toBeInTheDocument()
    expect(screen.getByText('Reading the code')).toBeInTheDocument()
    expect(screen.getAllByText(/read_file/).length).toBe(2) // call + result
    expect(screen.getByText('x = 1')).toBeInTheDocument()

    runStatus = 'success'
    act(() => {
      ws.emit({ event: 'step_end', step_id: 'plan', step_run_id: 'sr1', status: 'success', duration: 1.5 })
      ws.emit({ event: 'run_status', status: 'success' })
      ws.emit({ event: 'end' })
    })
    await waitFor(() => expect(screen.queryByText('Cancel')).toBeNull())
    expect(screen.getAllByText('success').length).toBeGreaterThan(0)

    await userEvent.click(screen.getByRole('button', { name: 'diff' }))
    const diff = await screen.findByTestId('diff')
    expect(diff).toHaveTextContent('+added')
    expect(diff).toHaveTextContent('-removed')

    await userEvent.click(screen.getByRole('button', { name: 'steps' }))
    const row = (await screen.findByText('1.5s')).closest('tr')!
    expect(row).toHaveTextContent('plan')
    expect(row).toHaveTextContent('anthropic/claude-test')
    expect(row).toHaveTextContent('30 / 12')
    expect(row).toHaveTextContent('$0.0003')
    expect(row).toHaveTextContent('researcher')
    expect(row).toHaveTextContent('citation-format')
  })

  it('cancel button posts to /cancel', async () => {
    fetchMock.mockImplementation((input, init) => {
      const url = String(input)
      if (url.endsWith('/steps')) return json([])
      if (init?.method === 'POST') return json(detail('cancelled'))
      return json(detail('running'))
    })
    const router = createMemoryRouter([{ path: '/runs/:id', element: <RunDetailPage /> }], { initialEntries: ['/runs/run12345'] })
    render(<RouterProvider router={router} />)
    await userEvent.click(await screen.findByText('Cancel'))
    await waitFor(() => expect(screen.getByText('cancelled')).toBeInTheDocument())
    expect(fetchMock.mock.calls.some((c) => String(c[0]).endsWith('/runs/run12345/cancel'))).toBe(true)
  })
})
