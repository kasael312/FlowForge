import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Customer, ImportPreview } from '../api/biz'
import { ImportWizard } from '../components/biz/ImportWizard'
import { CustomersPage } from '../pages/biz/CustomersPage'

const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }))
const customer = (over: Partial<Customer> = {}): Customer => ({
  id: 'c1',
  name: 'Nguyễn Minh Anh',
  phone: '0901234567',
  zalo_id: null,
  fb_psid: null,
  tags: ['vip'],
  source: 'manual',
  notes: '',
  created_at: '2026-11-10T00:00:00Z',
  last_visit_at: '2026-11-09T10:00:00Z',
  membership: { id: 'm1', status: 'expiring', service_name: 'PT 10 buổi', end_date: null, sessions_left: 2 },
  ...over,
})

describe('CustomersPage', () => {
  const fetchMock = vi.fn<typeof fetch>()
  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('lists customers with membership badges, searches and adds one', async () => {
    const rows: Customer[] = [customer()]
    const posts: unknown[] = []
    fetchMock.mockImplementation((input, init) => {
      const url = String(input)
      if (url.includes('/biz/customers') && init?.method === 'POST') {
        const body = JSON.parse(String(init.body)) as { name: string; phone: string; tags: string[] }
        posts.push(body)
        rows.push(customer({ id: 'c2', name: body.name, phone: body.phone, tags: body.tags, membership: null }))
        return json(rows.at(-1), 201)
      }
      if (url.includes('/biz/customers')) {
        const q = new URL(url).searchParams.get('q') ?? ''
        return json(rows.filter((r) => !q || r.name.toLowerCase().includes(q.toLowerCase()) || r.phone.includes(q)))
      }
      return json({ detail: 'unexpected ' + url }, 500)
    })
    render(<CustomersPage />)
    const list = await screen.findByTestId('customer-list')
    expect(within(list).getByText('Nguyễn Minh Anh')).toBeInTheDocument()
    expect(within(list).getByText(/Sắp hết hạn · còn 2 buổi/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Thêm khách' }))
    await userEvent.type(screen.getByLabelText('Tên'), 'Trần Văn B')
    await userEvent.type(screen.getByLabelText('Số điện thoại'), '0912 345 678')
    await userEvent.type(screen.getByLabelText(/Nhãn/), 'mới, vip')
    await userEvent.click(screen.getByRole('button', { name: 'Lưu' }))
    await waitFor(() => expect(posts).toHaveLength(1))
    expect(posts[0]).toMatchObject({ name: 'Trần Văn B', phone: '0912 345 678', tags: ['mới', 'vip'] })
    await waitFor(() => expect(within(screen.getByTestId('customer-list')).getByText('Trần Văn B')).toBeInTheDocument())

    await userEvent.type(screen.getByLabelText('Tìm khách'), 'Trần')
    await waitFor(() => expect(within(screen.getByTestId('customer-list')).queryByText('Nguyễn Minh Anh')).toBeNull())
  })
})

describe('ImportWizard', () => {
  const fetchMock = vi.fn<typeof fetch>()
  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('previews, lets the user fix the mapping and imports', async () => {
    const preview: ImportPreview = {
      entity: 'customers',
      columns: ['Họ tên', 'Điện thoại', 'Cột lạ'],
      sample: [{ 'Họ tên': 'A', 'Điện thoại': '0901', 'Cột lạ': 'x' }],
      row_count: 12,
      fields: [
        { key: 'name', label: 'Tên khách', required: true },
        { key: 'phone', label: 'Số điện thoại', required: false },
        { key: 'notes', label: 'Ghi chú', required: false },
      ],
      suggested_mapping: { phone: 'Điện thoại' },
    }
    const applied: string[] = []
    fetchMock.mockImplementation((input) => {
      const url = String(input)
      if (url.includes('/biz/import/preview')) return json(preview)
      if (url.includes('/biz/import?')) {
        applied.push(decodeURIComponent(new URL(url).searchParams.get('mapping') ?? ''))
        return json({ entity: 'customers', created: 11, updated: 0, skipped: 1, errors: ['dòng 5: thiếu tên'] })
      }
      return json({ detail: 'unexpected' }, 500)
    })
    const onDone = vi.fn()
    render(<ImportWizard entity="customers" onClose={vi.fn()} onDone={onDone} />)
    const file = new File(['a,b\n1,2'], 'khach.csv', { type: 'text/csv' })
    await userEvent.upload(screen.getByTestId('import-file'), file)
    expect(await screen.findByText(/Phát hiện/)).toHaveTextContent('12')
    expect(within(screen.getByTestId('import-preview')).getByText('0901')).toBeInTheDocument()
    const importBtn = screen.getByRole('button', { name: /Nhập 12 dòng/ })
    expect(importBtn).toBeDisabled()
    expect(screen.getByText(/Còn thiếu: Tên khách/)).toBeInTheDocument()
    await userEvent.selectOptions(screen.getByLabelText('Tên khách'), 'Họ tên')
    expect(importBtn).toBeEnabled()
    await userEvent.click(importBtn)
    expect(await screen.findByTestId('import-result')).toHaveTextContent('Đã tạo 11, cập nhật 0, bỏ qua 1')
    expect(screen.getByText('dòng 5: thiếu tên')).toBeInTheDocument()
    expect(JSON.parse(applied[0]!)).toEqual({ phone: 'Điện thoại', name: 'Họ tên' })
    expect(onDone).toHaveBeenCalled()
  })
})
