import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { AuthProvider } from '../auth/AuthContext'
import { WorkspaceContext } from '../lib/WorkspaceContext'

// A system admin without a workspace sees the technical navigation.
const user = { id: 'u1', email: 'admin@flowforge.dev', created_at: '2026-11-09T00:00:00Z', is_admin: true }
const json = (body: unknown, status = 200) =>
  Promise.resolve(new Response(body === null ? null : JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }))

function renderApp(initial: string) {
  const router = createMemoryRouter(
    [
      { path: '/login', element: <p>login page</p> },
      {
        path: '/',
        element: <App />,
        children: [
          { index: true, element: <p>dashboard page</p> },
          { path: 'flows', element: <p>flows page</p> },
          { path: 'runs', element: <p>runs page</p> },
          { path: 'settings/keys', element: <p>keys page</p> },
        ],
      },
    ],
    { initialEntries: [initial] },
  )
  render(
    <AuthProvider>
      <RouterProvider router={router} />
    </AuthProvider>,
  )
  return router
}

describe('Sidebar layout', () => {
  const fetchMock = vi.fn<typeof fetch>()
  beforeEach(() => {
    localStorage.clear()
    document.documentElement.dataset.theme = ''
    fetchMock.mockReset()
    fetchMock.mockImplementation((input, init) => {
      const url = String(input)
      if (url.endsWith('/auth/me')) return json(user)
      if (url.endsWith('/auth/logout') && init?.method === 'POST') return json(null, 204)
      return json({}, 404)
    })
    vi.stubGlobal('fetch', fetchMock)
  })

  it('highlights the active item and navigates', async () => {
    const router = renderApp('/runs')
    expect(await screen.findByTestId('current-user')).toHaveTextContent('admin@flowforge.dev')
    const runs = screen.getByRole('link', { name: 'Runs' })
    expect(runs).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('link', { name: 'Flows' })).not.toHaveAttribute('aria-current')

    await userEvent.click(screen.getByRole('link', { name: 'API Keys' }))
    expect(router.state.location.pathname).toBe('/settings/keys')
    expect(screen.getByText('keys page')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'API Keys' })).toHaveAttribute('aria-current', 'page')
  })

  it('collapses, persists the state and keeps icons with titles', async () => {
    renderApp('/')
    await screen.findByTestId('current-user')
    const sidebar = screen.getByTestId('sidebar')
    expect(sidebar).toHaveAttribute('data-collapsed', 'false')
    await userEvent.click(screen.getByRole('button', { name: 'Collapse sidebar' }))
    expect(sidebar).toHaveAttribute('data-collapsed', 'true')
    expect(localStorage.getItem('flowforge.sidebar.collapsed')).toBe('1')
    expect(screen.queryByTestId('current-user')).toBeNull()
    expect(screen.getByRole('link', { name: 'Flows' })).toHaveAttribute('title', 'Flows')
    await userEvent.click(screen.getByRole('button', { name: 'Expand sidebar' }))
    expect(sidebar).toHaveAttribute('data-collapsed', 'false')
  })

  it('cycles the theme and applies it to <html>', async () => {
    renderApp('/')
    await screen.findByTestId('current-user')
    await userEvent.click(screen.getByRole('button', { name: /Theme: system/ }))
    expect(screen.getByRole('button', { name: 'Theme: light' })).toBeInTheDocument()
    expect(document.documentElement.dataset.theme).toBe('light')
    await userEvent.click(screen.getByRole('button', { name: 'Theme: light' }))
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(localStorage.getItem('flowforge.theme')).toBe('dark')
  })

  it('opens the mobile drawer and logs out', async () => {
    const router = renderApp('/')
    await screen.findByTestId('current-user')
    await userEvent.click(screen.getByRole('button', { name: 'Open navigation' }))
    expect(screen.getByTestId('sidebar-drawer')).toBeInTheDocument()
    await userEvent.click(screen.getAllByRole('button', { name: 'Logout' })[0]!)
    await waitFor(() => expect(router.state.location.pathname).toBe('/login'))
    expect(fetchMock.mock.calls.some((c) => String(c[0]).endsWith('/auth/logout'))).toBe(true)
  })

  it('staff see only the day-to-day pages: no automations, connections or settings', async () => {
    fetchMock.mockImplementation((input) => (String(input).endsWith('/auth/me') ? json({ ...user, is_admin: false }) : json({}, 404)))
    const ws = { id: 'w1', name: 'Gym A', industry: 'gym', industry_label: 'Gym', timezone: 'Asia/Ho_Chi_Minh', branches: [], onboarding: {}, role: 'staff', created_at: '' }
    const router = createMemoryRouter([{ path: '/', element: <App />, children: [{ index: true, element: <p>home</p> }] }], { initialEntries: ['/'] })
    render(
      <AuthProvider>
        <WorkspaceContext.Provider value={{ current: ws, all: [ws], reload: async () => {}, switchTo: async () => {} }}>
          <RouterProvider router={router} />
        </WorkspaceContext.Provider>
      </AuthProvider>,
    )
    await screen.findByTestId('current-user')
    const names = screen.getAllByRole('link').map((l) => l.textContent?.trim())
    expect(names).toEqual(expect.arrayContaining(['Tổng quan', 'Hộp thư', 'Lịch', 'Khách hàng', 'Gói & dịch vụ']))
    for (const hidden of ['Việc tự động', 'Kết nối', 'Trợ lý AI', 'Cơ sở', 'API Keys', 'Flows']) expect(screen.queryByRole('link', { name: hidden })).toBeNull()
    expect(screen.queryByText('Cài đặt')).toBeNull()
  })
})
