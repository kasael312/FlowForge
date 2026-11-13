import { useState } from 'react'
import { biz, type ImportEntity, type ImportPreview, type ImportResult } from '../../api/biz'
import { Button, ErrorBanner, Modal } from '../ui'

const ENTITY_LABEL: Record<ImportEntity, string> = { customers: 'Khách hàng', memberships: 'Gói đã bán', services: 'Gói & dịch vụ' }
const field = 'mt-1 w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900'

/** Three steps: choose a file → map columns (with a preview) → import and show counts/errors. */
export function ImportWizard({ entity, onClose, onDone }: { entity: ImportEntity; onClose: () => void; onDone: () => void }) {
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<ImportPreview | null>(null)
  const [mapping, setMapping] = useState<Record<string, string>>({})
  const [result, setResult] = useState<ImportResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const choose = async (f: File | undefined) => {
    if (!f) return
    setFile(f)
    setError(null)
    setBusy(true)
    try {
      const p = await biz.importPreview(entity, f)
      setPreview(p)
      setMapping(p.suggested_mapping)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const run = async () => {
    if (!file) return
    setBusy(true)
    setError(null)
    try {
      setResult(await biz.importApply(entity, mapping, file))
      onDone()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const missing = preview?.fields.filter((f) => f.required && !mapping[f.key]) ?? []

  return (
    <Modal title={`Nhập ${ENTITY_LABEL[entity]} từ CSV / Excel`} onClose={onClose}>
      <ErrorBanner message={error} onClose={() => setError(null)} />
      {!preview && (
        <div className="space-y-3">
          <p className="text-sm text-zinc-500">Chọn file .csv hoặc .xlsx có dòng đầu là tên cột. Bước sau bạn sẽ ghép cột với trường dữ liệu và xem trước.</p>
          <input type="file" accept=".csv,.xlsx,.xlsm,text/csv" data-testid="import-file" onChange={(e) => void choose(e.target.files?.[0])} disabled={busy} className="text-sm" />
          {busy && <p className="text-sm text-zinc-500">Đang đọc file…</p>}
        </div>
      )}
      {preview && !result && (
        <div className="max-h-[70vh] space-y-3 overflow-y-auto">
          <p className="text-sm text-zinc-600 dark:text-zinc-400">
            Phát hiện <b>{preview.row_count}</b> dòng, {preview.columns.length} cột. Ghép từng trường với cột trong file (dấu * là bắt buộc).
          </p>
          <div className="grid gap-2 sm:grid-cols-2" data-testid="mapping">
            {preview.fields.map((f) => (
              <label key={f.key} className="text-xs">
                <span className="font-medium">
                  {f.label}
                  {f.required ? ' *' : ''}
                </span>
                <select className={field} value={mapping[f.key] ?? ''} onChange={(e) => setMapping({ ...mapping, [f.key]: e.target.value })} aria-label={f.label}>
                  <option value="">— bỏ qua —</option>
                  {preview.columns.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              </label>
            ))}
          </div>
          <div className="overflow-x-auto rounded border border-zinc-200 dark:border-zinc-800">
            <table className="w-full text-xs" data-testid="import-preview">
              <thead className="bg-zinc-50 text-left dark:bg-zinc-800">
                <tr>
                  {preview.columns.map((c) => (
                    <th key={c} className="px-2 py-1 font-medium">
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {preview.sample.map((row, i) => (
                  <tr key={i} className="border-t border-zinc-100 dark:border-zinc-800">
                    {preview.columns.map((c) => (
                      <td key={c} className="max-w-40 truncate px-2 py-1">
                        {row[c]}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {missing.length > 0 && <p className="text-xs text-rose-600">Còn thiếu: {missing.map((m) => m.label).join(', ')}</p>}
          <div className="flex justify-end gap-2">
            <Button onClick={() => { setPreview(null); setFile(null) }}>Chọn file khác</Button>
            <Button variant="primary" disabled={busy || missing.length > 0} onClick={() => void run()}>
              {busy ? 'Đang nhập…' : `Nhập ${preview.row_count} dòng`}
            </Button>
          </div>
        </div>
      )}
      {result && (
        <div className="space-y-3" data-testid="import-result">
          <p className="text-sm">
            Đã tạo <b>{result.created}</b>, cập nhật <b>{result.updated}</b>, bỏ qua <b>{result.skipped}</b> dòng.
          </p>
          {result.errors.length > 0 && (
            <ul className="max-h-40 list-disc overflow-y-auto pl-5 text-xs text-rose-600">
              {result.errors.map((e, i) => (
                <li key={i}>{e}</li>
              ))}
            </ul>
          )}
          <div className="flex justify-end">
            <Button variant="primary" onClick={onClose}>
              Xong
            </Button>
          </div>
        </div>
      )}
    </Modal>
  )
}
