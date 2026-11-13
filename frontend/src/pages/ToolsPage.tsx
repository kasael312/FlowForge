import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { TOOL_CATEGORIES, type KeyTestResult, type McpServer, type ToolCategory, type ToolInfo } from '../api/types'
import { Button, ErrorBanner, Modal } from '../components/ui'

export const CATEGORY_LABEL: Record<ToolCategory, string> = {
  code: 'Code',
  web: 'Web',
  files: 'Files',
  data: 'Data',
  communication: 'Communication',
  integrations: 'Integrations',
  media: 'Media',
  industry: 'Industry (gym & spa)',
  mcp: 'MCP servers',
}

const field = 'mt-1 w-full rounded border border-zinc-300 bg-white px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-900'
const label = 'block text-xs font-medium text-zinc-600 dark:text-zinc-400'

function StatusPill({ tool }: { tool: ToolInfo }) {
  if (tool.status === 'ready') return <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-800 dark:bg-emerald-900 dark:text-emerald-200">ready</span>
  if (tool.status === 'offline') return <span className="rounded bg-zinc-200 px-1.5 py-0.5 text-[10px] font-semibold text-zinc-700 dark:bg-zinc-700 dark:text-zinc-200">offline</span>
  return (
    <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold text-amber-800 dark:bg-amber-900 dark:text-amber-200" title={`missing: ${tool.missing_credentials.join(', ')}`}>
      needs {tool.missing_credentials.join(', ')}
    </span>
  )
}

/** Tools page: every registry tool by category with readiness, tests and MCP server management. */
export function ToolsPage() {
  const [tools, setTools] = useState<ToolInfo[] | null>(null)
  const [servers, setServers] = useState<McpServer[]>([])
  const [error, setError] = useState<string | null>(null)
  const [tests, setTests] = useState<Record<string, KeyTestResult | 'running'>>({})
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState({ name: '', transport: 'url' as 'url' | 'command', url: '', command: '', args: '', secrets: '' })
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const [t, s] = await Promise.all([api.listTools(), api.listMcpServers()])
      setTools(t)
      setServers(s)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])
  useEffect(() => {
    void load()
  }, [load])

  const test = async (name: string) => {
    setTests((m) => ({ ...m, [name]: 'running' }))
    try {
      const result = await api.testTool(name)
      setTests((m) => ({ ...m, [name]: result }))
    } catch (e) {
      setTests((m) => ({ ...m, [name]: { ok: false, message: (e as Error).message } }))
    }
  }

  const addServer = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const secrets: Record<string, string> = {}
      for (const line of draft.secrets.split('\n')) {
        const [k, ...rest] = line.split('=')
        if (k?.trim() && rest.length) secrets[k.trim()] = rest.join('=').trim()
      }
      await api.createMcpServer({
        name: draft.name.trim(),
        url: draft.transport === 'url' ? draft.url.trim() : null,
        command: draft.transport === 'command' ? draft.command.trim() : null,
        args: draft.args.trim() ? draft.args.trim().split(/\s+/) : [],
        env: draft.transport === 'command' ? secrets : {},
        headers: draft.transport === 'url' ? secrets : {},
      })
      setAdding(false)
      setDraft({ name: '', transport: 'url', url: '', command: '', args: '', secrets: '' })
      await load()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const serverAction = async (fn: () => Promise<unknown>) => {
    try {
      await fn()
      await load()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  const byCat = (cat: ToolCategory) => (tools ?? []).filter((t) => t.category === cat)

  return (
    <div className="mx-auto max-w-6xl p-6">
      <div className="mb-1 flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Tools</h1>
        <Button variant="primary" onClick={() => setAdding(true)}>
          Add MCP server
        </Button>
      </div>
      <p className="mb-4 text-sm text-zinc-500">
        Tools a step may call. Missing credentials are added under <Link to="/settings/keys" className="underline">API keys</Link>; MCP servers contribute their own tools.
      </p>
      <ErrorBanner message={error} onClose={() => setError(null)} />
      {tools === null ? (
        <p className="text-sm text-zinc-500">Loading…</p>
      ) : (
        TOOL_CATEGORIES.map((cat) => {
          const items = byCat(cat)
          if (cat !== 'mcp' && items.length === 0) return null
          return (
            <section key={cat} className="mb-6" data-testid={`cat-${cat}`}>
              <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-zinc-500">
                {CATEGORY_LABEL[cat]} <span className="font-normal normal-case">({items.length})</span>
              </h2>
              {cat === 'mcp' && (
                <ul className="mb-2 space-y-1">
                  {servers.length === 0 && <li className="text-sm text-zinc-500">No MCP servers yet.</li>}
                  {servers.map((s) => (
                    <li key={s.id} className="flex flex-wrap items-center gap-2 rounded-md border border-zinc-200 bg-white px-3 py-2 text-sm dark:border-zinc-800 dark:bg-zinc-900">
                      <span className="font-medium">{s.name}</span>
                      <span className="font-mono text-xs text-zinc-500">{s.url ?? `${s.command} ${s.args.join(' ')}`}</span>
                      <span className="text-xs text-zinc-500">{s.tool_count} tools</span>
                      {!s.enabled && <span className="text-xs text-amber-600">disabled</span>}
                      {s.last_error && <span className="text-xs text-rose-600" title={s.last_error}>error: {s.last_error.slice(0, 60)}</span>}
                      <span className="ml-auto flex gap-1">
                        <Button variant="ghost" onClick={() => void serverAction(() => api.refreshMcpServer(s.id))}>Refresh</Button>
                        <Button variant="ghost" onClick={() => void serverAction(() => api.updateMcpServer(s.id, { enabled: !s.enabled }))}>{s.enabled ? 'Disable' : 'Enable'}</Button>
                        <Button variant="ghost" className="text-rose-600" onClick={() => window.confirm(`Remove MCP server "${s.name}"?`) && void serverAction(() => api.deleteMcpServer(s.id))}>Delete</Button>
                      </span>
                    </li>
                  ))}
                </ul>
              )}
              <ul className="grid gap-2 md:grid-cols-2">
                {items.map((t) => {
                  const r = tests[t.name]
                  return (
                    <li key={t.name} className="rounded-lg border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900" data-testid={`tool-${t.name}`}>
                      <div className="flex items-center gap-2">
                        <code className="text-sm font-semibold">{t.name}</code>
                        <StatusPill tool={t} />
                        {t.needs_sandbox && <span className="text-[10px] text-zinc-500">sandbox</span>}
                        <Button variant="ghost" className="ml-auto px-2 py-0.5 text-xs" disabled={r === 'running'} onClick={() => void test(t.name)}>
                          {r === 'running' ? 'Testing…' : 'Test'}
                        </Button>
                      </div>
                      <p className="mt-1 text-xs text-zinc-600 dark:text-zinc-400">{t.description}</p>
                      {r && r !== 'running' && <p className={`mt-1 text-xs ${r.ok ? 'text-emerald-600' : 'text-rose-600'}`}>{r.ok ? '✔' : '✖'} {r.message}</p>}
                    </li>
                  )
                })}
              </ul>
            </section>
          )
        })
      )}

      {adding && (
        <Modal title="Add MCP server" onClose={() => setAdding(false)}>
          <form onSubmit={(e) => void addServer(e)} className="space-y-3">
            <div>
              <label htmlFor="mcp-name" className={label}>Name</label>
              <input id="mcp-name" required className={field} value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
            </div>
            <div>
              <label htmlFor="mcp-transport" className={label}>Transport</label>
              <select id="mcp-transport" className={field} value={draft.transport} onChange={(e) => setDraft({ ...draft, transport: e.target.value as 'url' | 'command' })}>
                <option value="url">HTTP (Streamable HTTP URL)</option>
                <option value="command">Command (stdio)</option>
              </select>
            </div>
            {draft.transport === 'url' ? (
              <div>
                <label htmlFor="mcp-url" className={label}>URL</label>
                <input id="mcp-url" required className={`${field} font-mono`} placeholder="https://host/mcp" value={draft.url} onChange={(e) => setDraft({ ...draft, url: e.target.value })} />
              </div>
            ) : (
              <>
                <div>
                  <label htmlFor="mcp-command" className={label}>Command</label>
                  <input id="mcp-command" required className={`${field} font-mono`} placeholder="npx" value={draft.command} onChange={(e) => setDraft({ ...draft, command: e.target.value })} />
                </div>
                <div>
                  <label htmlFor="mcp-args" className={label}>Arguments (space separated)</label>
                  <input id="mcp-args" className={`${field} font-mono`} placeholder="-y @modelcontextprotocol/server-filesystem /data" value={draft.args} onChange={(e) => setDraft({ ...draft, args: e.target.value })} />
                </div>
              </>
            )}
            <div>
              <label htmlFor="mcp-secrets" className={label}>{draft.transport === 'url' ? 'Headers' : 'Environment variables'} (KEY=value per line, stored encrypted)</label>
              <textarea id="mcp-secrets" className={`${field} h-20 font-mono text-xs`} placeholder={draft.transport === 'url' ? 'Authorization=Bearer …' : 'API_TOKEN=…'} value={draft.secrets} onChange={(e) => setDraft({ ...draft, secrets: e.target.value })} />
            </div>
            <div className="flex justify-end gap-2">
              <Button onClick={() => setAdding(false)}>Cancel</Button>
              <Button type="submit" variant="primary" disabled={busy || !draft.name.trim()}>
                {busy ? 'Connecting…' : 'Add and fetch tools'}
              </Button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  )
}
