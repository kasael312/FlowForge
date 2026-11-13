import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { FlowSummary } from '../api/types'
import { FlowsPage } from '../pages/FlowsPage'

const summary = (id: string, name: string): FlowSummary => ({
  id,
  name,
  description: '',
  node_count: 3,
  created_at: '2026-11-09T00:00:00Z',
  updated_at: '2026-11-09T00:00:00Z',
})

function jsonResponse(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }))
}

function renderPage() {
  const router = createMemoryRouter(
    [
      { path: '/', element: <FlowsPage /> },
      { path: '/flows/:id', element: <p>editor</p> },
    ],
    { initialEntries: ['/'] },
  )
  render(<RouterProvider router={router} />)
  return router
}

describe('FlowsPage', () => {
  const fetchMock = vi.fn<typeof fetch>()
  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })

  it('lists flows and deletes one after confirmation', async () => {
    fetchMock
      .mockImplementationOnce(() => jsonResponse([summary('a', 'Fix issue'), summary('b', 'Other')]))
      .mockImplementationOnce(() => Promise.resolve(new Response(null, { status: 204 })))
      .mockImplementationOnce(() => jsonResponse([summary('b', 'Other')]))
    vi.stubGlobal('confirm', vi.fn(() => true))
    renderPage()
    expect(await screen.findByText('Fix issue')).toBeInTheDocument()
    expect(screen.getByText('Other')).toBeInTheDocument()

    await userEvent.click(screen.getAllByText('Delete')[0]!)
    await waitFor(() => expect(screen.queryByText('Fix issue')).toBeNull())
    const [delUrl, delInit] = fetchMock.mock.calls[1]!
    expect(delUrl).toMatch(/\/flows\/a$/)
    expect((delInit as RequestInit).method).toBe('DELETE')
  })

  it('creates a flow from a template via the API and navigates to the editor', async () => {
    fetchMock.mockImplementation((input, init) => {
      const url = String(input)
      if (url.endsWith('/flows') && (init?.method ?? 'GET') === 'GET') return jsonResponse([])
      if (url.endsWith('/templates')) return jsonResponse([{ id: 't1', key: 'fix-issue', name: 'Fix issue', description: 'desc', builtin: true, node_count: 5, nodes: [], edges: [], created_at: '' }])
      if (url.endsWith('/templates/t1/use')) return jsonResponse({ id: 'new1', name: 'Fix issue', description: '', nodes: [], edges: [] }, 201)
      return jsonResponse({ detail: 'unexpected' }, 500)
    })
    const router = renderPage()
    expect(await screen.findByText(/No flows yet/)).toBeInTheDocument()

    await userEvent.click(screen.getByText('New flow'))
    await userEvent.click(await screen.findByText('Fix issue'))

    await waitFor(() => expect(router.state.location.pathname).toBe('/flows/new1'))
    const use = fetchMock.mock.calls.find((c) => String(c[0]).endsWith('/templates/t1/use'))!
    expect((use[1] as RequestInit).method).toBe('POST')
  })

  it('shows backend errors', async () => {
    fetchMock.mockImplementationOnce(() => jsonResponse({ detail: 'boom' }, 500))
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent('boom')
  })
})
