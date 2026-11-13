import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { biz, type AiSettings, type DocKind, type KbDocument, type KbPreview } from '../../api/biz'
import { Button, ErrorBanner } from '../../components/ui'
import { formatDateVi } from '../../lib/format'
import { useWorkspace } from '../../lib/WorkspaceContext'

const field = 'mt-1 w-full rounded border border-zinc-300 bg-white px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-900'
const label = 'block text-xs font-medium text-zinc-600 dark:text-zinc-400'
const KIND_LABEL: Record<DocKind, string> = { price: 'Bảng giá', package: 'Mô tả gói / dịch vụ', rules: 'Nội quy', faq: 'Câu hỏi thường gặp', other: 'Tài liệu khác' }
type Tab = 'docs' | 'rules' | 'tone' | 'guard' | 'try'
const TABS: [Tab, string][] = [['docs', 'Tài liệu'], ['rules', 'Điều cấm'], ['tone', 'Giọng điệu'], ['guard', 'Rào chắn'], ['try', 'Thử hỏi']]

const formatSize = (n: number) => (n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`)

/** Trợ lý AI: knowledge documents, forbidden rules, tone, safety guard and a dry-run preview. */
export function AssistantPage() {
  const { current } = useWorkspace()
  const isOwner = current?.role === 'owner'
  const [tab, setTab] = useState<Tab>('docs')
  const [docs, setDocs] = useState<KbDocument[] | null>(null)
  const [settings, setSettings] = useState<AiSettings | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const [uploadKind, setUploadKind] = useState<DocKind>('price')
  const [pasted, setPasted] = useState({ title: '', kind: 'faq' as DocKind, text: '' })
  const [forbiddenText, setForbiddenText] = useState('')
  const [examplesText, setExamplesText] = useState('')
  const [keywordsText, setKeywordsText] = useState('')
  const [question, setQuestion] = useState('')
  const [preview, setPreview] = useState<KbPreview | null>(null)

  const load = useCallback(async () => {
    try {
      const [d, s] = await Promise.all([biz.listKbDocuments(), biz.getAiSettings()])
      setDocs(d)
      setSettings(s)
      setForbiddenText(s.forbidden.join('\n'))
      setExamplesText(s.style_examples.join('\n'))
      setKeywordsText(s.handoff_keywords.join(', '))
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])
  useEffect(() => {
    void load()
  }, [load])

  const run = async (fn: () => Promise<unknown>, ok?: string) => {
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      await fn()
      await load()
      if (ok) setNotice(ok)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const upload = async (f: File | undefined) => {
    if (!f) return
    await run(() => biz.uploadKbDocument(f, uploadKind, f.name.replace(/\.[^.]+$/, '')), `Đã thêm “${f.name}”.`)
    if (fileRef.current) fileRef.current.value = ''
  }
  const paste = async (e: FormEvent) => {
    e.preventDefault()
    await run(() => biz.addKbText(pasted.title.trim(), pasted.kind, pasted.text), 'Đã thêm tài liệu.')
    setPasted({ title: '', kind: 'faq', text: '' })
  }
  const saveSettings = (patch: Partial<AiSettings>) => run(() => biz.updateAiSettings(patch), 'Đã lưu.')
  const lines = (t: string) => t.split('\n').map((x) => x.trim()).filter(Boolean)
  const ask = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      setPreview(await biz.kbPreview(question))
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (!settings || docs === null) return <p className="p-6 text-sm text-zinc-500">{error ? <ErrorBanner message={error} /> : 'Đang tải…'}</p>

  return (
    <div className="mx-auto max-w-5xl p-4 sm:p-6">
      <h1 className="text-2xl font-semibold">Trợ lý AI</h1>
      <p className="text-sm text-zinc-500">Dạy AI về cơ sở của bạn: tài liệu để tra cứu, điều cấm, cách xưng hô và rào chắn an toàn. Nhân viên chỉ xem.</p>
      <div className="mt-3 flex flex-wrap gap-1 border-b border-zinc-200 dark:border-zinc-800">
        {TABS.map(([t, l]) => (
          <button key={t} type="button" onClick={() => setTab(t)} className={`px-3 py-1.5 text-sm ${tab === t ? 'border-b-2 border-zinc-900 font-medium dark:border-zinc-100' : 'text-zinc-500'}`}>
            {l}
          </button>
        ))}
      </div>
      <ErrorBanner message={error} onClose={() => setError(null)} />
      {notice && (
        <p className="mt-3 rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-200" role="status">
          {notice}
        </p>
      )}

      {tab === 'docs' && (
        <section className="mt-4 space-y-4">
          {isOwner && (
            <div className="grid gap-4 md:grid-cols-2">
              <div className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-800">
                <h2 className="text-sm font-semibold">Tải file lên</h2>
                <p className="text-xs text-zinc-500">PDF, Word, Excel, CSV hoặc văn bản. Bảng giá, mô tả gói, nội quy, FAQ.</p>
                <label htmlFor="kb-kind" className={`${label} mt-2`}>Loại tài liệu</label>
                <select id="kb-kind" className={field} value={uploadKind} onChange={(e) => setUploadKind(e.target.value as DocKind)}>
                  {(Object.keys(KIND_LABEL) as DocKind[]).map((k) => (
                    <option key={k} value={k}>
                      {KIND_LABEL[k]}
                    </option>
                  ))}
                </select>
                <input ref={fileRef} type="file" accept=".pdf,.docx,.xlsx,.csv,.txt,.md" className="mt-2 text-sm" data-testid="kb-file" disabled={busy} onChange={(e) => void upload(e.target.files?.[0])} />
              </div>
              <form onSubmit={(e) => void paste(e)} className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-800">
                <h2 className="text-sm font-semibold">Hoặc dán văn bản</h2>
                <div className="mt-2 grid grid-cols-[1fr_auto] gap-2">
                  <input aria-label="Tiêu đề" className={field} placeholder="Tiêu đề, ví dụ: FAQ giữ chỗ" value={pasted.title} onChange={(e) => setPasted({ ...pasted, title: e.target.value })} required />
                  <select aria-label="Loại" className={field} value={pasted.kind} onChange={(e) => setPasted({ ...pasted, kind: e.target.value as DocKind })}>
                    {(Object.keys(KIND_LABEL) as DocKind[]).map((k) => (
                      <option key={k} value={k}>
                        {KIND_LABEL[k]}
                      </option>
                    ))}
                  </select>
                </div>
                <textarea aria-label="Nội dung" className={`${field} h-24`} placeholder="Mỗi câu hỏi và trả lời cách nhau một dòng trống…" value={pasted.text} onChange={(e) => setPasted({ ...pasted, text: e.target.value })} required />
                <div className="mt-2 flex justify-end">
                  <Button type="submit" variant="primary" disabled={busy || !pasted.title.trim() || !pasted.text.trim()}>
                    Thêm
                  </Button>
                </div>
              </form>
            </div>
          )}
          <ul className="divide-y divide-zinc-100 rounded-lg border border-zinc-200 bg-white dark:divide-zinc-800 dark:border-zinc-800 dark:bg-zinc-900" data-testid="kb-docs">
            {docs.map((d) => (
              <li key={d.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-sm">
                <div className="min-w-0 flex-1">
                  <span className="font-medium">{d.title}</span>
                  <span className="ml-2 rounded bg-zinc-100 px-1.5 py-0.5 text-[11px] dark:bg-zinc-800">{d.kind_label}</span>
                  {d.status === 'error' ? <span className="ml-2 text-xs text-rose-600">{d.error}</span> : <span className="ml-2 text-xs text-zinc-500">{d.chunk_count} đoạn</span>}
                  <p className="truncate text-xs text-zinc-500">
                    {d.filename} · {formatSize(d.size)} · {formatDateVi(d.created_at)}
                  </p>
                </div>
                {isOwner && (
                  <button type="button" className="text-xs text-rose-600 hover:underline" onClick={() => window.confirm(`Xoá “${d.title}”?`) && void run(() => biz.deleteKbDocument(d.id))}>
                    Xoá
                  </button>
                )}
              </li>
            ))}
            {docs.length === 0 && <li className="px-3 py-4 text-sm text-zinc-500">Chưa có tài liệu. AI chỉ biết các dịch vụ trong mục Gói & dịch vụ.</li>}
          </ul>
        </section>
      )}

      {tab === 'rules' && (
        <section className="mt-4 space-y-3">
          <p className="text-sm text-zinc-600 dark:text-zinc-400">Mỗi dòng một điều AI không được làm. Chúng được chèn thẳng vào hướng dẫn hệ thống, ví dụ: không giảm giá, không hứa giữ chỗ, không nhận đặt lịch sau 21h.</p>
          <textarea aria-label="Điều cấm" className={`${field} h-40`} value={forbiddenText} disabled={!isOwner} onChange={(e) => setForbiddenText(e.target.value)} />
          {isOwner && (
            <div className="flex justify-end">
              <Button variant="primary" disabled={busy} onClick={() => void saveSettings({ forbidden: lines(forbiddenText) })}>
                Lưu điều cấm
              </Button>
            </div>
          )}
        </section>
      )}

      {tab === 'tone' && (
        <section className="mt-4 space-y-3">
          <div className="grid gap-3 sm:grid-cols-3">
            <div>
              <label htmlFor="tone-self" className={label}>AI tự xưng là</label>
              <select id="tone-self" className={field} value={settings.address_self} disabled={!isOwner} onChange={(e) => void saveSettings({ address_self: e.target.value })}>
                {['em', 'mình', 'phòng tập', 'spa', current?.name ?? 'cơ sở'].map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="tone-customer" className={label}>Gọi khách là</label>
              <select id="tone-customer" className={field} value={settings.address_customer} disabled={!isOwner} onChange={(e) => void saveSettings({ address_customer: e.target.value })}>
                {['anh/chị', 'bạn', 'quý khách', 'chị', 'anh'].map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="tone-level" className={label}>Mức thân thiện</label>
              <select id="tone-level" className={field} value={settings.friendliness} disabled={!isOwner} onChange={(e) => void saveSettings({ friendliness: Number(e.target.value) })}>
                <option value={1}>1 · Trang trọng</option>
                <option value={2}>2 · Thân thiện, chuyên nghiệp</option>
                <option value={3}>3 · Rất thoải mái, có emoji</option>
              </select>
            </div>
          </div>
          <div>
            <label htmlFor="tone-examples" className={label}>Ví dụ tin nhắn mẫu (mỗi dòng một câu, AI sẽ bắt chước văn phong)</label>
            <textarea id="tone-examples" className={`${field} h-32`} placeholder={'Dạ chào chị, em là Mai ở Gym Sức Sống ạ 😊\nMình giữ chỗ 9h sáng mai cho bạn nhé!'} value={examplesText} disabled={!isOwner} onChange={(e) => setExamplesText(e.target.value)} />
            {isOwner && (
              <div className="mt-2 flex justify-end">
                <Button variant="primary" disabled={busy} onClick={() => void saveSettings({ style_examples: lines(examplesText) })}>
                  Lưu ví dụ
                </Button>
              </div>
            )}
          </div>
        </section>
      )}

      {tab === 'guard' && (
        <section className="mt-4 space-y-3">
          <label className="flex items-start gap-2 text-sm">
            <input type="checkbox" className="mt-1" checked={settings.medical_guard} disabled={!isOwner} onChange={(e) => void saveSettings({ medical_guard: e.target.checked })} />
            <span>
              <b>Không tư vấn y tế, chấn thương, thuốc, dinh dưỡng chuyên sâu.</b>
              <span className="block text-xs text-zinc-500">Khi khách hỏi những chủ đề này ({settings.guard_categories.join(', ')}), AI gửi câu chuyển nhân viên và dừng lại, hội thoại được đánh dấu cần người xử lý.</span>
            </span>
          </label>
          <div>
            <label htmlFor="guard-keywords" className={label}>Từ khoá thêm cần chuyển người (phẩy)</label>
            <input id="guard-keywords" className={field} placeholder="bảo hiểm, khiếu nại, hoàn tiền" value={keywordsText} disabled={!isOwner} onChange={(e) => setKeywordsText(e.target.value)} />
          </div>
          <div>
            <label htmlFor="guard-message" className={label}>Câu chuyển nhân viên ({'{self}'} và {'{customer}'} được thay bằng cách xưng hô)</label>
            <textarea id="guard-message" className={`${field} h-20`} value={settings.handoff_message} disabled={!isOwner} onChange={(e) => setSettings({ ...settings, handoff_message: e.target.value })} />
            <p className="mt-1 text-xs text-zinc-500">Khách sẽ thấy: “{settings.handoff_preview}”</p>
          </div>
          {isOwner && (
            <div className="flex justify-end">
              <Button variant="primary" disabled={busy} onClick={() => void saveSettings({ handoff_keywords: keywordsText.split(',').map((x) => x.trim()).filter(Boolean), handoff_message: settings.handoff_message })}>
                Lưu rào chắn
              </Button>
            </div>
          )}
        </section>
      )}

      {tab === 'try' && (
        <section className="mt-4 space-y-3">
          <form onSubmit={(e) => void ask(e)} className="flex gap-2">
            <input aria-label="Câu hỏi thử" className={field} placeholder="Ví dụ: Gói 3 tháng giá bao nhiêu?" value={question} onChange={(e) => setQuestion(e.target.value)} />
            <Button type="submit" variant="primary" disabled={busy || !question.trim()} className="mt-1">
              Xem AI sẽ thấy gì
            </Button>
          </form>
          {preview && (
            <div className="space-y-3" data-testid="kb-preview">
              {preview.guard.handoff ? (
                <p className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100">
                  <b>Chuyển nhân viên</b> ({preview.guard.category}{preview.guard.matched ? ` · “${preview.guard.matched}”` : ''}). AI sẽ trả lời: “{preview.guard.reply}”
                </p>
              ) : (
                <p className="rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-200">AI được phép trả lời câu này.</p>
              )}
              <div>
                <h3 className="text-xs font-semibold uppercase text-zinc-500">Đoạn tài liệu tìm được ({preview.hits.length})</h3>
                <ul className="mt-1 space-y-1">
                  {preview.hits.map((h) => (
                    <li key={h.chunk_id} className="rounded border border-zinc-200 p-2 text-xs dark:border-zinc-800">
                      <span className="font-medium">{h.title}</span> <span className="text-zinc-500">· điểm {h.score}</span>
                      <p className="mt-1 whitespace-pre-wrap text-zinc-600 dark:text-zinc-400">{h.text.slice(0, 400)}</p>
                    </li>
                  ))}
                  {preview.hits.length === 0 && <li className="text-xs text-zinc-500">Không tìm thấy đoạn nào liên quan.</li>}
                </ul>
              </div>
              <details>
                <summary className="cursor-pointer text-xs font-semibold uppercase text-zinc-500">Hướng dẫn hệ thống đầy đủ</summary>
                <pre className="mt-1 max-h-96 overflow-auto whitespace-pre-wrap rounded bg-zinc-100 p-3 text-xs dark:bg-zinc-800">{preview.system_prompt}</pre>
              </details>
            </div>
          )}
        </section>
      )}
    </div>
  )
}
