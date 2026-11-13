import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AdminWorkspace, BillingState, Invoice } from '../api/billing'
import { WorkspaceContext } from '../lib/WorkspaceContext'
import { AdminPage } from '../pages/AdminPage'
import { BillingPage } from '../pages/settings/BillingPage'

const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }))
const ws = { id: 'w1', name: 'Gym A', industry: 'gym', industry_label: 'Gym', timezone: 'Asia/Ho_Chi_Minh', branches: [], onboarding: {}, role: 'owner', created_at: '' }
const plans = [
  { key: 'trial', name: 'Dùng thử', price_vnd: 0, ai_conversations_month: 100, branches: 5, description: '30 ngày', trial_days: 30 },
  { key: 'basic', name: 'Cơ bản', price_vnd: 499000, ai_conversations_month: 500, branches: 1, description: '1 chi nhánh', trial_days: 0 },
  { key: 'pro', name: 'Pro', price_vnd: 1290000, ai_conversations_month: 3000, branches: 5, description: '5 chi nhánh', trial_days: 0 },
]
const state = (over: Partial<BillingState> = {}): BillingState => ({
  plan: plans[0]!,
  subscribed_plan: 'trial',
  status: 'trial',
  trial_ends_at: '2026-12-05T00:00:00Z',
  current_period_end: null,
  days_left: 5,
  usage: { ai_conversations: 85, ai_conversations_limit: 100, branches: 1, branches_limit: 5, month: '11/2026' },
  warnings: ['Gói dùng thử còn 5 ngày. Chọn gói để không gián đoạn trợ lý AI.'],
  blocked: false,
  plans,
  gateways: [{ key: 'bank_transfer', name: 'Chuyển khoản' }],
  pending_invoice: null,
  ...over,
})
const invoice: Invoice = {
  id: 'i1',
  number: 'FF202611-0001',
  plan: 'pro',
  plan_name: 'Pro',
  months: 3,
  amount_vnd: 3870000,
  status: 'pending',
  gateway: 'bank_transfer',
  transfer_code: 'FFABC123',
  payment: { bank_name: 'Vietcombank', account: '0011002233', account_name: 'CONG TY FLOWFORGE', qr_image_url: 'https://img.vietqr.io/image/x.png', configured: true },
  paid_at: null,
  created_at: '2026-11-10T00:00:00Z',
}

describe('billing', () => {
  const fetchMock = vi.fn<typeof fetch>()
  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('shows plan, usage and creates a VietQR invoice', async () => {
    let pending: Invoice | null = null
    const posts: unknown[] = []
    fetchMock.mockImplementation((input, init) => {
      const url = String(input)
      if (url.endsWith('/biz/billing/invoices') && init?.method === 'POST') {
        posts.push(JSON.parse(String(init.body)))
        pending = invoice
        return json(invoice, 201)
      }
      if (url.endsWith('/biz/billing/invoices')) return json(pending ? [pending] : [])
      if (url.endsWith('/biz/billing')) return json(state({ pending_invoice: pending }))
      return json({ detail: 'unexpected ' + url }, 500)
    })
    render(
      <WorkspaceContext.Provider value={{ current: ws, all: [ws], reload: async () => {}, switchTo: async () => {} }}>
        <MemoryRouter>
          <BillingPage />
        </MemoryRouter>
      </WorkspaceContext.Provider>,
    )
    const current = within(await screen.findByTestId('billing-current'))
    expect(current.getAllByText('Dùng thử')).toHaveLength(2) // plan name + status badge
    expect(current.getByRole('progressbar', { name: 'Hội thoại AI' })).toHaveAttribute('aria-valuenow', '85')
    expect(screen.getByRole('status')).toHaveTextContent('còn 5 ngày')
    await userEvent.selectOptions(screen.getByLabelText('Số tháng'), '3')
    const pro = within(screen.getByTestId('plan-pro'))
    expect(pro.getByRole('button', { name: /Chọn gói/ })).toHaveTextContent('3.870.000 đ')
    await userEvent.click(pro.getByRole('button', { name: /Chọn gói/ }))
    const card = within(await screen.findByTestId('pending-invoice'))
    expect(posts[0]).toEqual({ plan: 'pro', months: 3, gateway: 'bank_transfer' })
    expect(card.getByTestId('transfer-code')).toHaveTextContent('FFABC123')
    expect(card.getByRole('img', { name: 'Mã VietQR' })).toHaveAttribute('src', 'https://img.vietqr.io/image/x.png')
    expect(card.getByRole('link', { name: 'Tải hoá đơn PDF' })).toHaveAttribute('href', expect.stringContaining('/biz/billing/invoices/i1/pdf'))
    expect(screen.getByText('FF202611-0001')).toBeInTheDocument()
  })

  it('admin panel confirms a payment and extends a trial', async () => {
    const row: AdminWorkspace = {
      id: 'w1',
      name: 'Gym A',
      industry: 'gym',
      owner_email: 'o@example.com',
      members: 1,
      customers: 5,
      created_at: '2026-11-01T00:00:00Z',
      last_activity_at: null,
      plan: 'trial',
      plan_name: 'Dùng thử',
      status: 'trial',
      trial_ends_at: '2026-12-01T00:00:00Z',
      current_period_end: null,
      days_left: 20,
      usage: { ai_conversations: 3, ai_conversations_limit: 100, branches: 1, branches_limit: 5, month: '11/2026' },
      open_alerts: 1,
    }
    let inv = { ...invoice, workspace_id: 'w1', workspace_name: 'Gym A' }
    const calls: string[] = []
    fetchMock.mockImplementation((input, init) => {
      const url = String(input)
      calls.push(`${init?.method ?? 'GET'} ${url.replace(/^.*\/admin/, '/admin')}`)
      if (url.endsWith('/admin/summary')) return json({ workspaces: 1, trials: 1, trials_ending_7d: 0, paid: 0, expired: 0, pending_invoices: inv.status === 'pending' ? 1 : 0, open_alerts: 1 })
      if (url.includes('/admin/workspaces/w1/extend-trial')) {
        row.days_left = 34
        return json(row)
      }
      if (url.includes('/admin/workspaces/w1/logs')) return json([{ time: '2026-11-10T01:00:00Z', kind: 'alert', text: 'Mất kết nối Zalo OA', detail: {} }])
      if (url.includes('/admin/workspaces')) return json([row])
      if (url.endsWith('/admin/invoices/i1/confirm')) {
        inv = { ...inv, status: 'paid', paid_at: '2026-11-10T02:00:00Z' }
        return json(inv)
      }
      if (url.includes('/admin/invoices')) return json([inv])
      if (url.includes('/admin/alerts')) return json([{ id: 'a1', workspace_id: 'w1', workspace_name: 'Gym A', key: 'connector:zalo', title: 'Mất kết nối Zalo OA', body: 'token', detail: {}, read: false, created_at: '2026-11-10T01:00:00Z' }])
      return json({ detail: 'unexpected ' + url }, 500)
    })
    vi.spyOn(window, 'prompt').mockReturnValue('14')
    render(
      <MemoryRouter>
        <AdminPage />
      </MemoryRouter>,
    )
    const table = within(await screen.findByTestId('admin-workspaces'))
    expect(table.getByText('o@example.com')).toBeInTheDocument()
    expect(table.getByText('3/100')).toBeInTheDocument()
    await userEvent.click(table.getByRole('button', { name: 'Kéo dài thử' }))
    await waitFor(() => expect(calls).toContain('POST /admin/workspaces/w1/extend-trial'))
    expect(await table.findByText('còn 34 ngày')).toBeInTheDocument()
    await userEvent.click(table.getByRole('button', { name: /Log/ }))
    expect(within(await screen.findByTestId('admin-logs')).getByText('Mất kết nối Zalo OA')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Đóng' }))

    await userEvent.click(screen.getByRole('tab', { name: /Thanh toán/ }))
    const invoices = within(screen.getByTestId('admin-invoices'))
    expect(invoices.getByText('FFABC123')).toBeInTheDocument()
    await userEvent.click(invoices.getByRole('button', { name: 'Đã nhận tiền' }))
    await waitFor(() => expect(calls).toContain('POST /admin/invoices/i1/confirm'))
    expect(await invoices.findByText(/Đã trả/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('tab', { name: /Cảnh báo/ }))
    expect(within(screen.getByTestId('admin-alerts')).getByText(/Gym A · Mất kết nối Zalo OA/)).toBeInTheDocument()
  })
})
