import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { TRIGGER_TYPES, type Trigger, type TriggerType } from '../api/types'
import { formatDate } from '../lib/format'
import { Button, ErrorBanner } from './ui'

const field = 'mt-1 w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900'
const label = 'block text-[11px] font-medium text-zinc-600 dark:text-zinc-400'

export const TRIGGER_LABEL: Record<TriggerType, string> = {
  manual: 'Manual',
  schedule: 'Schedule (cron)',
  webhook: 'Webhook',
  email: 'Incoming email (IMAP)',
  file_watch: 'Watched folder',
  github: 'GitHub event',
}

const HINT: Record<TriggerType, string> = {
  manual: '',
  schedule: '5-field cron in UTC, e.g. "0 2 * * *" = every day at 02:00.',
  webhook: 'POST JSON to the URL below, signed with HMAC-SHA256 of the raw body using the secret (header X-FlowForge-Signature: sha256=…) or with Authorization: Bearer <secret>. Top-level JSON keys become inputs; a "task" key sets the task.',
  email: 'Polls the mailbox of your "imap" credential (Settings → API keys: imap://user:password@host:993). Each new message starts a run with input.email.{subject,from,body}.',
  file_watch: 'Any file dropped into the folder below starts a run with input.file; processed files are moved to processed/.',
  github: 'Add the URL below as a GitHub webhook (content type application/json) with the secret; chosen events start a run with input.event and the payload.',
}

type Draft = { type: TriggerType; name: string; cron: string; events: string; folder: string; poll: string; pattern: string; task: string; repo_url: string; branch: string }
const EMPTY: Draft = { type: 'schedule', name: '', cron: '0 2 * * *', events: 'push, pull_request, issues', folder: 'INBOX', poll: '60', pattern: '*', task: '', repo_url: '', branch: 'main' }

/** Triggers of one flow: list, add, enable/disable, fire now, delete. */
export function TriggersPanel({ flowId }: { flowId: string }) {
  const navigate = useNavigate()
  const [rows, setRows] = useState<Trigger[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [draft, setDraft] = useState<Draft>(EMPTY)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setRows(await api.listTriggers(flowId))
    } catch (e) {
      setError((e as Error).message)
    }
  }, [flowId])
  useEffect(() => {
    void load()
  }, [load])

  const act = async (fn: () => Promise<unknown>, after?: (r: unknown) => void) => {
    setError(null)
    try {
      const r = await fn()
      after?.(r)
      await load()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const add = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    const config: Record<string, unknown> = { task: draft.task, repo_url: draft.repo_url, branch: draft.branch || 'main' }
    if (draft.type === 'schedule') config.cron = draft.cron
    if (draft.type === 'github') config.events = draft.events.split(',').map((s) => s.trim()).filter(Boolean)
    if (draft.type === 'email') Object.assign(config, { folder: draft.folder, poll_seconds: Number(draft.poll) || 60 })
    if (draft.type === 'file_watch') config.pattern = draft.pattern || '*'
    await act(() => api.createTrigger(flowId, { type: draft.type, name: draft.name, config }), () => setDraft(EMPTY))
    setBusy(false)
  }

  return (
    <div className="space-y-4">
      <ErrorBanner message={error} onClose={() => setError(null)} />
      {notice && <p role="status" className="text-sm text-emerald-600">{notice}</p>}
      {rows === null ? (
        <p className="text-sm text-zinc-500">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-zinc-500">No triggers yet. Runs can always be started manually from the Run page.</p>
      ) : (
        <ul className="space-y-2">
          {rows.map((t) => (
            <li key={t.id} className="rounded-lg border border-zinc-200 p-3 text-sm dark:border-zinc-800" data-testid={`trigger-${t.id}`}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded bg-zinc-200 px-1.5 py-0.5 text-[10px] font-semibold uppercase dark:bg-zinc-700">{TRIGGER_LABEL[t.type]}</span>
                <span className="font-medium">{t.name || '(unnamed)'}</span>
                {!t.enabled && <span className="text-xs text-amber-600">disabled</span>}
                {t.last_error && <span className="text-xs text-rose-600" title={t.last_error}>error: {t.last_error.slice(0, 60)}</span>}
                <span className="ml-auto flex gap-1">
                  <Button variant="ghost" className="px-2 py-0.5 text-xs" onClick={() => void act(() => api.fireTrigger(t.id), (r) => { setNotice('Run started'); navigate(`/runs/${(r as { id: string }).id}`) })}>Fire now</Button>
                  <Button variant="ghost" className="px-2 py-0.5 text-xs" onClick={() => void act(() => api.updateTrigger(t.id, { enabled: !t.enabled }))}>{t.enabled ? 'Disable' : 'Enable'}</Button>
                  <Button variant="ghost" className="px-2 py-0.5 text-xs text-rose-600" onClick={() => window.confirm('Delete this trigger?') && void act(() => api.deleteTrigger(t.id))}>Delete</Button>
                </span>
              </div>
              <div className="mt-1 text-xs text-zinc-500">
                {t.type === 'schedule' && <code>cron: {String(t.config.cron)}</code>}
                {t.type === 'email' && <code>folder {String(t.config.folder)} · every {String(t.config.poll_seconds)}s</code>}
                {t.type === 'file_watch' && <code className="break-all">drop files into {t.watch_folder}</code>}
                {t.type === 'github' && <code>events: {(t.config.events as string[] | undefined)?.join(', ')}</code>}
                {t.last_fired && <span> · last fired {formatDate(t.last_fired)}</span>}
              </div>
              {t.webhook && (
                <div className="mt-2 space-y-1 rounded bg-zinc-50 p-2 font-mono text-[11px] dark:bg-zinc-900">
                  <div className="break-all">URL: {t.webhook.url}</div>
                  <div className="break-all">
                    Secret:{' '}
                    <span data-testid="webhook-secret">{t.webhook.secret ?? 'hidden — shown once when created; rotate to get a new one'}</span>
                  </div>
                  <details>
                    <summary className="cursor-pointer text-zinc-500">curl example</summary>
                    <pre className="mt-1 whitespace-pre-wrap">{t.webhook.example}</pre>
                  </details>
                  <Button variant="ghost" className="px-2 py-0.5 text-[11px]" onClick={() => window.confirm('Rotate the secret? Existing callers must be updated.') && void act(() => api.updateTrigger(t.id, { rotate_secret: true }))}>Rotate secret</Button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      <form onSubmit={(e) => void add(e)} className="space-y-2 rounded-lg border border-zinc-200 p-3 dark:border-zinc-800">
        <h3 className="text-sm font-semibold">Add trigger</h3>
        <div className="grid gap-2 sm:grid-cols-2">
          <div>
            <label htmlFor="tr-type" className={label}>Type</label>
            <select id="tr-type" className={field} value={draft.type} onChange={(e) => setDraft({ ...draft, type: e.target.value as TriggerType })}>
              {TRIGGER_TYPES.map((t) => (
                <option key={t} value={t}>
                  {TRIGGER_LABEL[t]}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="tr-name" className={label}>Name</label>
            <input id="tr-name" className={field} value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
          </div>
        </div>
        <p className="text-[11px] text-zinc-500">{HINT[draft.type]}</p>
        {draft.type === 'schedule' && (
          <div>
            <label htmlFor="tr-cron" className={label}>Cron expression (UTC)</label>
            <input id="tr-cron" className={`${field} font-mono`} value={draft.cron} onChange={(e) => setDraft({ ...draft, cron: e.target.value })} />
          </div>
        )}
        {draft.type === 'github' && (
          <div>
            <label htmlFor="tr-events" className={label}>Events (comma separated)</label>
            <input id="tr-events" className={`${field} font-mono`} value={draft.events} onChange={(e) => setDraft({ ...draft, events: e.target.value })} />
          </div>
        )}
        {draft.type === 'email' && (
          <div className="grid gap-2 sm:grid-cols-2">
            <div>
              <label htmlFor="tr-folder" className={label}>Folder</label>
              <input id="tr-folder" className={field} value={draft.folder} onChange={(e) => setDraft({ ...draft, folder: e.target.value })} />
            </div>
            <div>
              <label htmlFor="tr-poll" className={label}>Poll every (seconds, min 30)</label>
              <input id="tr-poll" type="number" min={30} className={field} value={draft.poll} onChange={(e) => setDraft({ ...draft, poll: e.target.value })} />
            </div>
          </div>
        )}
        {draft.type === 'file_watch' && (
          <div>
            <label htmlFor="tr-pattern" className={label}>File pattern</label>
            <input id="tr-pattern" className={`${field} font-mono`} value={draft.pattern} onChange={(e) => setDraft({ ...draft, pattern: e.target.value })} />
          </div>
        )}
        <div className="grid gap-2 sm:grid-cols-3">
          <div className="sm:col-span-1">
            <label htmlFor="tr-task" className={label}>Default task</label>
            <input id="tr-task" className={field} value={draft.task} onChange={(e) => setDraft({ ...draft, task: e.target.value })} />
          </div>
          <div>
            <label htmlFor="tr-repo" className={label}>Repository URL (optional)</label>
            <input id="tr-repo" className={`${field} font-mono`} value={draft.repo_url} onChange={(e) => setDraft({ ...draft, repo_url: e.target.value })} />
          </div>
          <div>
            <label htmlFor="tr-branch" className={label}>Branch</label>
            <input id="tr-branch" className={`${field} font-mono`} value={draft.branch} onChange={(e) => setDraft({ ...draft, branch: e.target.value })} />
          </div>
        </div>
        <div className="flex justify-end">
          <Button type="submit" variant="primary" disabled={busy}>
            {busy ? 'Adding…' : 'Add trigger'}
          </Button>
        </div>
      </form>
    </div>
  )
}
