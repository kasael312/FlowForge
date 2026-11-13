import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Appointment, Conversation, Message, Overview } from '../api/biz'
import { WorkspaceContext } from '../lib/WorkspaceContext'
import { CalendarPage } from '../pages/biz/CalendarPage'
import { InboxPage } from '../pages/biz/InboxPage'
import { OverviewPage } from '../pages/biz/OverviewPage'

const json = (body: unknown, status = 200) => Promise.resolve(new Response(body === null ? null : JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }))
const ws = { id: 'w1', name: 'Gym A', industry: 'gym', industry_label: 'Gym', timezone: 'Asia/Ho_Chi_Minh', branches: [], onboarding: {}, role: 'owner', created_at: '' }
const wrap = (el: React.ReactElement) =>
  render(
    <WorkspaceContext.Provider value={{ current: ws, all: [ws], reload: async () => {}, switchTo: async () => {} }}>
      <MemoryRouter>{el}</MemoryRouter>
    </WorkspaceContext.Provider>,
  )

const overview: Overview = {
  date: '10/11/2026',
  appointments_today: 3,
  arrived_today: 1,
  no_show_rate: 0.25,
  no_show_30d: 2,
  expiring_memberships: 4,
  unread_conversations: 2,
  ai_suggestions: 1,
  ai_messages_week: 40,
  hours_saved_week: 1.0,
  new_customers_week: 5,
  customers: 120,
  upcoming_today: [{ id: 'a1', time: '15:00', customer_name: 'Lan', service_name: 'PT', staff_name: 'Hoàng', status: 'confirmed' }],
  notifications: [{ id: 'n1', kind: 'report', title: 'Báo cáo cuối ngày', body: 'Hôm nay 3 lịch', created_at: '2026-11-10T12:00:00Z' }],
}

describe('owner UI', () => {
  const fetchMock = vi.fn<typeof fetch>()
  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('Tổng quan shows the tiles, upcoming list and hours saved', async () => {
    fetchMock.mockImplementation((input, init) => {
      const url = String(input)
      if (url.endsWith('/biz/overview')) return json(overview)
      if (url.endsWith('/biz/notifications/n1/read') && init?.method === 'POST') return json({ ...overview.notifications[0], read: true })
      return json({ detail: 'unexpected ' + url }, 500)
    })
    wrap(<OverviewPage />)
    const tiles = within(await screen.findByTestId('overview-tiles'))
    expect(tiles.getByText('Lịch hôm nay').nextSibling).toHaveTextContent('3')
    expect(tiles.getByText('25%')).toBeInTheDocument()
    expect(tiles.getByText('1h')).toBeInTheDocument()
    expect(tiles.getByText('40 tin AI đã xử lý × 1,5 phút')).toBeInTheDocument()
    expect(tiles.getByText('1 gợi ý AI chờ duyệt')).toBeInTheDocument()
    expect(within(screen.getByTestId('upcoming')).getByText('Lan')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Đã đọc' }))
    expect(fetchMock.mock.calls.some((c) => String(c[0]).endsWith('/biz/notifications/n1/read'))).toBe(true)
  })

  it('Hộp thư: approve an edited AI suggestion and take over the conversation', async () => {
    const conv: Conversation = {
      id: 'c1',
      channel: 'zalo',
      external_thread_id: 'z-1',
      customer_id: 'k1',
      customer_name: 'Chị Lan',
      ai_enabled: true,
      unread: true,
      last_message_at: '2026-11-10T08:00:00Z',
      last_inbound_at: '2026-11-10T08:00:00Z',
      last_text: 'Gói PT giá bao nhiêu?',
      window: { open: true, fallback: null },
      created_at: '2026-11-10T08:00:00Z',
    }
    let msgs: Message[] = [
      { id: 'm1', conversation_id: 'c1', direction: 'in', text: 'Gói PT giá bao nhiêu?', sender: 'customer', status: 'sent', created_at: '2026-11-10T08:00:00Z' },
      { id: 'm2', conversation_id: 'c1', direction: 'out', text: 'Gói PT 10 buổi giá 3.000.000đ ạ.', sender: 'ai', status: 'ai_suggested', created_at: '2026-11-10T08:00:05Z' },
    ]
    const calls: string[] = []
    fetchMock.mockImplementation((input, init) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      calls.push(`${method} ${url.replace(/^.*\/biz/, '/biz')}`)
      if (url.includes('/biz/conversations/c1/messages/m2/send')) {
        msgs = msgs.map((m) => (m.id === 'm2' ? { ...m, status: 'sent', sender: 'staff', text: decodeURIComponent(url.split('text=')[1] ?? '').replace(/\+/g, ' ') } : m))
        return json(msgs[1])
      }
      if (url.endsWith('/biz/conversations/c1/messages')) return json(msgs)
      if (url.endsWith('/biz/conversations/c1') && method === 'PUT') {
        const body = JSON.parse(String(init?.body)) as Partial<Conversation>
        Object.assign(conv, body)
        return json(conv)
      }
      if (url.includes('/biz/conversations')) return json([conv])
      if (url.endsWith('/biz/customers/k1')) return json({ id: 'k1', name: 'Chị Lan', phone: '0909xxx', tags: [], source: 'zalo', notes: 'Thích tập sáng', membership: { id: 'ms', status: 'expiring', service_name: 'Gói 1 tháng', end_date: '2026-11-15', sessions_left: null } })
      if (url.endsWith('/biz/customers/k1/memberships')) return json([])
      if (url.endsWith('/biz/customers/k1/appointments')) return json([])
      return json({ detail: 'unexpected ' + url }, 500)
    })
    wrap(<InboxPage />)
    await userEvent.click(await screen.findByRole('button', { name: /Chị Lan/ }))
    const suggestion = within(await screen.findByTestId('suggestion'))
    expect(suggestion.getByText('AI gợi ý')).toBeInTheDocument()
    await userEvent.click(suggestion.getByRole('button', { name: 'Sửa' }))
    const box = suggestion.getByLabelText('Sửa gợi ý')
    await userEvent.clear(box)
    await userEvent.type(box, 'Gói PT 10 buổi 2.800.000đ nếu đăng ký hôm nay ạ.')
    await userEvent.click(suggestion.getByRole('button', { name: 'Gửi' }))
    await waitFor(() => expect(screen.queryByTestId('suggestion')).toBeNull())
    expect(calls.some((c) => c.startsWith('POST /biz/conversations/c1/messages/m2/send?text='))).toBe(true)
    expect(screen.getByText('Gói PT 10 buổi 2.800.000đ nếu đăng ký hôm nay ạ.')).toBeInTheDocument()
    // customer card on the right
    const card = within(screen.getByTestId('customer-card'))
    expect(card.getByText('Thích tập sáng')).toBeInTheDocument()
    expect(card.getByText(/Sắp hết hạn/)).toBeInTheDocument()
    // take over → AI off
    await userEvent.click(screen.getByRole('button', { name: 'Tôi tiếp quản' }))
    expect(await screen.findByRole('button', { name: 'Bật lại AI' })).toBeInTheDocument()
    expect(calls).toContain('PUT /biz/conversations/c1')
  })

  it('Lịch: renders staff columns for the day, marks arrived and books from the modal', async () => {
    const today = new Date()
    const at = (h: number) => {
      const d = new Date(today)
      d.setHours(h, 0, 0, 0)
      return d.toISOString()
    }
    let appts: Appointment[] = [
      { id: 'a1', customer_id: 'k1', customer_name: 'Lan', service_id: 's1', service_name: 'PT', staff_id: 'st1', staff_name: 'Hoàng', branch_id: null, start_at: at(9), end_at: at(10), status: 'booked', source: 'ai', notes: '', created_at: '' },
    ]
    const puts: Record<string, unknown>[] = []
    fetchMock.mockImplementation((input, init) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.includes('/biz/staff')) return json([{ id: 'st1', name: 'Hoàng', role: 'pt', working_hours: {}, branch_id: null, phone: '', active: true, created_at: '' }])
      if (url.includes('/biz/services')) return json([{ id: 's1', name: 'PT', category: 'pt_session', duration_minutes: 60, price: 0, sessions: 1, validity_days: 30, description: '', sale_notes: '', active: true, created_at: '' }])
      if (url.includes('/biz/customers')) return json([{ id: 'k2', name: 'Minh', phone: '0911', tags: [], source: '', notes: '', membership: null }])
      if (url.includes('/biz/appointments/a1') && method === 'PUT') {
        const body = JSON.parse(String(init?.body)) as Record<string, unknown>
        puts.push(body)
        appts = appts.map((a) => (a.id === 'a1' ? { ...a, ...body } : a))
        return json(appts[0])
      }
      if (url.includes('/biz/appointments') && method === 'POST') {
        const body = JSON.parse(String(init?.body)) as Record<string, unknown>
        puts.push(body)
        appts = [...appts, { ...appts[0]!, id: 'a2', customer_id: 'k2', customer_name: 'Minh', start_at: String(body.start_at), end_at: String(body.start_at), status: 'booked' }]
        return json(appts[1], 201)
      }
      if (url.includes('/biz/appointments')) return json(appts)
      return json({ detail: 'unexpected ' + url }, 500)
    })
    wrap(<CalendarPage />)
    const col = within(await screen.findByTestId('col-st1'))
    expect(col.getByText('Hoàng')).toBeInTheDocument()
    expect(screen.getByTestId('col-none')).toBeInTheDocument()
    await userEvent.click(col.getByTestId('appointment'))
    await userEvent.click(screen.getByRole('button', { name: 'Đã đến' }))
    await waitFor(() => expect(puts[0]).toEqual({ status: 'arrived' }))
    // book from the modal
    await userEvent.click(screen.getByRole('button', { name: '+ Đặt lịch' }))
    const dialog = within(await screen.findByRole('dialog', { name: 'Đặt lịch' }))
    await dialog.findByRole('option', { name: /Minh/ })
    await userEvent.selectOptions(dialog.getByLabelText('Chọn khách'), 'k2')
    await userEvent.selectOptions(dialog.getByLabelText('Dịch vụ'), 's1')
    await userEvent.click(dialog.getByRole('button', { name: 'Đặt lịch' }))
    await waitFor(() => expect(puts[1]).toMatchObject({ customer_id: 'k2', service_id: 's1', staff_id: null }))
    expect(await screen.findAllByTestId('appointment')).toHaveLength(2)
  })
})
