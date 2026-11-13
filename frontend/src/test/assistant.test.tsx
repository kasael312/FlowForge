import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AiSettings, KbDocument } from '../api/biz'
import { WorkspaceContext } from '../lib/WorkspaceContext'
import { AssistantPage } from '../pages/biz/AssistantPage'

const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }))
const ws = { id: 'w1', name: 'Gym A', industry: 'gym', industry_label: 'Gym', timezone: 'Asia/Ho_Chi_Minh', branches: [], onboarding: {}, role: 'owner', created_at: '' }
const settings: AiSettings = {
  forbidden: ['Không giảm giá'],
  address_self: 'em',
  address_customer: 'anh/chị',
  friendliness: 2,
  style_examples: [],
  medical_guard: true,
  handoff_keywords: [],
  handoff_message: 'Dạ câu này {self} chuyển nhân viên ạ',
  handoff_preview: 'Dạ câu này em chuyển nhân viên ạ',
  guard_categories: ['y tế / bệnh lý', 'chấn thương'],
  updated_at: '',
}

describe('AssistantPage', () => {
  const fetchMock = vi.fn<typeof fetch>()
  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('pastes a document, saves forbidden rules and previews a question', async () => {
    const docs: KbDocument[] = []
    let current = { ...settings }
    const puts: unknown[] = []
    fetchMock.mockImplementation((input, init) => {
      const url = String(input)
      if (url.endsWith('/biz/kb/documents/text') && init?.method === 'POST') {
        const body = JSON.parse(String(init.body)) as { title: string; kind: string; text: string }
        docs.unshift({ id: `d${docs.length + 1}`, title: body.title, kind: body.kind as KbDocument['kind'], kind_label: 'Câu hỏi thường gặp', filename: 'x.txt', size: body.text.length, status: 'ready', error: null, chunk_count: 1, embedder: 'lexical', preview: body.text, created_at: '2026-11-10T00:00:00Z' })
        return json(docs[0], 201)
      }
      if (url.endsWith('/biz/kb/documents')) return json(docs)
      if (url.endsWith('/biz/ai/settings') && init?.method === 'PUT') {
        const body = JSON.parse(String(init.body)) as Partial<AiSettings>
        puts.push(body)
        current = { ...current, ...body }
        return json(current)
      }
      if (url.endsWith('/biz/ai/settings')) return json(current)
      if (url.endsWith('/biz/kb/preview')) return json({ guard: { handoff: true, category: 'chấn thương', matched: 'dau goi', reply: 'Dạ câu này em chuyển nhân viên ạ' }, hits: [{ chunk_id: 'c1', document_id: 'd1', title: 'FAQ', kind: 'faq', ordinal: 0, text: 'Mở cửa 5h30', score: 0.42 }], system_prompt: 'PROMPT' })
      return json({ detail: 'unexpected ' + url }, 500)
    })
    render(
      <WorkspaceContext.Provider value={{ current: ws, all: [ws], reload: async () => {}, switchTo: async () => {} }}>
        <AssistantPage />
      </WorkspaceContext.Provider>,
    )
    expect(await screen.findByText(/Chưa có tài liệu/)).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Tiêu đề'), 'FAQ giữ chỗ')
    await userEvent.type(screen.getByLabelText('Nội dung'), 'Giữ chỗ tối đa 15 phút.')
    await userEvent.click(screen.getByRole('button', { name: 'Thêm' }))
    await waitFor(() => expect(within(screen.getByTestId('kb-docs')).getByText('FAQ giữ chỗ')).toBeInTheDocument())

    await userEvent.click(screen.getByRole('button', { name: 'Điều cấm' }))
    const area = screen.getByLabelText('Điều cấm')
    expect(area).toHaveValue('Không giảm giá')
    await userEvent.type(area, '\nKhông hứa giữ chỗ')
    await userEvent.click(screen.getByRole('button', { name: 'Lưu điều cấm' }))
    await waitFor(() => expect(puts).toContainEqual({ forbidden: ['Không giảm giá', 'Không hứa giữ chỗ'] }))

    await userEvent.click(screen.getByRole('button', { name: 'Giọng điệu' }))
    await userEvent.selectOptions(screen.getByLabelText('Mức thân thiện'), '3')
    await waitFor(() => expect(puts).toContainEqual({ friendliness: 3 }))

    await userEvent.click(screen.getByRole('button', { name: 'Thử hỏi' }))
    await userEvent.type(screen.getByLabelText('Câu hỏi thử'), 'Em bị đau gối')
    await userEvent.click(screen.getByRole('button', { name: 'Xem AI sẽ thấy gì' }))
    const prev = await screen.findByTestId('kb-preview')
    expect(prev).toHaveTextContent('Chuyển nhân viên')
    expect(prev).toHaveTextContent('chấn thương')
    expect(prev).toHaveTextContent('Mở cửa 5h30')
  })
})
