import { render, screen } from '@testing-library/react'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Stats } from '../api/types'
import { DashboardPage } from '../pages/DashboardPage'

const json = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }))

const stats: Stats = {
  flows: 3,
  runs_total: 12,
  runs_today: 2,
  runs_active: 1,
  success_rate: 0.75,
  total_cost_usd: 0.0625,
  cost_by_model: [
    { model: 'anthropic/claude-x', cost_usd: 0.05, input_tokens: 1000, output_tokens: 200, steps: 3 },
    { model: 'openai/gpt-x', cost_usd: 0.0125, input_tokens: 100, output_tokens: 20, steps: 1 },
  ],
  recent_runs: [
    {
      id: 'abcdef12-xyz',
      flow_id: 'f',
      repo_url: 'https://github.com/a/b',
      branch: 'main',
      task_input: 'fix login',
      status: 'success',
      error: null,
      started_at: '2026-11-09T10:00:00Z',
      finished_at: null,
      created_at: '2026-11-09T10:00:00Z',
      cost_tokens: 10,
      cost_usd: 0.01,
      work_branch: null,
      pr_url: null,
      inputs: null,
      trigger_id: null,
      outputs: null,
      sandbox: null,
    },
  ],
}

function renderPage() {
  const router = createMemoryRouter([{ path: '/', element: <DashboardPage /> }], { initialEntries: ['/'] })
  render(<RouterProvider router={router} />)
}

describe('DashboardPage', () => {
  const fetchMock = vi.fn<typeof fetch>()
  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })

  it('shows tiles and recent runs', async () => {
    fetchMock.mockImplementation(() => json(stats))
    renderPage()
    expect(await screen.findByText('75%')).toBeInTheDocument()
    expect(screen.getByText('Flows').nextElementSibling).toHaveTextContent('3')
    expect(screen.getByText('12 total · 1 active')).toBeInTheDocument()
    expect(screen.getByText('abcdef12').closest('a')).toHaveAttribute('href', '/runs/abcdef12-xyz')
    expect(screen.getByText('fix login')).toBeInTheDocument()
    expect(screen.getByText('LLM spend').nextElementSibling).toHaveTextContent('$0.06')
    const chart = screen.getByTestId('cost-by-model')
    const bars = chart.querySelectorAll('[role="img"]')
    expect(bars).toHaveLength(2)
    expect((bars[0] as HTMLElement).style.width).toBe('100%')
    expect((bars[1] as HTMLElement).style.width).toBe('25%')
    expect(chart).toHaveTextContent('anthropic/claude-x')
    expect(chart).toHaveTextContent('3 steps · 1,200 tok')
  })

  it('shows a dash before any run finished and an empty state', async () => {
    fetchMock.mockImplementation(() => json({ ...stats, success_rate: null, recent_runs: [] }))
    renderPage()
    expect(await screen.findByText('—')).toBeInTheDocument()
    expect(screen.getByText(/No runs yet/)).toBeInTheDocument()
  })
})
