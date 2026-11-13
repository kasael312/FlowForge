import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Automation } from '../api/biz'
import { WorkspaceContext } from '../lib/WorkspaceContext'
import { AutomationsPage } from '../pages/biz/AutomationsPage'

const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }))
const ws = { id: 'w1', name: 'Gym A', industry: 'gym', industry_label: 'Gym', timezone: 'Asia/Ho_Chi_Minh', branches: [], onboarding: {}, role: 'owner', created_at: '' }
const card = (over: Partial<Automation> = {}): Automation => ({
  id: 'f1',
  key: 'renewal_reminder',
  name: 'Nhắc gia hạn gói',
  description: 'Nhắc khách gia hạn.',
  trigger_label: 'Tự động mỗi ngày',
  enabled: true,
  config_schema: [
    { key: 'days_before_1', label: 'Nhắc lần 1 (ngày trước hết hạn)', type: 'number', options: [], default: 7, help: '' },
    { key: 'offer_enabled', label: 'Kèm ưu đãi', type: 'bool', options: [], default: false, help: '' },
    { key: 'mode', label: 'Cách gửi', type: 'select', options: [{ value: 'suggest', label: 'Gợi ý' }, { value: 'auto', label: 'Tự gửi' }], default: 'suggest', help: '' },
    { key: 'message_template', label: 'Mẫu tin', type: 'textarea', options: [], default: 'x', help: 'Biến: {{ ten_khach }}' },
  ],
  config: { days_before_1: 7, offer_enabled: false, mode: 'suggest', message_template: 'Chào {{ ten_khach }}' },
  runs_7d: 3,
  failed_7d: 1,
  last_run_at: '2026-11-10T02:00:00Z',
  last_error: 'khách chưa có hội thoại',
  needs_model: false,
  ...over,
})

describe('AutomationsPage', () => {
  const fetchMock = vi.fn<typeof fetch>()
  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('toggles a card, edits config from the schema and shows the dry run', async () => {
    let rows = [card(), card({ id: 'f2', key: 'daily_report', name: 'Báo cáo cuối ngày', enabled: false, config_schema: [], config: {}, runs_7d: 0, failed_7d: 0, last_run_at: null, last_error: null })]
    const puts: unknown[] = []
    fetchMock.mockImplementation((input, init) => {
      const url = String(input)
      if (url.includes('/biz/automations/renewal_reminder/try')) return json({ run_id: 'r1', status: 'success', error: null, messages: ['[chạy thử · chế độ suggest] Chào Nguyễn Văn Thử'], inputs: { customer_id: 'c1' } })
      if (url.includes('/biz/automations/') && init?.method === 'PUT') {
        const body = JSON.parse(String(init.body)) as Partial<Automation>
        puts.push(body)
        rows = rows.map((r) => (url.endsWith(`/${r.key}`) ? { ...r, ...body, config: { ...r.config, ...(body.config ?? {}) } } : r))
        return json(rows.find((r) => url.endsWith(`/${r.key}`)))
      }
      if (url.endsWith('/biz/automations')) return json(rows)
      return json({ detail: 'unexpected ' + url }, 500)
    })
    render(
      <WorkspaceContext.Provider value={{ current: ws, all: [ws], reload: async () => {}, switchTo: async () => {} }}>
        <AutomationsPage />
      </WorkspaceContext.Provider>,
    )
    const c = within(await screen.findByTestId('automation-renewal_reminder'))
    expect(c.getByTestId('stats-renewal_reminder')).toHaveTextContent('7 ngày qua: 3 lần chạy, 1 lỗi')
    expect(c.getByText('Đang bật')).toBeInTheDocument()
    await userEvent.click(c.getByRole('switch', { name: 'Bật Nhắc gia hạn gói' }))
    await waitFor(() => expect(puts).toContainEqual({ enabled: false }))

    await userEvent.click(c.getByRole('button', { name: 'Cấu hình' }))
    const num = c.getByLabelText('Nhắc lần 1 (ngày trước hết hạn)')
    await userEvent.clear(num)
    await userEvent.type(num, '5')
    await userEvent.click(c.getByLabelText('Kèm ưu đãi'))
    await userEvent.selectOptions(c.getByLabelText('Cách gửi'), 'auto')
    await userEvent.click(c.getByRole('button', { name: 'Lưu cấu hình' }))
    await waitFor(() => expect(puts).toContainEqual({ config: { days_before_1: 5, offer_enabled: true, mode: 'auto', message_template: 'Chào {{ ten_khach }}' } }))

    await userEvent.click(c.getByRole('button', { name: 'Chạy thử' }))
    const modal = await screen.findByTestId('trial-result')
    expect(modal).toHaveTextContent('Chào Nguyễn Văn Thử')
    expect(modal).toHaveTextContent('thành công')
  })
})
