import { useCallback, useEffect, useState } from 'react'
import { STATUS_LABEL, billing, vnd, type BillingState, type Invoice, type Plan } from '../../api/billing'
import { Button, ErrorBanner } from '../../components/ui'
import { formatDateVi } from '../../lib/format'
import { useWorkspace } from '../../lib/WorkspaceContext'

const INVOICE_STATUS: Record<Invoice['status'], string> = { pending: 'Chờ xác nhận', paid: 'Đã thanh toán', cancelled: 'Đã huỷ' }

function Meter({ label, used, limit }: { label: string; used: number; limit: number }) {
  const pct = limit ? Math.min(100, Math.round((used / limit) * 100)) : 0
  const tone = pct >= 100 ? 'bg-rose-500' : pct >= 80 ? 'bg-amber-500' : 'bg-emerald-500'
  return (
    <div>
      <div className="flex justify-between text-xs">
        <span>{label}</span>
        <span className="tabular-nums">
          {used} / {limit}
        </span>
      </div>
      <div className="mt-1 h-2 overflow-hidden rounded bg-zinc-200 dark:bg-zinc-800" role="progressbar" aria-label={label} aria-valuenow={used} aria-valuemax={limit}>
        <div className={`h-full ${tone}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

/** Gói & thanh toán: current plan, usage meters, the catalogue and VietQR payment. */
export function BillingPage() {
  const { current } = useWorkspace()
  const [state, setState] = useState<BillingState | null>(null)
  const [invoices, setInvoices] = useState<Invoice[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [months, setMonths] = useState(1)
  const isOwner = current?.role === 'owner'

  const load = useCallback(async () => {
    try {
      const [s, inv] = await Promise.all([billing.get(), billing.listInvoices()])
      setState(s)
      setInvoices(inv)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])
  useEffect(() => {
    void load()
  }, [load])

  const choose = async (plan: Plan) => {
    setBusy(true)
    setError(null)
    try {
      await billing.createInvoice(plan.key, months)
      await load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const cancel = async (inv: Invoice) => {
    if (!window.confirm('Huỷ yêu cầu thanh toán này?')) return
    try {
      await billing.cancelInvoice(inv.id)
      await load()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  if (!state) return <p className="p-6 text-sm text-zinc-500">{error ?? 'Đang tải…'}</p>
  const pending = state.pending_invoice
  const periodEnd = state.current_period_end ?? state.trial_ends_at

  return (
    <div className="mx-auto max-w-4xl p-4 sm:p-6">
      <h1 className="text-2xl font-semibold">Gói & thanh toán</h1>
      <p className="text-sm text-zinc-500">Gói hiện tại, mức dùng tháng này và cách thanh toán.</p>
      <ErrorBanner message={error} onClose={() => setError(null)} />
      {state.warnings.map((w) => (
        <p key={w} className="mt-3 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100" role="status">
          {w}
        </p>
      ))}

      <section className="mt-4 grid gap-4 sm:grid-cols-2" data-testid="billing-current">
        <div className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
          <div className="text-xs uppercase tracking-wide text-zinc-500">Gói hiện tại</div>
          <div className="mt-1 text-2xl font-semibold">{state.plan.name}</div>
          <div className="text-sm">
            <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${state.status === 'expired' ? 'bg-rose-100 text-rose-800 dark:bg-rose-900 dark:text-rose-100' : state.status === 'past_due' ? 'bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-100' : 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-100'}`}>{STATUS_LABEL[state.status]}</span>
            {periodEnd && (
              <span className="ml-2 text-zinc-500">
                {state.status === 'expired' ? 'hết hạn' : 'đến'} {formatDateVi(periodEnd, true)}
                {state.days_left !== null && state.status !== 'expired' ? ` (còn ${state.days_left} ngày)` : ''}
              </span>
            )}
          </div>
        </div>
        <div className="space-y-3 rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
          <div className="text-xs uppercase tracking-wide text-zinc-500">Mức dùng tháng {state.usage.month}</div>
          <Meter label="Hội thoại AI" used={state.usage.ai_conversations} limit={state.usage.ai_conversations_limit} />
          <Meter label="Chi nhánh" used={state.usage.branches} limit={state.usage.branches_limit} />
        </div>
      </section>

      {pending && (
        <section className="mt-6 rounded-lg border border-sky-300 bg-sky-50 p-4 dark:border-sky-800 dark:bg-sky-950" data-testid="pending-invoice">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <h2 className="font-semibold">
                Hoá đơn {pending.number} · gói {pending.plan_name} × {pending.months} tháng
              </h2>
              <p className="text-2xl font-semibold tabular-nums">{vnd(pending.amount_vnd)}</p>
            </div>
            <div className="flex gap-2">
              <a className="rounded-md border border-zinc-300 px-3 py-1.5 text-sm dark:border-zinc-700" href={billing.pdfUrl(pending.id)} target="_blank" rel="noreferrer">
                Tải hoá đơn PDF
              </a>
              {isOwner && (
                <Button variant="ghost" className="text-rose-600" onClick={() => void cancel(pending)}>
                  Huỷ
                </Button>
              )}
            </div>
          </div>
          <div className="mt-3 grid gap-4 sm:grid-cols-[auto_1fr]">
            {pending.payment.qr_image_url ? (
              <img src={pending.payment.qr_image_url} alt="Mã VietQR" className="h-56 w-56 rounded bg-white object-contain" />
            ) : (
              <div className="flex h-40 w-56 items-center justify-center rounded border border-dashed border-zinc-400 text-center text-xs text-zinc-500">Chưa cấu hình tài khoản nhận tiền (BILLING_BANK_ACCOUNT).</div>
            )}
            <div className="text-sm">
              <p className="font-medium">Chuyển khoản ngân hàng</p>
              <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5">
                <dt className="text-zinc-500">Ngân hàng</dt>
                <dd>{pending.payment.bank_name || pending.payment.bank_bin || '—'}</dd>
                <dt className="text-zinc-500">Số tài khoản</dt>
                <dd className="font-mono">{pending.payment.account || '—'}</dd>
                <dt className="text-zinc-500">Chủ tài khoản</dt>
                <dd>{pending.payment.account_name || '—'}</dd>
                <dt className="text-zinc-500">Số tiền</dt>
                <dd className="font-semibold">{vnd(pending.amount_vnd)}</dd>
                <dt className="text-zinc-500">Nội dung</dt>
                <dd>
                  <code className="rounded bg-white px-1.5 py-0.5 font-mono text-base dark:bg-zinc-900" data-testid="transfer-code">
                    {pending.transfer_code}
                  </code>
                </dd>
              </dl>
              <p className="mt-2 text-xs text-zinc-600 dark:text-zinc-400">Ghi đúng mã nội dung để hệ thống đối chiếu. Gói được kích hoạt ngay khi quản trị viên xác nhận đã nhận tiền.</p>
            </div>
          </div>
        </section>
      )}

      <section className="mt-6">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="font-semibold">Chọn gói</h2>
          <label className="text-sm">
            Thời hạn{' '}
            <select aria-label="Số tháng" className="rounded border border-zinc-300 px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900" value={months} onChange={(e) => setMonths(Number(e.target.value))}>
              {[1, 3, 6, 12].map((m) => (
                <option key={m} value={m}>
                  {m} tháng
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="mt-3 grid gap-3 sm:grid-cols-3">
          {state.plans.map((p) => {
            const isCurrent = state.plan.key === p.key && state.status !== 'expired'
            return (
              <div key={p.key} className={`flex flex-col rounded-lg border p-4 ${isCurrent ? 'border-zinc-900 dark:border-zinc-100' : 'border-zinc-200 dark:border-zinc-800'}`} data-testid={`plan-${p.key}`}>
                <div className="text-lg font-semibold">{p.name}</div>
                <div className="text-sm text-zinc-500">{p.price_vnd ? `${vnd(p.price_vnd)}/tháng` : `${p.trial_days} ngày miễn phí`}</div>
                <p className="mt-2 flex-1 text-sm">{p.description}</p>
                <ul className="mt-2 text-xs text-zinc-600 dark:text-zinc-400">
                  <li>{p.ai_conversations_month} hội thoại AI / tháng</li>
                  <li>
                    {p.branches} chi nhánh{p.branches > 1 ? '' : ''}
                  </li>
                </ul>
                {p.price_vnd > 0 && isOwner && (
                  <Button variant="primary" className="mt-3" disabled={busy} onClick={() => void choose(p)}>
                    {isCurrent ? 'Gia hạn' : 'Chọn gói'} · {vnd(p.price_vnd * months)}
                  </Button>
                )}
                {isCurrent && <span className="mt-2 text-center text-xs text-zinc-500">Gói hiện tại</span>}
              </div>
            )
          })}
        </div>
      </section>

      <section className="mt-6">
        <h2 className="font-semibold">Lịch sử hoá đơn</h2>
        {invoices.length === 0 ? (
          <p className="mt-1 text-sm text-zinc-500">Chưa có hoá đơn.</p>
        ) : (
          <table className="mt-2 w-full text-sm">
            <thead className="text-left text-xs uppercase text-zinc-500">
              <tr>
                <th className="py-1">Số</th>
                <th>Gói</th>
                <th>Số tiền</th>
                <th>Trạng thái</th>
                <th>Ngày</th>
                <th />
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
              {invoices.map((i) => (
                <tr key={i.id}>
                  <td className="py-1.5 font-mono text-xs">{i.number}</td>
                  <td>
                    {i.plan_name} × {i.months}
                  </td>
                  <td className="tabular-nums">{vnd(i.amount_vnd)}</td>
                  <td>{INVOICE_STATUS[i.status]}</td>
                  <td className="text-xs text-zinc-500">{formatDateVi(i.paid_at ?? i.created_at, true)}</td>
                  <td className="text-right">
                    <a className="text-xs underline" href={billing.pdfUrl(i.id)} target="_blank" rel="noreferrer">
                      PDF
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}
