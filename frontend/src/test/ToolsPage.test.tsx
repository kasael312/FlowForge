import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { McpServer, ToolInfo } from '../api/types'
import { ToolsPage } from '../pages/ToolsPage'

const json = (body: unknown, status = 200) => Promise.resolve(new Response(body === null ? null : JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }))

const tool = (name: string, category: ToolInfo['category'], extra: Partial<ToolInfo> = {}): ToolInfo => ({
  name,
  description: `${name} does things`,
  category,
  input_schema: {},
  required_credentials: [],
  missing_credentials: [],
  needs_sandbox: false,
  status: 'ready',
  server_id: null,
  ...extra,
})

function renderPage() {
  const router = createMemoryRouter([{ path: '/tools', element: <ToolsPage /> }, { path: '/settings/keys', element: <p>keys</p> }], { initialEntries: ['/tools'] })
  render(<RouterProvider router={router} />)
}

describe('ToolsPage', () => {
  const fetchMock = vi.fn<typeof fetch>()
  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('confirm', vi.fn(() => true))
  })

  it('groups tools by category with status, tests a tool and adds an MCP server', async () => {
    let servers: McpServer[] = []
    let tools = [tool('read_file', 'code', { needs_sandbox: true }), tool('fetch_url', 'web'), tool('telegram_send', 'communication', { status: 'missing_credentials', required_credentials: ['telegram'], missing_credentials: ['telegram'] })]
    fetchMock.mockImplementation((input, init) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.endsWith('/tools')) return json(tools)
      if (url.endsWith('/mcp-servers') && method === 'GET') return json(servers)
      if (url.endsWith('/tools/fetch_url/test')) return json({ ok: true, message: 'fetched example.com (1256 bytes)' })
      if (url.endsWith('/mcp-servers') && method === 'POST') {
        const body = JSON.parse(String(init?.body)) as Record<string, unknown>
        servers = [{ id: 's1', name: String(body.name), url: String(body.url), command: null, args: [], env_keys: [], header_keys: Object.keys(body.headers as object), enabled: true, last_error: null, tool_count: 2, created_at: '' }]
        tools = [...tools, tool('mcp:s1:search', 'mcp', { server_id: 's1', description: '[Docs] search' })]
        return json(servers[0], 201)
      }
      return json({ detail: 'unexpected ' + url }, 500)
    })
    renderPage()
    expect(await screen.findByTestId('cat-code')).toHaveTextContent('read_file')
    expect(screen.getByTestId('tool-telegram_send')).toHaveTextContent('needs telegram')
    expect(screen.getByTestId('tool-read_file')).toHaveTextContent('sandbox')
    expect(screen.getByTestId('cat-mcp')).toHaveTextContent('No MCP servers yet.')

    await userEvent.click(within(screen.getByTestId('tool-fetch_url')).getByRole('button', { name: 'Test' }))
    expect(await screen.findByText(/fetched example.com/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Add MCP server' }))
    await userEvent.type(screen.getByLabelText('Name'), 'Docs')
    await userEvent.type(screen.getByLabelText('URL'), 'https://docs.example/mcp')
    await userEvent.type(screen.getByLabelText(/Headers/), 'Authorization=Bearer abc')
    await userEvent.click(screen.getByRole('button', { name: 'Add and fetch tools' }))
    await waitFor(() => expect(screen.getByTestId('cat-mcp')).toHaveTextContent('Docs'))
    expect(screen.getByTestId('cat-mcp')).toHaveTextContent('2 tools')
    expect(screen.getByTestId('tool-mcp:s1:search')).toBeInTheDocument()
    const post = fetchMock.mock.calls.find((c) => String(c[0]).endsWith('/mcp-servers') && (c[1] as RequestInit).method === 'POST')!
    expect(JSON.parse(String((post[1] as RequestInit).body))).toMatchObject({ name: 'Docs', url: 'https://docs.example/mcp', headers: { Authorization: 'Bearer abc' }, command: null })
  })
})
