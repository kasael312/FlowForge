import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from '../auth/AuthContext'
import { SharedFlowPage } from '../pages/SharedFlowPage'
import { defaultStepData } from '../lib/steps'

const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }))
const shared = {
  name: 'Fix issue',
  description: 'shared desc',
  nodes: [{ id: 'plan', type: 'step', position: { x: 0, y: 0 }, data: { ...defaultStepData('plan'), label: 'Plan fix', system_prompt: 'Diagnose' } }],
  edges: [],
  updated_at: '2026-11-09T00:00:00Z',
}

function renderPage(loggedIn: boolean) {
  const router = createMemoryRouter(
    [
      { path: '/shared/:token', element: <SharedFlowPage /> },
      { path: '/login', element: <p>login page</p> },
      { path: '/flows/:id', element: <p>editor</p> },
    ],
    { initialEntries: ['/shared/tok123'] },
  )
  const fetchMock = vi.fn<typeof fetch>((input, init) => {
    const url = String(input)
    if (url.endsWith('/auth/me')) return loggedIn ? json({ id: 'u', email: 'a@b.io', created_at: '' }) : json({ detail: 'no' }, 401)
    if (url.endsWith('/shared/tok123') && !init?.method) return json(shared)
    if (url.endsWith('/shared/tok123/clone')) return json({ id: 'cl1', name: 'Fix issue (clone)' }, 201)
    return json({ detail: 'not found' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  render(
    <AuthProvider>
      <RouterProvider router={router} />
    </AuthProvider>,
  )
  return { router, fetchMock }
}

describe('SharedFlowPage', () => {
  beforeEach(() => vi.unstubAllGlobals())

  it('renders read-only without login and sends visitors to sign in', async () => {
    const { router } = renderPage(false)
    expect(await screen.findByText('Fix issue')).toBeInTheDocument()
    expect(screen.getByText('Plan fix')).toBeInTheDocument()
    expect(screen.getByText('Diagnose')).toBeInTheDocument()
    await userEvent.click(await screen.findByRole('button', { name: 'Sign in to clone' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/login'))
    expect((router.state.location.state as { from: string }).from).toBe('/shared/tok123')
  })

  it('clones for a logged-in user', async () => {
    const { router, fetchMock } = renderPage(true)
    await userEvent.click(await screen.findByRole('button', { name: 'Clone to my flows' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/flows/cl1'))
    expect(fetchMock.mock.calls.some((c) => String(c[0]).endsWith('/shared/tok123/clone'))).toBe(true)
  })
})
