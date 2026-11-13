import { useCallback, useEffect, useState } from 'react'
import { biz, type Automation, type ConfigField, type TryResult } from '../../api/biz'
import { Button, ErrorBanner, Modal } from '../../components/ui'
import { formatDateVi } from '../../lib/format'
import { useWorkspace } from '../../lib/WorkspaceContext'

const field = 'mt-1 w-full rounded border border-zinc-300 bg-white px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-900'
const label = 'block text-xs font-medium text-zinc-600 dark:text-zinc-400'

function ConfigInput({ f, value, onChange, disabled }: { f: ConfigField; value: unknown; onChange: (v: unknown) => void; disabled: boolean }) {
  const id = `cfg-${f.key}`
  if (f.type === 'bool') {
    return (
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={Boolean(value)} disabled={disabled} onChange={(e) => onChange(e.target.checked)} /> {f.label}
      </label>
    )
  }
  return (
    <div>
      <label htmlFor={id} className={label}>{f.label}</label>
      {f.type === 'select' ? (
        <select id={id} className={field} value={String(value ?? '')} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
          {f.options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      ) : f.type === 'textarea' ? (
        <textarea id={id} className={`${field} h-24 font-mono text-xs`} value={String(value ?? '')} disabled={disabled} onChange={(e) => onChange(e.target.value)} />
      ) : (
        <input id={id} type={f.type === 'number' ? 'number' : f.type === 'time' ? 'time' : 'text'} className={field} value={value === null || value === undefined ? '' : String(value)} disabled={disabled} onChange={(e) => onChange(f.type === 'number' ? Number(e.target.value) : e.target.value)} />
      )}
      {f.help && <p className="text-[11px] text-zinc-500">{f.help}</p>}
    </div>
  )
}

function Card({ a, isOwner, onChange }: { a: Automation; isOwner: boolean; onChange: () => Promise<void> }) {
  const [open, setOpen] = useState(false)
  const [config, setConfig] = useState<Record<string, unknown>>(a.config)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [trial, setTrial] = useState<TryResult | 'running' | null>(null)
  useEffect(() => setConfig(a.config), [a.config])

  const toggle = async () => {
    setBusy(true)
    setError(null)
    try {
      await biz.updateAutomation(a.key, { enabled: !a.enabled })
      await onChange()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const save = async () => {
    setBusy(true)
    setError(null)
    try {
      await biz.updateAutomation(a.key, { config })
      await onChange()
      setOpen(false)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const tryRun = async () => {
    setTrial('running')
    setError(null)
    try {
      setTrial(await biz.tryAutomation(a.key))
    } catch (e) {
      setTrial(null)
      setError((e as Error).message)
    }
  }

  return (
    <section className={`rounded-lg border bg-white p-4 dark:bg-zinc-900 ${a.enabled ? 'border-emerald-300 dark:border-emerald-800' : 'border-zinc-200 dark:border-zinc-800'}`} data-testid={`automation-${a.key}`}>
      <div className="flex flex-wrap items-start gap-2">
        <div className="min-w-0 flex-1">
          <h2 className="text-base font-semibold">{a.name}</h2>
          <p className="text-xs text-zinc-500">{a.trigger_label}</p>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <span className={a.enabled ? 'text-emerald-700 dark:text-emerald-300' : 'text-zinc-500'}>{a.enabled ? 'Đang bật' : 'Đang tắt'}</span>
          <input type="checkbox" role="switch" aria-label={`Bật ${a.name}`} className="h-5 w-9 cursor-pointer appearance-none rounded-full bg-zinc-300 transition checked:bg-emerald-500 dark:bg-zinc-700" checked={a.enabled} disabled={!isOwner || busy} onChange={() => void toggle()} />
        </label>
      </div>
      <p className="mt-2 text-sm text-zinc-700 dark:text-zinc-300">{a.description}</p>
      <p className="mt-2 text-xs text-zinc-500" data-testid={`stats-${a.key}`}>
        7 ngày qua: {a.runs_7d} lần chạy{a.failed_7d ? `, ${a.failed_7d} lỗi` : ''}
        {a.last_run_at ? ` · gần nhất ${formatDateVi(a.last_run_at)}` : ''}
        {a.last_error && <span className="text-rose-600"> · {a.last_error}</span>}
        {a.needs_model && <span className="block">Cần một model AI ở Settings → Models (hoặc key Anthropic của server).</span>}
      </p>
      <ErrorBanner message={error} onClose={() => setError(null)} />
      <div className="mt-3 flex flex-wrap gap-2">
        <Button className="px-2 py-1 text-xs" onClick={() => setOpen(!open)}>
          {open ? 'Đóng cấu hình' : 'Cấu hình'}
        </Button>
        <Button className="px-2 py-1 text-xs" disabled={trial === 'running'} onClick={() => void tryRun()}>
          {trial === 'running' ? 'Đang chạy thử…' : 'Chạy thử'}
        </Button>
      </div>
      {open && (
        <div className="mt-3 grid gap-3 border-t border-zinc-200 pt-3 sm:grid-cols-2 dark:border-zinc-800" data-testid={`config-${a.key}`}>
          {a.config_schema.map((f) => (
            <div key={f.key} className={f.type === 'textarea' ? 'sm:col-span-2' : ''}>
              <ConfigInput f={f} value={config[f.key]} disabled={!isOwner} onChange={(v) => setConfig({ ...config, [f.key]: v })} />
            </div>
          ))}
          {isOwner && (
            <div className="flex justify-end sm:col-span-2">
              <Button variant="primary" disabled={busy} onClick={() => void save()}>
                Lưu cấu hình
              </Button>
            </div>
          )}
        </div>
      )}
      {trial && trial !== 'running' && (
        <Modal title={`Chạy thử: ${a.name}`} onClose={() => setTrial(null)}>
          <div className="space-y-3 text-sm" data-testid="trial-result">
            <p className="text-xs text-zinc-500">Khách giả lập, không có tin nào thật sự được gửi hay lưu. Kết quả: {trial.status === 'success' ? 'thành công' : trial.status}.</p>
            {trial.error && <p className="rounded border border-rose-300 bg-rose-50 p-2 text-xs text-rose-800 dark:border-rose-800 dark:bg-rose-950 dark:text-rose-200">{trial.error}</p>}
            {trial.messages.map((m, i) => (
              <pre key={i} className="whitespace-pre-wrap rounded bg-zinc-100 p-3 text-xs dark:bg-zinc-800">{m}</pre>
            ))}
            {trial.messages.length === 0 && !trial.error && <p className="text-xs text-zinc-500">Không có tin nào được tạo.</p>}
            <div className="flex justify-end">
              <Button onClick={() => setTrial(null)}>Đóng</Button>
            </div>
          </div>
        </Modal>
      )}
    </section>
  )
}

/** Việc tự động: the six industry flows as on/off cards with a small config form and a dry run. */
export function AutomationsPage() {
  const { current } = useWorkspace()
  const [rows, setRows] = useState<Automation[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(async () => {
    try {
      setRows(await biz.listAutomations())
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])
  useEffect(() => {
    void load()
  }, [load])
  return (
    <div className="mx-auto max-w-5xl p-4 sm:p-6">
      <h1 className="text-2xl font-semibold">Việc tự động</h1>
      <p className="text-sm text-zinc-500">Bật việc nào thì FlowForge tự làm việc đó. Bấm “Chạy thử” để xem tin nhắn mẫu với khách giả lập trước khi bật.</p>
      <ErrorBanner message={error} onClose={() => setError(null)} />
      {rows === null ? (
        <p className="mt-4 text-sm text-zinc-500">Đang tải…</p>
      ) : (
        <div className="mt-4 grid gap-4 md:grid-cols-2">
          {rows.map((a) => (
            <Card key={a.key} a={a} isOwner={current?.role === 'owner'} onChange={load} />
          ))}
        </div>
      )}
    </div>
  )
}
