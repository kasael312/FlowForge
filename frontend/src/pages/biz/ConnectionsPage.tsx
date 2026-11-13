import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { biz, type Connector, type ConnectorKind, type ZnsTemplate } from '../../api/biz'
import { Button, ErrorBanner } from '../../components/ui'
import { formatDateVi } from '../../lib/format'
import { useWorkspace } from '../../lib/WorkspaceContext'

const field = 'mt-1 w-full rounded border border-zinc-300 bg-white px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-900'
const label = 'block text-xs font-medium text-zinc-600 dark:text-zinc-400'

interface Step {
  title: string
  text: string
  /** Small illustration of what the screen looks like at that step. */
  picture: ReactNode
}

/** Minimal line drawings so each step has a picture without shipping screenshots. */
function Illustration({ children, caption }: { children: ReactNode; caption: string }) {
  return (
    <figure className="w-full max-w-xs">
      <svg viewBox="0 0 220 110" className="w-full rounded border border-zinc-200 bg-white text-zinc-500 dark:border-zinc-700 dark:bg-zinc-900" role="img" aria-label={caption}>
        <rect x="6" y="6" width="208" height="98" rx="6" fill="none" stroke="currentColor" strokeWidth="1.5" />
        <rect x="6" y="6" width="208" height="16" rx="6" fill="currentColor" opacity="0.12" />
        {children}
      </svg>
      <figcaption className="mt-1 text-[11px] text-zinc-500">{caption}</figcaption>
    </figure>
  )
}
const Menu = ({ items }: { items: string[] }) => (
  <>
    {items.map((t, i) => (
      <text key={t} x="14" y={36 + i * 16} fontSize="8" fill="currentColor" fontWeight={i === items.length - 1 ? 'bold' : 'normal'}>
        {i === items.length - 1 ? '▶ ' : '• '}
        {t}
      </text>
    ))}
  </>
)
const Field = ({ y, name, value }: { y: number; name: string; value: string }) => (
  <>
    <text x="14" y={y} fontSize="7" fill="currentColor">
      {name}
    </text>
    <rect x="14" y={y + 3} width="190" height="12" rx="2" fill="none" stroke="currentColor" />
    <text x="18" y={y + 12} fontSize="7" fill="currentColor" opacity="0.7">
      {value}
    </text>
  </>
)
const Btn = ({ x, y, text }: { x: number; y: number; text: string }) => (
  <>
    <rect x={x} y={y} width={text.length * 5 + 14} height="14" rx="3" fill="currentColor" opacity="0.85" />
    <text x={x + 7} y={y + 10} fontSize="7" fill="#fff">
      {text}
    </text>
  </>
)

const GUIDES: Record<ConnectorKind, { title: string; intro: string; steps: Step[]; fields: { key: string; label: string; secret: boolean; hint?: string }[] }> = {
  zalo: {
    title: 'Zalo Official Account',
    intro: 'Nhận tin nhắn khách gửi tới OA, trả lời trong 24 giờ và gửi ZNS (tin có phí theo mẫu) ngoài 24 giờ.',
    steps: [
      { title: 'Tạo ứng dụng trên Zalo for Developers', text: 'Vào developers.zalo.me → Ứng dụng của tôi → Tạo ứng dụng. Ghi lại App ID và Secret key.', picture: <Illustration caption="developers.zalo.me → Tạo ứng dụng"><Menu items={['Trang chủ', 'Ứng dụng của tôi', 'Tạo ứng dụng mới']} /><Field y={30 + 0} name="" value="" /><Field y={78} name="App ID: 1234567890" value="Secret key: ••••••••" /></Illustration> },
      { title: 'Liên kết OA và cấp quyền', text: 'Trong ứng dụng, mở Official Account → Liên kết OA của bạn, bật quyền gửi tin và quản lý ZNS. Lấy Access token và Refresh token bằng công cụ OAuth của Zalo.', picture: <Illustration caption="Ứng dụng → Official Account → Liên kết"><Menu items={['Cài đặt', 'Webhook', 'Official Account']} /><Btn x={120} y={60} text="Liên kết OA" /><Btn x={120} y={80} text="Lấy access token" /></Illustration> },
      { title: 'Khai báo Webhook', text: 'Trong ứng dụng → Webhook, dán URL webhook bên dưới, chọn các sự kiện “Người dùng gửi tin nhắn”. Zalo sẽ gửi tin khách nhắn tới FlowForge.', picture: <Illustration caption="Webhook URL + sự kiện user_send_text"><Field y={30} name="Webhook URL" value="https://…/hooks/zalo/…" /><text x="14" y="70" fontSize="7" fill="currentColor">☑ user_send_text   ☑ user_send_image</text><Btn x={160} y={82} text="Lưu" /></Illustration> },
      { title: 'Mẫu ZNS (tuỳ chọn)', text: 'Tại oa.zalo.me → ZNS → Mẫu tin, tạo mẫu nhắc lịch / gia hạn và chờ duyệt. Nhập Template ID, tên và giá ước tính (đ/tin) vào bảng bên dưới để AI gửi được ngoài 24 giờ.', picture: <Illustration caption="oa.zalo.me → ZNS → Mẫu tin"><Menu items={['Tin nhắn', 'ZNS', 'Mẫu tin']} /><Field y={62} name="Template ID 1234  ·  Nhắc lịch hẹn  ·  ~350đ" value="Trạng thái: Đã duyệt" /></Illustration> },
    ],
    fields: [
      { key: 'app_id', label: 'App ID', secret: false },
      { key: 'oa_id', label: 'OA ID', secret: false, hint: 'tự điền sau khi kiểm tra kết nối' },
      { key: 'access_token', label: 'Access token', secret: true },
      { key: 'refresh_token', label: 'Refresh token', secret: true, hint: 'để FlowForge tự làm mới token mỗi giờ' },
      { key: 'app_secret', label: 'Secret key (OA secret)', secret: true, hint: 'dùng xác thực chữ ký webhook và làm mới token' },
    ],
  },
  facebook: {
    title: 'Facebook Page / Messenger',
    intro: 'Nhận tin Messenger của Page, trả lời trong 24 giờ; ngoài 24 giờ phải gắn tag theo chính sách Meta (nhắc lịch, cập nhật sau mua…).',
    steps: [
      { title: 'Tạo ứng dụng Meta', text: 'Vào developers.facebook.com → My Apps → Create App (loại Business). Thêm sản phẩm Messenger. Ghi lại App Secret ở Settings → Basic.', picture: <Illustration caption="developers.facebook.com → Create App → Messenger"><Menu items={['My Apps', 'Create App', 'Add product: Messenger']} /><Field y={78} name="App Secret" value="••••••••••••" /></Illustration> },
      { title: 'Cấp quyền cho Page và lấy token', text: 'Messenger → Settings → Access Tokens → Add or Remove Pages, chọn Page của bạn và bấm Generate Token (quyền pages_messaging). Dán Page access token vào ô bên dưới.', picture: <Illustration caption="Messenger → Access Tokens → Generate Token"><Field y={30} name="Page: Gym Sức Sống" value="pages_messaging ✓" /><Btn x={14} y={70} text="Generate Token" /><Field y={86} name="" value="EAAB…" /></Illustration> },
      { title: 'Đăng ký Webhook', text: 'Messenger → Webhooks → Add Callback URL: dán URL và Verify token bên dưới, bấm Verify and Save, rồi Subscribe sự kiện “messages” cho Page.', picture: <Illustration caption="Webhooks → Callback URL + Verify token"><Field y={28} name="Callback URL" value="https://…/hooks/facebook/…" /><Field y={52} name="Verify token" value="(mã bên dưới)" /><text x="14" y="86" fontSize="7" fill="currentColor">☑ messages  ☑ messaging_postbacks</text><Btn x={150} y={80} text="Verify and Save" /></Illustration> },
    ],
    fields: [
      { key: 'page_id', label: 'Page ID', secret: false, hint: 'tự điền sau khi kiểm tra kết nối' },
      { key: 'page_access_token', label: 'Page access token', secret: true },
      { key: 'app_secret', label: 'App Secret', secret: true, hint: 'xác thực chữ ký webhook' },
    ],
  },
  google_calendar: {
    title: 'Google Calendar (tuỳ chọn)',
    intro: 'Đồng bộ hai chiều lịch hẹn của PT / kỹ thuật viên với một lịch Google: đặt trong FlowForge thì hiện trên Google, dời hoặc huỷ trên Google thì cập nhật lại.',
    steps: [
      { title: 'Bật Calendar API', text: 'Vào console.cloud.google.com → tạo project → APIs & Services → Enable “Google Calendar API”.', picture: <Illustration caption="Cloud Console → Enable Google Calendar API"><Menu items={['APIs & Services', 'Library', 'Google Calendar API']} /><Btn x={140} y={82} text="Enable" /></Illustration> },
      { title: 'Tạo OAuth client', text: 'Credentials → Create credentials → OAuth client ID (Desktop app). Ghi lại Client ID và Client secret.', picture: <Illustration caption="Credentials → OAuth client ID"><Field y={30} name="Client ID" value="…apps.googleusercontent.com" /><Field y={60} name="Client secret" value="••••••••" /></Illustration> },
      { title: 'Lấy refresh token', text: 'Dùng OAuth Playground (developers.google.com/oauthplayground) với client trên và scope https://www.googleapis.com/auth/calendar để lấy Refresh token. Dán vào ô bên dưới cùng Calendar ID (mặc định “primary”).', picture: <Illustration caption="OAuth Playground → Exchange for tokens"><Field y={28} name="Scope" value="…/auth/calendar" /><Btn x={14} y={56} text="Exchange authorization code for tokens" /><Field y={78} name="Refresh token" value="1//0g…" /></Illustration> },
    ],
    fields: [
      { key: 'calendar_id', label: 'Calendar ID', secret: false, hint: 'primary hoặc email lịch chia sẻ' },
      { key: 'client_id', label: 'Client ID', secret: true },
      { key: 'client_secret', label: 'Client secret', secret: true },
      { key: 'refresh_token', label: 'Refresh token', secret: true },
    ],
  },
}

const STATUS: Record<string, { label: string; cls: string }> = {
  connected: { label: 'Đã kết nối', cls: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-200' },
  error: { label: 'Lỗi kết nối', cls: 'bg-rose-100 text-rose-800 dark:bg-rose-900 dark:text-rose-200' },
  disconnected: { label: 'Chưa kiểm tra', cls: 'bg-zinc-200 text-zinc-700 dark:bg-zinc-700 dark:text-zinc-200' },
  none: { label: 'Chưa thiết lập', cls: 'bg-zinc-100 text-zinc-500 dark:bg-zinc-800' },
}

function ConnectorCard({ kind, row, isOwner, onChange }: { kind: ConnectorKind; row: Connector | null; isOwner: boolean; onChange: () => Promise<void> }) {
  const guide = GUIDES[kind]
  const [open, setOpen] = useState(false)
  const [values, setValues] = useState<Record<string, string>>({})
  const [templates, setTemplates] = useState<ZnsTemplate[]>([])
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    const v: Record<string, string> = {}
    for (const f of guide.fields) if (!f.secret) v[f.key] = String(row?.config[f.key] ?? '')
    setValues(v)
    setTemplates((row?.config.zns_templates as ZnsTemplate[] | undefined) ?? [])
  }, [row, guide])

  const save = async () => {
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const config: Record<string, unknown> = {}
      const secrets: Record<string, string> = {}
      for (const f of guide.fields) {
        if (f.secret) {
          if (values[f.key]) secrets[f.key] = values[f.key]!
        } else config[f.key] = values[f.key] ?? ''
      }
      if (kind === 'zalo') config.zns_templates = templates.filter((t) => t.id.trim())
      await biz.upsertConnector(kind, { config, secrets, enabled: true })
      setValues((v) => Object.fromEntries(Object.entries(v).map(([k, val]) => [k, guide.fields.find((f) => f.key === k)?.secret ? '' : val])))
      setNotice('Đã lưu. Bấm “Kiểm tra kết nối” để xác nhận.')
      await onChange()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const test = async () => {
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const r = await biz.testConnector(kind)
      setNotice(r.test_message ?? (r.status === 'connected' ? 'Kết nối thành công.' : 'Kết nối thất bại.'))
      await onChange()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  const toggle = async () => {
    if (!row) return
    try {
      await biz.upsertConnector(kind, { enabled: !row.enabled })
      await onChange()
    } catch (e) {
      setError((e as Error).message)
    }
  }
  const sync = async () => {
    setBusy(true)
    try {
      const r = await biz.syncCalendar()
      setNotice(`Đồng bộ xong: đẩy ${r.pushed}, nhận mới ${r.created}, dời ${r.moved}, huỷ ${r.cancelled}.`)
      await onChange()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const st = STATUS[row ? row.status : 'none'] ?? STATUS.none!
  return (
    <section className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900" data-testid={`connector-${kind}`}>
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="mr-auto text-lg font-semibold">{guide.title}</h2>
        <span className={`rounded px-2 py-0.5 text-xs font-medium ${st.cls}`} data-testid={`status-${kind}`}>
          {st.label}
          {row && !row.enabled ? ' · tắt' : ''}
        </span>
        {row && isOwner && <Button className="px-2 py-0.5 text-xs" onClick={() => void toggle()}>{row.enabled ? 'Tắt' : 'Bật'}</Button>}
        {row && (
          <Button className="px-2 py-0.5 text-xs" disabled={busy} onClick={() => void test()}>
            Kiểm tra kết nối
          </Button>
        )}
        {row && kind === 'google_calendar' && row.enabled && (
          <Button className="px-2 py-0.5 text-xs" disabled={busy} onClick={() => void sync()}>
            Đồng bộ ngay
          </Button>
        )}
        <Button className="px-2 py-0.5 text-xs" onClick={() => setOpen(!open)}>
          {open ? 'Thu gọn' : row ? 'Chỉnh sửa' : 'Thiết lập'}
        </Button>
      </div>
      <p className="mt-1 text-sm text-zinc-500">{guide.intro}</p>
      {row && (
        <p className="mt-1 text-xs text-zinc-500">
          {row.last_checked_at && `Kiểm tra lần cuối ${formatDateVi(row.last_checked_at)}. `}
          {row.last_event_at && `Nhận tin gần nhất ${formatDateVi(row.last_event_at)}. `}
          {row.last_error && <span className="text-rose-600">{row.last_error}</span>}
        </p>
      )}
      <ErrorBanner message={error} onClose={() => setError(null)} />
      {notice && (
        <p className="mt-2 rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-200" role="status">
          {notice}
        </p>
      )}
      {open && (
        <div className="mt-4 grid gap-6 lg:grid-cols-2">
          <ol className="space-y-4">
            {guide.steps.map((s, i) => (
              <li key={s.title} className="flex gap-3">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-zinc-900 text-xs font-bold text-white dark:bg-zinc-100 dark:text-zinc-900">{i + 1}</span>
                <div className="min-w-0 space-y-2">
                  <p className="text-sm font-medium">{s.title}</p>
                  <p className="text-xs text-zinc-600 dark:text-zinc-400">{s.text}</p>
                  {s.picture}
                </div>
              </li>
            ))}
            {row?.webhook_url && (
              <li className="rounded border border-dashed border-zinc-300 p-3 text-xs dark:border-zinc-700">
                <p className="font-medium">URL webhook của bạn</p>
                <code className="block break-all" data-testid={`webhook-${kind}`}>{row.webhook_url}</code>
                {row.verify_token && (
                  <p className="mt-1">
                    Verify token: <code className="break-all">{row.verify_token}</code>
                  </p>
                )}
                {isOwner && (
                  <button type="button" className="mt-1 text-rose-600 hover:underline" onClick={() => window.confirm('Đổi URL webhook? Bạn phải cập nhật lại bên Zalo/Meta.') && void biz.upsertConnector(kind, { rotate_webhook_token: true }).then(onChange)}>
                    Đổi URL
                  </button>
                )}
              </li>
            )}
          </ol>
          <div className="space-y-3">
            {guide.fields.map((f) => (
              <div key={f.key}>
                <label htmlFor={`${kind}-${f.key}`} className={label}>
                  {f.label}
                  {f.secret && row?.secret_keys.includes(f.key) && <span className="ml-1 text-emerald-600">· đã lưu</span>}
                </label>
                <input id={`${kind}-${f.key}`} type={f.secret ? 'password' : 'text'} className={field} placeholder={f.secret && row?.secret_keys.includes(f.key) ? 'để trống nếu giữ nguyên' : ''} value={values[f.key] ?? ''} disabled={!isOwner} onChange={(e) => setValues({ ...values, [f.key]: e.target.value })} autoComplete="off" />
                {f.hint && <p className="text-[11px] text-zinc-500">{f.hint}</p>}
              </div>
            ))}
            {kind === 'zalo' && (
              <fieldset>
                <div className="flex items-center justify-between">
                  <legend className={label}>Mẫu ZNS (gửi ngoài 24 giờ, có phí)</legend>
                  {isOwner && (
                    <Button className="px-2 py-0.5 text-xs" onClick={() => setTemplates([...templates, { id: '', name: '', price: 0, params: [] }])}>
                      Thêm mẫu
                    </Button>
                  )}
                </div>
                <ul className="mt-1 space-y-1" data-testid="zns-templates">
                  {templates.map((t, i) => (
                    <li key={i} className="grid grid-cols-[6rem_1fr_5rem_1fr_auto] items-center gap-1">
                      <input aria-label={`Template ID ${i + 1}`} className={field} placeholder="ID" value={t.id} disabled={!isOwner} onChange={(e) => setTemplates(templates.map((x, j) => (j === i ? { ...x, id: e.target.value } : x)))} />
                      <input aria-label={`Tên mẫu ${i + 1}`} className={field} placeholder="Tên mẫu" value={t.name} disabled={!isOwner} onChange={(e) => setTemplates(templates.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
                      <input aria-label={`Giá mẫu ${i + 1}`} type="number" className={field} placeholder="đ/tin" value={t.price} disabled={!isOwner} onChange={(e) => setTemplates(templates.map((x, j) => (j === i ? { ...x, price: Number(e.target.value) } : x)))} />
                      <input aria-label={`Tham số mẫu ${i + 1}`} className={field} placeholder="tham số: ten, gio" value={t.params.join(', ')} disabled={!isOwner} onChange={(e) => setTemplates(templates.map((x, j) => (j === i ? { ...x, params: e.target.value.split(',').map((p) => p.trim()).filter(Boolean) } : x)))} />
                      {isOwner && (
                        <button type="button" className="text-xs text-rose-600" onClick={() => setTemplates(templates.filter((_, j) => j !== i))}>
                          ✕
                        </button>
                      )}
                    </li>
                  ))}
                  {templates.length === 0 && <li className="text-[11px] text-zinc-500">Chưa có mẫu. Không có mẫu thì chỉ trả lời được trong 24 giờ.</li>}
                </ul>
              </fieldset>
            )}
            {isOwner && (
              <div className="flex justify-end gap-2">
                <Button variant="primary" disabled={busy} onClick={() => void save()}>
                  {busy ? 'Đang lưu…' : 'Lưu'}
                </Button>
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  )
}

/** Kết nối: Zalo OA, Facebook Page, Google Calendar with step-by-step guides and status. */
export function ConnectionsPage() {
  const { current } = useWorkspace()
  const [rows, setRows] = useState<Connector[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(async () => {
    try {
      setRows(await biz.listConnectors())
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])
  useEffect(() => {
    void load()
  }, [load])
  const isOwner = current?.role === 'owner'
  return (
    <div className="mx-auto max-w-5xl space-y-4 p-4 sm:p-6">
      <div>
        <h1 className="text-2xl font-semibold">Kết nối</h1>
        <p className="text-sm text-zinc-500">Kết nối kênh nhắn tin và lịch. Mỗi mục có hướng dẫn từng bước; sau khi lưu hãy bấm “Kiểm tra kết nối”.</p>
      </div>
      <ErrorBanner message={error} onClose={() => setError(null)} />
      {rows === null ? (
        <p className="text-sm text-zinc-500">Đang tải…</p>
      ) : (
        (['zalo', 'facebook', 'google_calendar'] as ConnectorKind[]).map((kind) => <ConnectorCard key={kind} kind={kind} row={rows.find((r) => r.kind === kind) ?? null} isOwner={isOwner} onChange={load} />)
      )}
    </div>
  )
}
