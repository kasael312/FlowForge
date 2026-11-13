import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ApiKey } from '../api/types'
import { KeysPage } from '../pages/settings/KeysPage'

const key = (id: string, provider: ApiKey['provider'], last4: string): ApiKey => ({ id, provider, label: 'work', last4, created_at: '2026-11-09T00:00:00Z' })
const json = (body: unknown, status = 200) =>
  Promise.resolve(new Response(body === null ? null : JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }))

describe('KeysPage', () => {
  const fetchMock = vi.fn<typeof fetch>()
  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('confirm', vi.fn(() => true))
  })

  it('lists masked keys, tests a value, saves and deletes', async () => {
    let stored: ApiKey[] = [key('k1', 'anthropic', 'abcd')]
    fetchMock.mockImplementation((input, init) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.endsWith('/keys') && method === 'GET') return json(stored)
      if (url.endsWith('/keys/test')) {
        const body = JSON.parse(String(init?.body)) as { value: string; provider: string }
        return json({ ok: body.value.endsWith('ok'), message: body.value.endsWith('ok') ? 'OK, models available' : 'rejected (401)' })
      }
      if (url.endsWith('/keys') && method === 'POST') {
        const body = JSON.parse(String(init?.body)) as { value: string; provider: ApiKey['provider']; label: string }
        stored = [key('k2', body.provider, body.value.slice(-4)), ...stored]
        return json(stored[0], 201)
      }
      if (url.endsWith('/keys/k1') && method === 'DELETE') {
        stored = stored.filter((k) => k.id !== 'k1')
        return json(null, 204)
      }
      return json({ detail: 'unexpected' }, 500)
    })
    render(<KeysPage />)
    expect(await screen.findByText('••••••••abcd')).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'Anthropic' })).toBeInTheDocument()

    // test a bad value, then a good one
    await userEvent.selectOptions(screen.getByLabelText('Provider'), 'github')
    await userEvent.type(screen.getByLabelText('Key'), 'ghp_this-is-bad')
    await userEvent.click(screen.getByRole('button', { name: 'Test key' }))
    expect(await screen.findByRole('status')).toHaveTextContent('rejected (401)')
    await userEvent.clear(screen.getByLabelText('Key'))
    await userEvent.type(screen.getByLabelText('Key'), 'ghp_this-is-ok')
    await userEvent.click(screen.getByRole('button', { name: 'Test key' }))
    expect(await screen.findByRole('status')).toHaveTextContent('OK, models available')

    await userEvent.click(screen.getByRole('button', { name: 'Save key' }))
    expect(await screen.findByText('••••••••s-ok')).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'GitHub' })).toBeInTheDocument()
    expect(screen.getByLabelText('Key')).toHaveValue('')
    const post = fetchMock.mock.calls.find((c) => String(c[0]).endsWith('/keys') && (c[1] as RequestInit).method === 'POST')!
    expect(JSON.parse(String((post[1] as RequestInit).body))).toEqual({ provider: 'github', label: '', value: 'ghp_this-is-ok' })

    await userEvent.click(screen.getAllByRole('button', { name: 'Delete' })[1]!)
    await waitFor(() => expect(screen.queryByText('••••••••abcd')).toBeNull())
  })

  it('never renders anything but last4 and disables actions for short values', async () => {
    fetchMock.mockImplementation(() => json([key('k1', 'openai', 'wxyz')]))
    const { container } = render(<KeysPage />)
    await screen.findByText('••••••••wxyz')
    expect(container.textContent).not.toMatch(/sk-[A-Za-z0-9_-]{6,}/)
    expect(container.textContent).not.toMatch(/encrypted_value|password_hash/)
    expect(screen.getByRole('button', { name: 'Test key' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Save key' })).toBeDisabled()
  })
})
