import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import { KEY_PROVIDERS, type ApiKey, type KeyProvider, type KeyTestResult } from '../../api/types'
import { Button, ErrorBanner } from '../../components/ui'
import { formatDate } from '../../lib/format'

const PROVIDER_LABEL: Record<KeyProvider, string> = {
  anthropic: 'Anthropic',
  openai: 'OpenAI',
  github: 'GitHub',
  other: 'Other (OpenAI-compatible)',
  tavily: 'Tavily (web search)',
  smtp: 'SMTP (email)',
  slack: 'Slack',
  telegram: 'Telegram bot',
  notion: 'Notion',
  google: 'Google service account',
  database: 'Database URL',
}
const PROVIDER_HINT: Record<KeyProvider, string> = {
  anthropic: 'Used by the executor to run your flows. sk-ant-…',
  openai: 'Stored for future providers. sk-…',
  github: 'Personal access token with repo scope, used to create pull requests. ghp_…',
  other: 'Key for an OpenAI-compatible host such as Groq, DeepSeek or OpenRouter. Attach it to a model under Settings → Models.',
  tavily: 'Tavily API key for the web_search tool (without it DuckDuckGo HTML is used). tvly-…',
  smtp: 'smtp://user:password@host:587 or smtps://…:465. Gmail: use an app password. Used by send_email.',
  slack: 'Incoming webhook URL (https://hooks.slack.com/…) or bot token xoxb-… for slack_post.',
  telegram: 'Bot token from @BotFather (123456:ABC…) for telegram_send.',
  notion: 'Internal integration token (secret_…) for the notion tool.',
  google: 'Paste the whole service-account JSON; share your sheet with its client_email. Used by google_sheets.',
  database: 'SQLAlchemy URL, e.g. postgresql+psycopg://user:pw@host/db, for read-only query_sql.',
}

const field = 'mt-1 w-full rounded border border-zinc-300 bg-white px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-900'
const label = 'block text-xs font-medium text-zinc-600 dark:text-zinc-400'

/** Settings → API keys: list, add (with live test) and delete provider keys. */
export function KeysPage() {
  const [keys, setKeys] = useState<ApiKey[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [provider, setProvider] = useState<KeyProvider>('anthropic')
  const [keyLabel, setKeyLabel] = useState('')
  const [value, setValue] = useState('')
  const [testResult, setTestResult] = useState<KeyTestResult | null>(null)
  const [busy, setBusy] = useState<'test' | 'save' | null>(null)
  const [storedTest, setStoredTest] = useState<Record<string, KeyTestResult>>({})

  const load = useCallback(async () => {
    try {
      setKeys(await api.listKeys())
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])
  useEffect(() => {
    void load()
  }, [load])

  const test = async () => {
    setBusy('test')
    setTestResult(null)
    try {
      setTestResult(await api.testKeyValue(provider, value.trim()))
    } catch (e) {
      setTestResult({ ok: false, message: (e as Error).message })
    } finally {
      setBusy(null)
    }
  }

  const save = async (e: FormEvent) => {
    e.preventDefault()
    setBusy('save')
    setError(null)
    try {
      await api.createKey({ provider, label: keyLabel.trim(), value: value.trim() })
      setValue('')
      setKeyLabel('')
      setTestResult(null)
      await load()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(null)
    }
  }

  const remove = async (k: ApiKey) => {
    if (!window.confirm(`Delete the ${PROVIDER_LABEL[k.provider]} key ending in ${k.last4}?`)) return
    try {
      await api.deleteKey(k.id)
      await load()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  const testStored = async (k: ApiKey) => {
    try {
      const r = await api.testStoredKey(k.id)
      setStoredTest((m) => ({ ...m, [k.id]: r }))
    } catch (err) {
      setStoredTest((m) => ({ ...m, [k.id]: { ok: false, message: (err as Error).message } }))
    }
  }

  return (
    <div className="mx-auto max-w-4xl p-6">
      <h1 className="mb-1 text-2xl font-semibold">API keys</h1>
      <p className="mb-4 text-sm text-zinc-500">
        Keys are encrypted at rest and never shown again. The newest Anthropic key is used when your flows run.
      </p>
      <ErrorBanner message={error} onClose={() => setError(null)} />

      {keys === null ? (
        <p className="text-sm text-zinc-500">Loading…</p>
      ) : keys.length === 0 ? (
        <p className="rounded-md border border-dashed border-zinc-300 p-6 text-center text-sm text-zinc-500 dark:border-zinc-700">No keys yet.</p>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase text-zinc-500">
            <tr>
              <th className="py-2">Provider</th>
              <th>Label</th>
              <th>Key</th>
              <th>Added</th>
              <th className="text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {keys.map((k) => (
              <tr key={k.id} className="border-t border-zinc-200 dark:border-zinc-800">
                <td className="py-2 font-medium">{PROVIDER_LABEL[k.provider]}</td>
                <td className="text-zinc-600 dark:text-zinc-400">{k.label || '—'}</td>
                <td className="font-mono text-xs">••••••••{k.last4}</td>
                <td className="text-zinc-500">{formatDate(k.created_at)}</td>
                <td className="space-x-1 text-right">
                  {storedTest[k.id] && (
                    <span className={`mr-2 text-xs ${storedTest[k.id]!.ok ? 'text-emerald-600' : 'text-rose-600'}`}>{storedTest[k.id]!.message}</span>
                  )}
                  <Button variant="ghost" onClick={() => void testStored(k)}>
                    Test
                  </Button>
                  <Button variant="ghost" className="text-rose-600" onClick={() => void remove(k)}>
                    Delete
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <form onSubmit={(e) => void save(e)} className="mt-6 space-y-3 rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
        <h2 className="text-sm font-semibold">Add a key</h2>
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label htmlFor="key-provider" className={label}>Provider</label>
            <select id="key-provider" className={field} value={provider} onChange={(e) => { setProvider(e.target.value as KeyProvider); setTestResult(null) }}>
              {KEY_PROVIDERS.map((p) => (
                <option key={p} value={p}>
                  {PROVIDER_LABEL[p]}
                </option>
              ))}
            </select>
            <p className="mt-1 text-[11px] text-zinc-500">{PROVIDER_HINT[provider]}</p>
          </div>
          <div>
            <label htmlFor="key-label" className={label}>Label (optional)</label>
            <input id="key-label" className={field} placeholder="e.g. personal" value={keyLabel} onChange={(e) => setKeyLabel(e.target.value)} />
          </div>
        </div>
        <div>
          <label htmlFor="key-value" className={label}>Key</label>
          <input
            id="key-value"
            type="password"
            autoComplete="off"
            className={`${field} font-mono`}
            value={value}
            onChange={(e) => { setValue(e.target.value); setTestResult(null) }}
          />
        </div>
        {testResult && (
          <p role="status" className={`text-sm ${testResult.ok ? 'text-emerald-600' : 'text-rose-600'}`}>
            {testResult.ok ? '✔' : '✖'} {testResult.message}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button type="button" disabled={busy !== null || value.trim().length < 8} onClick={() => void test()}>
            {busy === 'test' ? 'Testing…' : 'Test key'}
          </Button>
          <Button type="submit" variant="primary" disabled={busy !== null || value.trim().length < 8}>
            {busy === 'save' ? 'Saving…' : 'Save key'}
          </Button>
        </div>
      </form>
    </div>
  )
}
