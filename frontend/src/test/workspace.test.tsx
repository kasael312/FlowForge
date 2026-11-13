import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Workspace, WorkspaceMember } from '../api/types'
import { AuthProvider } from '../auth/AuthContext'
import { WorkspaceContext, WorkspaceProvider } from '../lib/WorkspaceContext'
import { OnboardingPage } from '../pages/OnboardingPage'
import { WorkspacePage } from '../pages/settings/WorkspacePage'

const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }))
const user = { id: 'u1', email: 'owner@example.com', is_admin: false, current_workspace_id: null, created_at: '2026-11-10T00:00:00Z' }
const questions = {
  questions: [
    { key: 'industry', label: 'Bạn kinh doanh loại hình nào?', type: 'select', options: [{ value: 'gym', label: 'Phòng gym / fitness' }, { value: 'spa', label: 'Spa / thẩm mỹ' }], default: 'gym', required: true, placeholder: '' },
    { key: 'branch_count', label: 'Bạn có bao nhiêu chi nhánh?', type: 'number', options: [], default: 1, required: true, placeholder: '' },
    { key: 'main_channel', label: 'Khách hàng nhắn tin cho bạn chủ yếu qua kênh nào?', type: 'select', options: [{ value: 'zalo', label: 'Zalo OA' }, { value: 'facebook', label: 'Facebook Messenger' }, { value: 'both', label: 'Cả hai' }], default: 'zalo', required: true, placeholder: '' },
    { key: 'current_software', label: 'Phần mềm?', type: 'text', options: [], default: null, required: false, placeholder: '' },
  ],
  industries: [
    { key: 'gym', label: 'Phòng gym / fitness', description: 'Gói tập, PT' },
    { key: 'spa', label: 'Spa / thẩm mỹ', description: 'Liệu trình' },
  ],
}
const ws = (over: Partial<Workspace> = {}): Workspace => ({
  id: 'w1',
  name: 'Gym Sức Sống',
  industry: 'gym',
  industry_label: 'Phòng gym / fitness',
  timezone: 'Asia/Ho_Chi_Minh',
  branches: [{ id: 'b1', name: 'Chi nhánh chính', address: '', phone: '' }],
  onboarding: {},
  role: 'owner',
  created_at: '2026-11-10T00:00:00Z',
  ...over,
})

describe('OnboardingPage', () => {
  const fetchMock = vi.fn<typeof fetch>()
  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('asks the four questions and posts the answers', async () => {
    const posts: unknown[] = []
    fetchMock.mockImplementation((input, init) => {
      const url = String(input)
      if (url.endsWith('/auth/me')) return json(user)
      if (url.endsWith('/onboarding/questions')) return json(questions)
      if (url.endsWith('/workspaces')) return json(posts.length ? [ws()] : [])
      if (url.endsWith('/onboarding') && init?.method === 'POST') {
        posts.push(JSON.parse(String(init.body)))
        return json(ws(), 201)
      }
      return json({ detail: 'unexpected ' + url }, 500)
    })
    const router = createMemoryRouter([{ path: '/onboarding', element: <OnboardingPage /> }, { path: '/', element: <p>home</p> }], { initialEntries: ['/onboarding'] })
    render(
      <AuthProvider>
        <WorkspaceProvider>
          <RouterProvider router={router} />
        </WorkspaceProvider>
      </AuthProvider>,
    )
    await userEvent.type(await screen.findByLabelText('Tên cơ sở'), 'Spa Hoa Sen')
    await userEvent.click(await within(screen.getByTestId('industry-options')).findByLabelText(/Spa \/ thẩm mỹ/))
    await userEvent.clear(screen.getByLabelText(/chi nhánh/))
    await userEvent.type(screen.getByLabelText(/chi nhánh/), '3')
    await userEvent.selectOptions(screen.getByLabelText(/kênh nào/), 'both')
    await userEvent.click(screen.getByRole('button', { name: 'Bắt đầu' }))
    await waitFor(() => expect(posts).toHaveLength(1))
    expect(posts[0]).toEqual({ name: 'Spa Hoa Sen', industry: 'spa', branch_count: 3, main_channel: 'both', current_software: '' })
    expect(await screen.findByText('home')).toBeInTheDocument()
  })
})

describe('WorkspacePage', () => {
  const fetchMock = vi.fn<typeof fetch>()
  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('owner edits branches and manages members', async () => {
    const members: WorkspaceMember[] = [{ id: 'm1', user_id: 'u1', email: 'owner@example.com', role: 'owner', created_at: '' }]
    const puts: unknown[] = []
    fetchMock.mockImplementation((input, init) => {
      const url = String(input)
      if (url.endsWith('/onboarding/questions')) return json(questions)
      if (url.endsWith('/workspaces/current/members') && init?.method === 'POST') {
        const body = JSON.parse(String(init.body)) as { email: string; role: string }
        members.push({ id: 'm2', user_id: 'u2', email: body.email, role: body.role, created_at: '' })
        return json(members.at(-1), 201)
      }
      if (url.endsWith('/workspaces/current/members')) return json(members)
      if (url.endsWith('/workspaces/current') && init?.method === 'PUT') {
        puts.push(JSON.parse(String(init.body)))
        return json(ws({ name: 'Gym Mới' }))
      }
      return json({ detail: 'unexpected ' + url }, 500)
    })
    render(
      <WorkspaceContext.Provider value={{ current: ws(), all: [ws()], reload: async () => {}, switchTo: async () => {} }}>
        <WorkspacePage />
      </WorkspaceContext.Provider>,
    )
    expect(await screen.findByDisplayValue('Gym Sức Sống')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Thêm chi nhánh' }))
    await userEvent.clear(screen.getByLabelText('Tên chi nhánh 2'))
    await userEvent.type(screen.getByLabelText('Tên chi nhánh 2'), 'Quận 7')
    await userEvent.click(screen.getByRole('button', { name: 'Lưu' }))
    await waitFor(() => expect(puts).toHaveLength(1))
    expect(puts[0]).toMatchObject({ name: 'Gym Sức Sống', branches: [{ id: 'b1', name: 'Chi nhánh chính' }, { id: '', name: 'Quận 7' }] })

    await userEvent.type(screen.getByLabelText(/Email tài khoản/), 'staff@example.com')
    await userEvent.click(screen.getByRole('button', { name: 'Thêm' }))
    await waitFor(() => expect(within(screen.getByTestId('members')).getByText('staff@example.com')).toBeInTheDocument())
  })

  it('staff sees read-only fields', async () => {
    fetchMock.mockImplementation((input) => {
      const url = String(input)
      if (url.endsWith('/onboarding/questions')) return json(questions)
      if (url.endsWith('/workspaces/current/members')) return json([])
      return json({ detail: 'unexpected' }, 500)
    })
    render(
      <WorkspaceContext.Provider value={{ current: ws({ role: 'staff' }), all: [ws({ role: 'staff' })], reload: async () => {}, switchTo: async () => {} }}>
        <WorkspacePage />
      </WorkspaceContext.Provider>,
    )
    expect(await screen.findByDisplayValue('Gym Sức Sống')).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Lưu' })).toBeNull()
    expect(screen.queryByLabelText(/Email tài khoản/)).toBeNull()
  })
})
