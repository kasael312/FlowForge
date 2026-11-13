import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Run } from '../api/types'
import { HistoryPage } from '../pages/HistoryPage'

const run = (id: string, status: Run['status'], flow_id = 'f1'): Run => ({
  id,
  flow_id,
  repo_url: 'https://github.com/a/b',
  branch: 'main',
  task_input: `task ${id}`,
  status,
  error: null,
  started_at: '2026-11-09T10:00:00Z',
  finished_at: null,
  created_at: '2026-11-09T10:00:00Z',
  cost_tokens: 1234,
  cost_usd: 0.0123,
  work_branch: null,
  pr_url: null,
  inputs: null,
  trigger_id: null,
  outputs: null,
  sandbox: null,
})

const json = (body: unknown) => Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } }))

describe('HistoryPage', () => {
  const fetchMock = vi.fn<typeof fetch>()
  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })

  it('lists runs, resolves flow names and filters by status via the URL', async () => {
    fetchMock.mockImplementation((input) => {
      const url = String(input)
      if (url.endsWith('/flows')) return json([{ id: 'f1', name: 'Fix issue', description: '', node_count: 1, created_at: '', updated_at: '' }])
      if (url.includes('status=failed')) return json([run('bbbbbbbb', 'failed')])
      return json([run('aaaaaaaa', 'success'), run('bbbbbbbb', 'failed')])
    })
    const router = createMemoryRouter([{ path: '/history', element: <HistoryPage /> }], { initialEntries: ['/history'] })
    render(<RouterProvider router={router} />)

    expect(await screen.findByText('aaaaaaaa')).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: 'Fix issue' }).length).toBe(2)
    expect(screen.getByText('aaaaaaaa').closest('a')).toHaveAttribute('href', '/runs/aaaaaaaa')

    await userEvent.selectOptions(screen.getByLabelText('Filter by status'), 'failed')
    await waitFor(() => expect(screen.queryByText('aaaaaaaa')).toBeNull())
    expect(screen.getByText('bbbbbbbb')).toBeInTheDocument()
    expect(router.state.location.search).toBe('?status=failed')
    const lastRunsCall = fetchMock.mock.calls.map((c) => String(c[0])).filter((u) => u.includes('/runs')).pop()
    expect(lastRunsCall).toMatch(/\/runs\?status=failed&limit=200$/)
  })

  it('shows empty state', async () => {
    fetchMock.mockImplementation((input) => json(String(input).endsWith('/flows') ? [] : []))
    const router = createMemoryRouter([{ path: '/history', element: <HistoryPage /> }], { initialEntries: ['/history?flow=zzz'] })
    render(<RouterProvider router={router} />)
    expect(await screen.findByText('No runs match.')).toBeInTheDocument()
    expect(fetchMock.mock.calls.map((c) => String(c[0]))).toContainEqual(expect.stringMatching(/\/runs\?flow_id=zzz&limit=200$/))
  })
})
