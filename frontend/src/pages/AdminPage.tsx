import { useCallback, useEffect, useState } from 'react'
import { STATUS_LABEL, admin, vnd, type AdminAlert, type AdminInvoice, type AdminSummary, type AdminWorkspace, type LogEntry } from '../api/billing'
import { Button, ErrorBanner } from '../components/ui'
import { formatDateVi } from '../lib/format'

type Tab = 'workspaces' | 'invoices' | 'alerts'
const TABS: { key: Tab; label: string }[] = [
  { key: 'workspaces', label: 'Cơ sở' },
  { key: 'invoices', label: 'Thanh toán' },
  { key: 'alerts', label: 'Cảnh báo' },
]
const STATUS_CLS: Record<string, string> = {
  trial: 'bg-sky-100 text-sky-800 dark:bg-sky-900 dark:text-sky-100',
  active: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-100',
  past_due: 'bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-100',
  expired: 'bg-rose-100 text-rose-800 dark:bg-rose-900 dark:text-rose-100',
}

/** Quản trị hệ thống: workspaces with usage and trial dates, payments to confirm, alerts, logs. */
export function AdminPage() {
  const [tab, setTab] = useState<Tab>('workspaces')
  const [summary, setSummary] = useState<AdminSummary | null>(null)
  const [rows, setRows] = useState<AdminWorkspace[]>([])
  const [invoices, setInvoices] = useState<AdminInvoice[]>([])
  const [alerts, setAlerts] = useState<AdminAlert[]>([])
  const [q, setQ] = useState('')
  const [logsFor, setLogsFor] = useState<AdminWorkspace | null>(null)
  const [logs, setLogs] = useState<LogEntry[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const [s, w, i, a] = await Promise.all([admin.summary(), admin.workspaces(q || undefined), admin.invoices(), admin.alerts()])
      setSummary(s)
      setRows(w)
      setInvoices(i)
      setAlerts(a)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [q])
  useEffect(() => {
    void load()
  }, [load])

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    try {
      await fn()
      await load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const extend = (w: AdminWorkspace) => {
    const v = window.prompt(`Kéo dài dùng thử cho "${w.name}" thêm bao nhiêu ngày?`, '14')
    if (!v) return
    void act(() => admin.extendTrial(w.id, Number(v)))
  }
  const openLogs = async (w: AdminWorkspace) => {
    setLogsFor(w)
    setLogs(null)
    try {
      setLogs(await admin.logs(w.id))
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <div className="mx-auto max-w-6xl p-4 sm:p-6">
      <h1 className="text-2xl font-semibold">Quản trị hệ thống</h1>
      <p className="text-sm text-zinc-500">Tất cả cơ sở trên hệ thống, mức dùng, ngày hết hạn thử, thanh toán chờ xác nhận và cảnh báo.</p>
      <ErrorBanner message={error} onClose={() => setError(null)} />
      {summary && (
        <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-7" data-testid="admin-summary">
          {[
            ['Cơ sở', summary.workspaces],
            ['Đang thử', summary.trials],
            ['Thử sắp hết (7 ngày)', summary.trials_ending_7d],
            ['Trả phí', summary.paid],
            ['Hết hạn', summary.expired],
            ['Chờ xác nhận', summary.pending_invoices],
            ['Cảnh báo mở', summary.open_alerts],
          ].map(([label, value]) => (
            <div key={String(label)} className="rounded-lg border border-zinc-200 p-2 dark:border-zinc-800">
              <div className="text-[11px] uppercase tracking-wide text-zinc-500">{label}</div>
              <div className="text-xl font-semibold tabular-nums">{value}</div>
            </div>
          ))}
        </div>
      )}
      <div className="mt-4 flex gap-1 border-b border-zinc-200 dark:border-zinc-800" role="tablist">
        {TABS.map((t) => (
          <button key={t.key} type="button" role="tab" aria-selected={tab === t.key} onClick={() => setTab(t.key)} className={`px-3 py-2 text-sm ${tab === t.key ? 'border-b-2 border-zinc-900 font-semibold dark:border-zinc-100' : 'text-zinc-500'}`}>
            {t.label}
            {t.key === 'invoices' && summary?.pending_invoices ? ` (${summary.pending_invoices})` : ''}
            {t.key === 'alerts' && summary?.open_alerts ? ` (${summary.open_alerts})` : ''}
          </button>
        ))}
      </div>

      {tab === 'workspaces' && (
        <section className="mt-3">
          <input aria-label="Tìm cơ sở" placeholder="Tìm theo tên cơ sở…" className="mb-2 w-full max-w-xs rounded border border-zinc-300 px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-900" value={q} onChange={(e) => setQ(e.target.value)} />
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="admin-workspaces">
              <thead className="text-left text-xs uppercase text-zinc-500">
                <tr>
                  <th className="py-1">Cơ sở</th>
                  <th>Chủ</th>
                  <th>Gói</th>
                  <th>Hết hạn</th>
                  <th>AI tháng này</th>
                  <th>Khách</th>
                  <th>Hoạt động</th>
                  <th />
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
                {rows.map((w) => (
                  <tr key={w.id} data-testid={`ws-${w.id}`}>
                    <td className="py-1.5">
                      <b>{w.name}</b>
                      <span className="block text-xs text-zinc-500">{w.industry}</span>
                    </td>
                    <td className="text-xs">{w.owner_email ?? '—'}</td>
                    <td>
                      <span className={`rounded px-1.5 py-0.5 text-xs ${STATUS_CLS[w.status] ?? ''}`}>
                        {w.plan_name}
                        {w.status !== 'trial' ? ` · ${STATUS_LABEL[w.status]}` : ''}
                      </span>
                    </td>
                    <td className="text-xs">
                      {formatDateVi(w.current_period_end ?? w.trial_ends_at, true)}
                      {w.days_left !== null && <span className="block text-zinc-500">còn {w.days_left} ngày</span>}
                    </td>
                    <td className="tabular-nums">
                      {w.usage.ai_conversations}/{w.usage.ai_conversations_limit}
                    </td>
                    <td className="tabular-nums">{w.customers}</td>
                    <td className="text-xs text-zinc-500">{formatDateVi(w.last_activity_at)}</td>
                    <td className="whitespace-nowrap text-right">
                      <Button className="px-2 py-1 text-xs" disabled={busy} onClick={() => extend(w)}>
                        Kéo dài thử
                      </Button>{' '}
                      <Button className="px-2 py-1 text-xs" onClick={() => void openLogs(w)}>
                        Log{w.open_alerts ? ` · ${w.open_alerts} cảnh báo` : ''}
                      </Button>
                    </td>
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr>
                    <td colSpan={8} className="py-3 text-sm text-zinc-500">
                      Không có cơ sở nào.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {tab === 'invoices' && (
        <section className="mt-3 overflow-x-auto">
          <table className="w-full text-sm" data-testid="admin-invoices">
            <thead className="text-left text-xs uppercase text-zinc-500">
              <tr>
                <th className="py-1">Số</th>
                <th>Cơ sở</th>
                <th>Gói</th>
                <th>Số tiền</th>
                <th>Mã CK</th>
                <th>Trạng thái</th>
                <th />
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
              {invoices.map((i) => (
                <tr key={i.id}>
                  <td className="py-1.5 font-mono text-xs">{i.number}</td>
                  <td>{i.workspace_name}</td>
                  <td>
                    {i.plan_name} × {i.months}
                  </td>
                  <td className="tabular-nums">{vnd(i.amount_vnd)}</td>
                  <td className="font-mono">{i.transfer_code}</td>
                  <td className="text-xs">{i.status === 'pending' ? 'Chờ xác nhận' : i.status === 'paid' ? `Đã trả ${formatDateVi(i.paid_at, true)}` : 'Đã huỷ'}</td>
                  <td className="whitespace-nowrap text-right">
                    {i.status === 'pending' && (
                      <>
                        <Button variant="primary" className="px-2 py-1 text-xs" disabled={busy} onClick={() => void act(() => admin.confirmInvoice(i.id))}>
                          Đã nhận tiền
                        </Button>{' '}
                        <Button variant="ghost" className="px-2 py-1 text-xs text-rose-600" disabled={busy} onClick={() => void act(() => admin.cancelInvoice(i.id))}>
                          Huỷ
                        </Button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
              {invoices.length === 0 && (
                <tr>
                  <td colSpan={7} className="py-3 text-sm text-zinc-500">
                    Chưa có hoá đơn.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </section>
      )}

      {tab === 'alerts' && (
        <section className="mt-3 space-y-2" data-testid="admin-alerts">
          {alerts.length === 0 && <p className="text-sm text-zinc-500">Không có cảnh báo.</p>}
          {alerts.map((a) => (
            <div key={a.id} className={`rounded-lg border p-3 text-sm ${a.read ? 'border-zinc-200 opacity-60 dark:border-zinc-800' : 'border-rose-300 dark:border-rose-800'}`}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <b>
                  {a.workspace_name} · {a.title}
                </b>
                <span className="text-xs text-zinc-500">{formatDateVi(a.created_at)}</span>
              </div>
              <pre className="mt-1 whitespace-pre-wrap font-sans text-xs">{a.body}</pre>
              {!a.read && (
                <button type="button" className="mt-1 text-xs text-sky-700 hover:underline dark:text-sky-400" onClick={() => void act(() => admin.readAlert(a.id))}>
                  Đã xử lý
                </button>
              )}
            </div>
          ))}
        </section>
      )}

      {logsFor && (
        <div className="fixed inset-0 z-40 flex items-end justify-center bg-black/40 p-4 sm:items-center" onClick={() => setLogsFor(null)}>
          <div className="max-h-[80vh] w-full max-w-3xl overflow-y-auto rounded-lg bg-white p-4 shadow-xl dark:bg-zinc-900" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Log cơ sở">
            <div className="flex items-center justify-between">
              <h2 className="font-semibold">Log · {logsFor.name}</h2>
              <button type="button" className="text-sm text-zinc-500" onClick={() => setLogsFor(null)}>
                Đóng
              </button>
            </div>
            {!logs ? (
              <p className="mt-2 text-sm text-zinc-500">Đang tải…</p>
            ) : logs.length === 0 ? (
              <p className="mt-2 text-sm text-zinc-500">Chưa có log.</p>
            ) : (
              <ul className="mt-2 divide-y divide-zinc-100 text-xs dark:divide-zinc-800" data-testid="admin-logs">
                {logs.map((l, i) => (
                  <li key={i} className="flex gap-2 py-1">
                    <span className="w-28 shrink-0 text-zinc-500">{formatDateVi(l.time)}</span>
                    <span className={`w-20 shrink-0 rounded px-1 text-center ${l.kind === 'alert' ? 'bg-rose-100 dark:bg-rose-900' : 'bg-zinc-100 dark:bg-zinc-800'}`}>{l.kind}</span>
                    <span className="min-w-0 flex-1 break-words">{l.text}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
