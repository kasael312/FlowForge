import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { LogEvent, RunDetail } from '../api/types'
import { initialRunLogState, runLogReducer } from '../lib/runLog'
import { STEP_META, defaultStepData } from '../lib/steps'
import { RunDetailPage } from '../pages/RunDetailPage'

class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  url: string
  onopen: (() => void) | null = null
  onmessage: ((m: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
    queueMicrotask(() => this.onopen?.())
  }
  emit(ev: LogEvent) {
    this.onmessage?.({ data: JSON.stringify(ev) })
  }
  close() {
    this.onclose?.()
  }
}

const detail = (status: RunDetail['status']): RunDetail => ({
  id: 'run12345',
  flow_id: 'f1',
  repo_url: '',
  branch: 'main',
  task_input: '',
  status,
  error: null,
  started_at: '2026-11-10T10:00:00Z',
  finished_at: null,
  created_at: '2026-11-10T10:00:00Z',
  cost_tokens: 0,
  cost_usd: 0,
  work_branch: null,
  pr_url: null,
  inputs: null,
  trigger_id: null,
  outputs: null,
  sandbox: null,
  final_diff: null,
})

const json = (body: unknown) => Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } }))

describe('approval step', () => {
  it('has metadata and defaults', () => {
    expect(STEP_META.approval.usesLlm).toBe(false)
    const d = defaultStepData('approval')
    expect(d.approval_timeout_seconds).toBe(86400)
    expect(d.system_prompt).toContain('{{prev_output}}')
  })

  it('reducer tracks the pending approval and clears it on resolution', () => {
    let s = runLogReducer(initialRunLogState, { event: 'step_start', step_id: 'gate', step_run_id: 'sr2', type: 'approval', attempt: 1 })
    s = runLogReducer(s, { event: 'run_status', status: 'waiting' })
    s = runLogReducer(s, { event: 'approval_requested', step_id: 'gate', step_run_id: 'sr2', question: 'Send it?', prev_output: 'draft', timeout_seconds: 60 })
    expect(s.status).toBe('waiting')
    expect(s.approval).toEqual({ stepId: 'gate', stepRunId: 'sr2', question: 'Send it?', prevOutput: 'draft', timeoutSeconds: 60 })
    expect(s.steps[0]!.status).toBe('waiting')
    s = runLogReducer(s, { event: 'approval_resolved', step_id: 'gate', step_run_id: 'sr2', decision: 'approve', comment: 'ok' })
    expect(s.approval).toBeNull()
    expect(s.steps[0]!.entries.at(-1)).toEqual({ kind: 'log', level: 'info', message: 'approve: ok' })
  })
})

describe('RunDetailPage approval card', () => {
  const fetchMock = vi.fn<typeof fetch>()
  beforeEach(() => {
    FakeWebSocket.instances = []
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('WebSocket', FakeWebSocket)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('shows the question and posts the decision', async () => {
    const posts: { url: string; body: unknown }[] = []
    fetchMock.mockImplementation((input, init) => {
      const url = String(input)
      if (url.endsWith('/steps') || url.endsWith('/artifacts')) return json([])
      if (init?.method === 'POST') {
        posts.push({ url, body: JSON.parse(String(init.body)) })
        return json(detail('running'))
      }
      return json(detail('waiting'))
    })
    const router = createMemoryRouter([{ path: '/runs/:id', element: <RunDetailPage /> }], { initialEntries: ['/runs/run12345'] })
    render(<RouterProvider router={router} />)
    expect(await screen.findByTestId('approval-card')).toBeInTheDocument()
    await waitFor(() => expect(FakeWebSocket.instances.length).toBe(1))
    const ws = FakeWebSocket.instances[0]!
    act(() => {
      ws.emit({ event: 'run_status', status: 'running' })
      ws.emit({ event: 'step_start', step_id: 'gate', step_run_id: 'sr2', type: 'approval', label: 'Human approval', attempt: 1 })
      ws.emit({ event: 'run_status', status: 'waiting' })
      ws.emit({ event: 'approval_requested', step_id: 'gate', step_run_id: 'sr2', question: 'Send this reply to bob@x.io?', prev_output: 'Hi Bob', timeout_seconds: 3600 })
    })
    expect(screen.getByText('Send this reply to bob@x.io?')).toBeInTheDocument()
    expect(screen.getByText('Cancel')).toBeInTheDocument()

    const user = userEvent.setup()
    await user.type(screen.getByLabelText(/Comment/), 'go ahead')
    await user.click(screen.getByRole('button', { name: 'Approve' }))
    await waitFor(() => expect(posts.length).toBe(1))
    expect(posts[0]!.url).toMatch(/\/runs\/run12345\/approve$/)
    expect(posts[0]!.body).toEqual({ decision: 'approve', comment: 'go ahead' })
    act(() => {
      ws.emit({ event: 'approval_resolved', step_id: 'gate', step_run_id: 'sr2', decision: 'approve', comment: 'go ahead' })
      ws.emit({ event: 'run_status', status: 'running' })
    })
    await waitFor(() => expect(screen.queryByTestId('approval-card')).not.toBeInTheDocument())
  })
})
