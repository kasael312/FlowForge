import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { biz, type Appointment, type Conversation, type Customer, type Membership, type Message } from '../../api/biz'
import { MembershipBadge } from './CustomersPage'
import { Button, ErrorBanner } from '../../components/ui'
import { formatDateVi } from '../../lib/format'

const field = 'w-full rounded border border-zinc-300 bg-white px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-900'
const CHANNEL: Record<string, { label: string; cls: string }> = {
  zalo: { label: 'Zalo', cls: 'bg-sky-100 text-sky-800 dark:bg-sky-900 dark:text-sky-100' },
  facebook: { label: 'FB', cls: 'bg-indigo-100 text-indigo-800 dark:bg-indigo-900 dark:text-indigo-100' },
}

/** Hộp thư hợp nhất: Zalo + Facebook, AI suggestions with Gửi / Sửa / Bỏ, take-over, customer card. */
export function InboxPage() {
  const [convs, setConvs] = useState<Conversation[] | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [card, setCard] = useState<{ customer: Customer; memberships: Membership[]; appointments: Appointment[] } | null>(null)
  const [reply, setReply] = useState('')
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [showCard, setShowCard] = useState(false)
  const [filter, setFilter] = useState<'all' | 'unread'>('all')
  const bottom = useRef<HTMLDivElement>(null)

  const loadList = useCallback(async () => {
    try {
      setConvs(await biz.listConversations(filter === 'unread' ? true : undefined))
    } catch (e) {
      setError((e as Error).message)
    }
  }, [filter])
  const loadThread = useCallback(async (id: string) => {
    try {
      const [m, c] = await Promise.all([biz.listMessages(id), biz.listConversations()])
      setMessages(m)
      const conv = c.find((x) => x.id === id)
      if (conv?.customer_id) {
        const [customer, memberships, appointments] = await Promise.all([biz.getCustomer(conv.customer_id), biz.customerMemberships(conv.customer_id), biz.customerAppointments(conv.customer_id)])
        setCard({ customer, memberships, appointments })
      } else setCard(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  useEffect(() => {
    void loadList()
    const t = setInterval(() => void loadList(), 10_000)
    return () => clearInterval(t)
  }, [loadList])
  useEffect(() => {
    if (!selectedId) return
    void loadThread(selectedId)
    const t = setInterval(() => void loadThread(selectedId), 10_000)
    return () => clearInterval(t)
  }, [selectedId, loadThread])
  useEffect(() => {
    if (typeof bottom.current?.scrollIntoView === 'function') bottom.current.scrollIntoView({ block: 'end' })
  }, [messages])

  const selected = convs?.find((c) => c.id === selectedId) ?? null
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    try {
      await fn()
      if (selectedId) await loadThread(selectedId)
      await loadList()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const open = async (c: Conversation) => {
    setSelectedId(c.id)
    setEditing(null)
    setShowCard(false)
    if (c.unread) {
      try {
        await biz.updateConversation(c.id, { unread: false })
        await loadList()
      } catch {
        /* list refresh will retry */
      }
    }
  }
  const send = async (e: FormEvent) => {
    e.preventDefault()
    if (!selected || !reply.trim()) return
    const text = reply
    await act(async () => {
      await biz.sendMessage(selected.id, { text })
      setReply('')
    })
  }

  const windowHint = selected && !selected.window.open ? (selected.channel === 'zalo' ? 'Khách im hơn 24 giờ: Zalo chỉ cho gửi bằng mẫu ZNS (cấu hình ở Việc tự động / Kết nối).' : 'Khách im hơn 24 giờ: Messenger yêu cầu gắn tag theo chính sách Meta.') : null

  return (
    <div className="flex h-[calc(100vh-3rem)] flex-col md:h-screen">
      <ErrorBanner message={error} onClose={() => setError(null)} />
      <div className="flex min-h-0 flex-1">
        {/* list */}
        <aside className={`w-full shrink-0 border-r border-zinc-200 md:w-80 dark:border-zinc-800 ${selectedId ? 'hidden md:block' : ''}`}>
          <div className="flex items-center gap-2 border-b border-zinc-200 p-3 dark:border-zinc-800">
            <h1 className="mr-auto text-lg font-semibold">Hộp thư</h1>
            <select aria-label="Lọc" className="rounded border border-zinc-300 px-2 py-1 text-xs dark:border-zinc-700 dark:bg-zinc-900" value={filter} onChange={(e) => setFilter(e.target.value as 'all' | 'unread')}>
              <option value="all">Tất cả</option>
              <option value="unread">Chưa đọc</option>
            </select>
          </div>
          <ul className="divide-y divide-zinc-100 overflow-y-auto dark:divide-zinc-800" data-testid="conversation-list">
            {(convs ?? []).map((c) => (
              <li key={c.id}>
                <button type="button" onClick={() => void open(c)} className={`flex w-full items-start gap-2 px-3 py-2 text-left hover:bg-zinc-100 dark:hover:bg-zinc-800 ${c.id === selectedId ? 'bg-zinc-100 dark:bg-zinc-800' : ''}`}>
                  <span className={`mt-0.5 rounded px-1 text-[10px] font-semibold ${CHANNEL[c.channel]?.cls ?? ''}`}>{CHANNEL[c.channel]?.label ?? c.channel}</span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-1">
                      <span className={`truncate text-sm ${c.unread ? 'font-semibold' : ''}`}>{c.customer_name ?? c.external_thread_id}</span>
                      {c.unread && <span className="h-2 w-2 rounded-full bg-sky-500" aria-label="chưa đọc" />}
                      {!c.ai_enabled && <span className="rounded bg-amber-100 px-1 text-[9px] text-amber-800 dark:bg-amber-900 dark:text-amber-100">nhân viên</span>}
                    </span>
                    <span className="block truncate text-xs text-zinc-500">{c.last_text}</span>
                  </span>
                  <span className="text-[10px] text-zinc-400">{c.last_message_at ? formatDateVi(c.last_message_at).slice(0, 5) : ''}</span>
                </button>
              </li>
            ))}
            {convs && convs.length === 0 && <li className="p-4 text-sm text-zinc-500">Chưa có hội thoại. Kết nối Zalo / Facebook để nhận tin.</li>}
          </ul>
        </aside>

        {/* thread */}
        <section className={`flex min-w-0 flex-1 flex-col ${selectedId ? '' : 'hidden md:flex'}`} data-testid="thread">
          {!selected ? (
            <p className="p-6 text-sm text-zinc-500">Chọn một hội thoại.</p>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-2 border-b border-zinc-200 p-3 dark:border-zinc-800">
                <button type="button" className="text-sm md:hidden" onClick={() => setSelectedId(null)}>
                  ‹ Danh sách
                </button>
                <span className="mr-auto font-semibold">{selected.customer_name ?? selected.external_thread_id}</span>
                <Button className="px-2 py-1 text-xs" disabled={busy} onClick={() => void act(() => biz.updateConversation(selected.id, { ai_enabled: !selected.ai_enabled }))}>
                  {selected.ai_enabled ? 'Tôi tiếp quản' : 'Bật lại AI'}
                </Button>
                <Button className="px-2 py-1 text-xs md:hidden" onClick={() => setShowCard(!showCard)}>
                  Thẻ khách
                </Button>
              </div>
              <div className="flex-1 space-y-2 overflow-y-auto p-3" data-testid="messages">
                {messages.map((m) =>
                  m.status === 'ai_suggested' ? (
                    <div key={m.id} className="ml-auto max-w-[85%] rounded-lg border border-violet-300 bg-violet-50 p-2 text-sm dark:border-violet-700 dark:bg-violet-950" data-testid="suggestion">
                      <div className="mb-1 text-[11px] font-semibold text-violet-700 dark:text-violet-300">AI gợi ý</div>
                      {editing?.id === m.id ? (
                        <textarea aria-label="Sửa gợi ý" className={`${field} h-24`} value={editing.text} onChange={(e) => setEditing({ id: m.id, text: e.target.value })} />
                      ) : (
                        <p className="whitespace-pre-wrap">{m.text}</p>
                      )}
                      <div className="mt-2 flex gap-2">
                        <Button variant="primary" className="px-2 py-0.5 text-xs" disabled={busy} onClick={() => void act(() => biz.approveSuggestion(selected.id, m.id, editing?.id === m.id ? editing.text : undefined)).then(() => setEditing(null))}>
                          Gửi
                        </Button>
                        <Button className="px-2 py-0.5 text-xs" onClick={() => setEditing(editing?.id === m.id ? null : { id: m.id, text: m.text })}>
                          {editing?.id === m.id ? 'Huỷ sửa' : 'Sửa'}
                        </Button>
                        <Button variant="ghost" className="px-2 py-0.5 text-xs text-rose-600" disabled={busy} onClick={() => void act(() => biz.discardSuggestion(selected.id, m.id))}>
                          Bỏ
                        </Button>
                      </div>
                    </div>
                  ) : (
                    <div key={m.id} className={`max-w-[85%] rounded-lg p-2 text-sm ${m.direction === 'in' ? 'bg-zinc-100 dark:bg-zinc-800' : 'ml-auto bg-emerald-100 dark:bg-emerald-900'}`}>
                      <p className="whitespace-pre-wrap">{m.text}</p>
                      <div className="mt-1 text-[10px] text-zinc-500">
                        {m.sender === 'customer' ? 'Khách' : m.sender === 'ai' ? (m.status === 'handoff' ? 'AI · chuyển nhân viên' : 'AI') : 'Nhân viên'} · {formatDateVi(m.created_at)}
                      </div>
                    </div>
                  ),
                )}
                <div ref={bottom} />
              </div>
              {windowHint && <p className="border-t border-amber-200 bg-amber-50 px-3 py-1.5 text-xs text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">{windowHint}</p>}
              <form onSubmit={(e) => void send(e)} className="flex gap-2 border-t border-zinc-200 p-3 dark:border-zinc-800">
                <input aria-label="Trả lời" className={field} placeholder="Nhập tin trả lời…" value={reply} onChange={(e) => setReply(e.target.value)} />
                <Button type="submit" variant="primary" disabled={busy || !reply.trim()}>
                  Gửi
                </Button>
              </form>
            </>
          )}
        </section>

        {/* customer card */}
        {selected && (
          <aside className={`w-full shrink-0 overflow-y-auto border-l border-zinc-200 p-3 text-sm md:block md:w-72 dark:border-zinc-800 ${showCard ? 'absolute inset-0 z-10 bg-white dark:bg-zinc-950' : 'hidden'}`} data-testid="customer-card">
            {!card ? (
              <p className="text-zinc-500">Khách chưa có hồ sơ.</p>
            ) : (
              <>
                <div className="flex items-center justify-between">
                  <h2 className="font-semibold">{card.customer.name}</h2>
                  <button type="button" className="text-xs md:hidden" onClick={() => setShowCard(false)}>
                    Đóng
                  </button>
                </div>
                <p className="font-mono text-xs text-zinc-500">{card.customer.phone || 'chưa có SĐT'}</p>
                <div className="mt-2">
                  <MembershipBadge m={card.customer.membership} />
                </div>
                {card.memberships.length > 0 && (
                  <ul className="mt-2 space-y-1 text-xs">
                    {card.memberships.slice(0, 3).map((m) => (
                      <li key={m.id}>
                        {m.service_name} · {m.start_date} → {m.end_date ?? '∞'}
                        {m.sessions_total ? ` · ${m.sessions_used}/${m.sessions_total}` : ''}
                      </li>
                    ))}
                  </ul>
                )}
                <h3 className="mt-3 text-xs font-semibold uppercase text-zinc-500">Lịch gần nhất</h3>
                {card.appointments.length === 0 ? (
                  <p className="text-xs text-zinc-500">Chưa có.</p>
                ) : (
                  <ul className="mt-1 space-y-1 text-xs">
                    {card.appointments.slice(0, 3).map((a) => (
                      <li key={a.id}>
                        {formatDateVi(a.start_at)} · {a.service_name ?? 'dịch vụ'} · {a.status}
                      </li>
                    ))}
                  </ul>
                )}
                {card.customer.notes && (
                  <>
                    <h3 className="mt-3 text-xs font-semibold uppercase text-zinc-500">Ghi chú</h3>
                    <p className="whitespace-pre-wrap text-xs">{card.customer.notes}</p>
                  </>
                )}
              </>
            )}
          </aside>
        )}
      </div>
    </div>
  )
}
