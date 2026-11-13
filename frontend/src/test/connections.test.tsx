import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Connector } from '../api/biz'
import { WorkspaceContext } from '../lib/WorkspaceContext'
import { ConnectionsPage } from '../pages/biz/ConnectionsPage'

const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }))
const ws = { id: 'w1', name: 'Gym', industry: 'gym', industry_label: 'Gym', timezone: 'Asia/Ho_Chi_Minh', branches: [], onboarding: {}, role: 'owner', created_at: '' }

describe('ConnectionsPage', () => {
  const fetchMock = vi.fn<typeof fetch>()
  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('shows three connectors, saves Zalo settings and tests the connection', async () => {
    const rows: Connector[] = []
    const puts: unknown[] = []
    fetchMock.mockImplementation((input, init) => {
      const url = String(input)
      if (url.endsWith('/biz/connectors/zalo') && init?.method === 'PUT') {
        const body = JSON.parse(String(init.body)) as { config?: Record<string, unknown>; secrets?: Record<string, string> }
        puts.push(body)
        const row: Connector = { id: 'c1', kind: 'zalo', enabled: true, config: body.config ?? {}, secret_keys: Object.keys(body.secrets ?? {}), webhook_url: 'http://api/hooks/zalo/tok123', verify_token: null, status: 'disconnected', last_checked_at: null, last_error: null, last_event_at: null, updated_at: '' }
        rows.splice(0, rows.length, row)
        return json(row)
      }
      if (url.endsWith('/biz/connectors/zalo/test')) return json({ ...rows[0], status: 'connected', test_message: 'Đã kết nối OA “Gym OA”.' })
      if (url.endsWith('/biz/connectors')) return json(rows)
      return json({ detail: 'unexpected ' + url }, 500)
    })
    render(
      <WorkspaceContext.Provider value={{ current: ws, all: [ws], reload: async () => {}, switchTo: async () => {} }}>
        <ConnectionsPage />
      </WorkspaceContext.Provider>,
    )
    expect(await screen.findByTestId('connector-zalo')).toBeInTheDocument()
    expect(screen.getByTestId('connector-facebook')).toBeInTheDocument()
    expect(screen.getByTestId('connector-google_calendar')).toBeInTheDocument()
    expect(screen.getByTestId('status-zalo')).toHaveTextContent('Chưa thiết lập')

    const card = within(screen.getByTestId('connector-zalo'))
    await userEvent.click(card.getByRole('button', { name: 'Thiết lập' }))
    expect(card.getByText('Tạo ứng dụng trên Zalo for Developers')).toBeInTheDocument()
    await userEvent.type(card.getByLabelText('App ID'), 'app1')
    await userEvent.type(card.getByLabelText('Access token'), 'tok')
    await userEvent.click(card.getByRole('button', { name: 'Thêm mẫu' }))
    await userEvent.type(card.getByLabelText('Template ID 1'), 'T1')
    await userEvent.type(card.getByLabelText('Giá mẫu 1'), '350')
    await userEvent.click(card.getByRole('button', { name: 'Lưu' }))
    await waitFor(() => expect(puts).toHaveLength(1))
    expect(puts[0]).toMatchObject({ config: { app_id: 'app1', zns_templates: [{ id: 'T1', price: 350 }] }, secrets: { access_token: 'tok' }, enabled: true })
    await waitFor(() => expect(screen.getByTestId('webhook-zalo')).toHaveTextContent('http://api/hooks/zalo/tok123'))
    expect(card.getByLabelText(/^Access token/)).toHaveValue('')

    await userEvent.click(card.getByRole('button', { name: 'Kiểm tra kết nối' }))
    expect(await card.findByRole('status')).toHaveTextContent('Đã kết nối OA “Gym OA”.')
  })
})
