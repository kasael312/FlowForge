import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Outlet, RouterProvider, createMemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { AuthProvider, useAuth } from '../auth/AuthContext'
import { RequireAuth } from '../auth/RequireAuth'
import { LoginPage } from '../pages/auth/LoginPage'
import { RegisterPage } from '../pages/auth/RegisterPage'

const user = { id: 'u1', email: 'a@example.com', created_at: '2026-11-09T00:00:00Z' }
const json = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }))

function Home() {
  const { user: u } = useAuth()
  return <p>home for {u?.email}</p>
}

function renderApp(initial: string) {
  const router = createMemoryRouter(
    [
      { path: '/login', element: <LoginPage /> },
      { path: '/register', element: <RegisterPage /> },
      {
        element: <RequireAuth />,
        children: [{ path: '/', element: <Outlet />, children: [{ index: true, element: <Home /> }, { path: 'flows/:id', element: <Home /> }] }],
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

describe('auth', () => {
  const fetchMock = vi.fn<typeof fetch>()
  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })

  it('redirects anonymous users to /login, then back after signing in', async () => {
    fetchMock.mockImplementation((input, init) => {
      const url = String(input)
      if (url.endsWith('/auth/me')) return json({ detail: 'not authenticated' }, 401)
      if (url.endsWith('/auth/login')) {
        const body = JSON.parse(String(init?.body)) as { password: string }
        return body.password === 'right-pass' ? json(user) : json({ detail: 'invalid email or password' }, 401)
      }
      return json({ detail: 'nope' }, 404)
    })
    const router = renderApp('/flows/abc')
    await waitFor(() => expect(router.state.location.pathname).toBe('/login'))

    await userEvent.type(await screen.findByLabelText('Email'), 'a@example.com')
    await userEvent.type(screen.getByLabelText('Mật khẩu'), 'wrong-pass')
    await userEvent.click(screen.getByRole('button', { name: 'Đăng nhập' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Email hoặc mật khẩu không đúng.')

    await userEvent.clear(screen.getByLabelText('Mật khẩu'))
    await userEvent.type(screen.getByLabelText('Mật khẩu'), 'right-pass')
    await userEvent.click(screen.getByRole('button', { name: 'Đăng nhập' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/flows/abc'))
    expect(screen.getByText('home for a@example.com')).toBeInTheDocument()
    const loginInit = fetchMock.mock.calls.find((c) => String(c[0]).endsWith('/auth/login'))?.[1] as RequestInit
    expect(loginInit.credentials).toBe('include')
  })

  it('register shows a clear error for duplicate email and client-side password length', async () => {
    fetchMock.mockImplementation((input) => {
      const url = String(input)
      if (url.endsWith('/auth/me')) return json(null, 401)
      if (url.endsWith('/auth/register')) return json({ detail: 'email already registered' }, 409)
      return json({}, 404)
    })
    renderApp('/register')
    await userEvent.type(await screen.findByLabelText('Email'), 'a@example.com')
    await userEvent.type(screen.getByLabelText('Mật khẩu'), 'short')
    await userEvent.click(screen.getByRole('button', { name: 'Tạo tài khoản' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('ít nhất 8 ký tự')
    expect(fetchMock.mock.calls.some((c) => String(c[0]).endsWith('/auth/register'))).toBe(false)

    await userEvent.type(screen.getByLabelText('Mật khẩu'), '-but-long-enough')
    await userEvent.click(screen.getByRole('button', { name: 'Tạo tài khoản' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('đã được đăng ký')
  })

  it('api client refreshes once on 401 and retries the request', async () => {
    let flowsCalls = 0
    fetchMock.mockImplementation((input) => {
      const url = String(input)
      if (url.endsWith('/flows')) {
        flowsCalls += 1
        return flowsCalls === 1 ? json({ detail: 'token expired' }, 401) : json([])
      }
      if (url.endsWith('/auth/refresh')) return json(user)
      return json({}, 404)
    })
    await expect(api.listFlows()).resolves.toEqual([])
    const urls = fetchMock.mock.calls.map((c) => String(c[0]))
    expect(urls.filter((u) => u.endsWith('/flows')).length).toBe(2)
    expect(urls.filter((u) => u.endsWith('/auth/refresh')).length).toBe(1)
  })
})
