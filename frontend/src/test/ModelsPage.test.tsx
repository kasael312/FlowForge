import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { LlmModel } from '../api/types'
import { ModelsPage } from '../pages/settings/ModelsPage'

const json = (body: unknown, status = 200) => Promise.resolve(new Response(body === null ? null : JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }))

const model = (id: string, label: string, extra: Partial<LlmModel> = {}): LlmModel => ({
  id,
  provider: 'anthropic',
  model_name: 'claude-sonnet-5-5',
  label,
  base_url: null,
  api_key_id: null,
  api_key_last4: null,
  params: {},
  is_default: false,
  created_at: '2026-11-09T00:00:00Z',
  pricing_key: 'anthropic/claude-sonnet-5-5',
  price: { input: 2, output: 10 },
  ...extra,
})

describe('ModelsPage', () => {
  const fetchMock = vi.fn<typeof fetch>()
  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('confirm', vi.fn(() => true))
  })

  it('lists models, adds an OpenAI-compatible one with its key, sets default and tests', async () => {
    let models = [model('m1', 'Sonnet', { is_default: true })]
    fetchMock.mockImplementation((input, init) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.endsWith('/models') && method === 'GET') return json(models)
      if (url.endsWith('/keys')) return json([{ id: 'k1', provider: 'other', label: 'groq', last4: 'q123', created_at: '' }, { id: 'k2', provider: 'anthropic', label: 'a', last4: 'a999', created_at: '' }])
      if (url.endsWith('/models') && method === 'POST') {
        const body = JSON.parse(String(init?.body)) as Record<string, unknown>
        models = [...models, model('m2', String(body.label), { provider: 'openai_compatible', model_name: String(body.model_name), base_url: String(body.base_url), api_key_id: 'k1', api_key_last4: 'q123', params: body.params as LlmModel['params'], price: null })]
        return json(models[1], 201)
      }
      if (url.endsWith('/models/m2/default')) {
        models = models.map((m) => ({ ...m, is_default: m.id === 'm2' }))
        return json(models[1])
      }
      if (url.endsWith('/models/m2/test')) return json({ ok: false, message: 'openai_compatible: authentication failed (401)' })
      return json({ detail: 'unexpected ' + url }, 500)
    })
    render(<ModelsPage />)
    expect(await screen.findByText('Sonnet')).toBeInTheDocument()
    expect(screen.getByText('default')).toBeInTheDocument()
    expect(screen.getByText('$2.00 in · $10.00 out')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Add model' }))
    await userEvent.selectOptions(screen.getByLabelText('Provider'), 'openai_compatible')
    await userEvent.type(screen.getByLabelText('Label'), 'Groq Llama')
    await userEvent.type(screen.getByLabelText('Model name'), 'llama-3.3-70b-versatile')
    await userEvent.type(screen.getByLabelText(/Base URL/), 'https://api.groq.com/openai/v1')
    // only "other" keys are offered for OpenAI-compatible hosts
    const keySelect = screen.getByLabelText('API key')
    expect(keySelect).toHaveTextContent('groq ••••q123')
    expect(keySelect).not.toHaveTextContent('a999')
    await userEvent.selectOptions(keySelect, 'k1')
    await userEvent.type(screen.getByLabelText('Temperature'), '0.3')
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Add model' }))

    expect(await screen.findByText('Groq Llama')).toBeInTheDocument()
    const post = fetchMock.mock.calls.find((c) => String(c[0]).endsWith('/models') && (c[1] as RequestInit).method === 'POST')!
    const body = JSON.parse(String((post[1] as RequestInit).body))
    expect(body).toMatchObject({ provider: 'openai_compatible', label: 'Groq Llama', model_name: 'llama-3.3-70b-versatile', base_url: 'https://api.groq.com/openai/v1', api_key_id: 'k1', params: { temperature: 0.3 } })

    await userEvent.click(screen.getByRole('button', { name: 'Set default' }))
    await waitFor(() => expect(screen.getByText('Groq Llama').closest('td')).toHaveTextContent('default'))

    await userEvent.click(screen.getAllByRole('button', { name: 'Test' })[1]!)
    expect(await screen.findByText(/authentication failed/)).toBeInTheDocument()
  })

  it('shows an empty state', async () => {
    fetchMock.mockImplementation((input) => json(String(input).endsWith('/keys') ? [] : []))
    render(<ModelsPage />)
    expect(await screen.findByText('No models configured yet.')).toBeInTheDocument()
  })
})
