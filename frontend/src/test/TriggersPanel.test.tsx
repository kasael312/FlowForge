import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Trigger } from '../api/types'
import { TriggersPanel } from '../components/TriggersPanel'

const json = (body: unknown, status = 200) => Promise.resolve(new Response(body === null ? null : JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }))

function renderPanel() {
  const router = createMemoryRouter([{ path: '/flows/:id', element: <TriggersPanel flowId="f1" /> }, { path: '/runs/:id', element: <p>run page</p> }], { initialEntries: ['/flows/f1'] })
  render(<RouterProvider router={router} />)
  return router
}

describe('TriggersPanel', () => {
  const fetchMock = vi.fn<typeof fetch>()
  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('confirm', vi.fn(() => true))
  })

  it('adds a schedule and a webhook, shows the secret, toggles and fires', async () => {
    let rows: Trigger[] = []
    fetchMock.mockImplementation((input, init) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.endsWith('/flows/f1/triggers') && method === 'GET') return json(rows)
      if (url.endsWith('/flows/f1/triggers') && method === 'POST') {
        const body = JSON.parse(String(init?.body)) as { type: Trigger['type']; name: string; config: Record<string, unknown> }
        const t: Trigger = {
          id: `t${rows.length + 1}`,
          flow_id: 'f1',
          type: body.type,
          name: body.name,
          config: body.config,
          enabled: true,
          last_fired: null,
          last_error: null,
          created_at: '',
          webhook: body.type === 'webhook' ? { url: 'http://localhost:8800/hooks/t2', signature_header: 'x-flowforge-signature', secret: 's3cr3t-token', example: 'curl ...' } : null,
          watch_folder: null,
        }
        rows = [...rows, t]
        return json(t, 201)
      }
      if (url.endsWith('/triggers/t1') && method === 'PUT') {
        rows = rows.map((t) => (t.id === 't1' ? { ...t, enabled: false } : t))
        return json(rows[0])
      }
      if (url.endsWith('/triggers/t1/fire')) return json({ id: 'run9', status: 'queued' }, 202)
      return json({ detail: 'unexpected ' + url }, 500)
    })
    const router = renderPanel()
    expect(await screen.findByText(/No triggers yet/)).toBeInTheDocument()

    await userEvent.clear(screen.getByLabelText(/Cron expression/))
    await userEvent.type(screen.getByLabelText(/Cron expression/), '*/15 * * * *')
    await userEvent.type(screen.getByLabelText('Name'), 'quarter-hourly')
    await userEvent.type(screen.getByLabelText('Default task'), 'tick')
    await userEvent.click(screen.getByRole('button', { name: 'Add trigger' }))
    expect(await screen.findByText('quarter-hourly')).toBeInTheDocument()
    expect(screen.getByText('cron: */15 * * * *')).toBeInTheDocument()
    const post = JSON.parse(String((fetchMock.mock.calls.find((c) => (c[1] as RequestInit)?.method === 'POST')![1] as RequestInit).body))
    expect(post).toMatchObject({ type: 'schedule', name: 'quarter-hourly', config: { cron: '*/15 * * * *', task: 'tick', branch: 'main' } })

    await userEvent.selectOptions(screen.getByLabelText('Type'), 'webhook')
    expect(screen.getByText(/HMAC-SHA256/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Add trigger' }))
    expect(await screen.findByTestId('webhook-secret')).toHaveTextContent('s3cr3t-token')
    expect(screen.getByText(/hooks\/t2/)).toBeInTheDocument()

    await userEvent.click(screen.getAllByRole('button', { name: 'Disable' })[0]!)
    await waitFor(() => expect(screen.getByTestId('trigger-t1')).toHaveTextContent('disabled'))
    await userEvent.click(screen.getAllByRole('button', { name: 'Fire now' })[0]!)
    await waitFor(() => expect(router.state.location.pathname).toBe('/runs/run9'))
  })
})
