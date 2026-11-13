import { useState } from 'react'
import { api } from '../api/client'
import type { InputField } from '../api/types'

const field = 'mt-1 w-full rounded border border-zinc-300 bg-white px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-900'
const label = 'block text-xs font-medium text-zinc-600 dark:text-zinc-400'

export type InputValues = Record<string, unknown>

/** Initial values from field defaults. */
export function defaultValues(schema: InputField[]): InputValues {
  const out: InputValues = {}
  for (const f of schema) {
    if (f.default !== null && f.default !== undefined && f.default !== '') out[f.name] = f.default
    else if (f.type === 'boolean') out[f.name] = false
  }
  return out
}

/** Client-side check mirroring the backend; returns the first problem or null. */
export function validateValues(schema: InputField[], values: InputValues): string | null {
  for (const f of schema) {
    const v = values[f.name]
    const empty = v === undefined || v === null || v === '' || (f.type === 'file' && !(v as { upload_id?: string })?.upload_id)
    if (f.required && empty) return `${f.label || f.name} is required`
    if (empty) continue
    if (f.type === 'number' && Number.isNaN(Number(v))) return `${f.label || f.name} must be a number`
    if (f.type === 'url' && !/^https?:\/\//.test(String(v))) return `${f.label || f.name} must be a URL`
  }
  return null
}

interface Props {
  schema: InputField[]
  values: InputValues
  onChange: (values: InputValues) => void
}

/** Renders the flow's input schema as a form. */
export function InputForm({ schema, values, onChange }: Props) {
  const [uploading, setUploading] = useState<string | null>(null)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const set = (name: string, v: unknown) => onChange({ ...values, [name]: v })

  const upload = async (f: InputField, file: File) => {
    setUploading(f.name)
    setUploadError(null)
    try {
      const up = await api.uploadFile(file)
      set(f.name, { upload_id: up.id, name: up.name, size: up.size })
    } catch (e) {
      setUploadError((e as Error).message)
    } finally {
      setUploading(null)
    }
  }

  if (schema.length === 0) return null
  return (
    <div className="space-y-3" data-testid="input-form">
      {schema.map((f) => {
        const id = `in-${f.name}`
        const v = values[f.name]
        const lbl = (
          <label htmlFor={id} className={label}>
            {f.label || f.name}
            {f.required && <span className="text-rose-600"> *</span>}
          </label>
        )
        return (
          <div key={f.name}>
            {f.type !== 'boolean' && lbl}
            {f.type === 'text' && <input id={id} className={field} placeholder={f.placeholder} value={(v as string) ?? ''} onChange={(e) => set(f.name, e.target.value)} />}
            {f.type === 'url' && <input id={id} type="url" className={`${field} font-mono`} placeholder={f.placeholder || 'https://'} value={(v as string) ?? ''} onChange={(e) => set(f.name, e.target.value)} />}
            {f.type === 'textarea' && <textarea id={id} className={`${field} h-24`} placeholder={f.placeholder} value={(v as string) ?? ''} onChange={(e) => set(f.name, e.target.value)} />}
            {f.type === 'number' && <input id={id} type="number" className={field} placeholder={f.placeholder} value={v === undefined || v === null ? '' : String(v)} onChange={(e) => set(f.name, e.target.value === '' ? '' : Number(e.target.value))} />}
            {f.type === 'select' && (
              <select id={id} className={field} value={(v as string) ?? ''} onChange={(e) => set(f.name, e.target.value)}>
                <option value="">— choose —</option>
                {f.options.map((o) => (
                  <option key={o} value={o}>
                    {o}
                  </option>
                ))}
              </select>
            )}
            {f.type === 'boolean' && (
              <label className="flex items-center gap-2 text-sm">
                <input id={id} type="checkbox" checked={Boolean(v)} onChange={(e) => set(f.name, e.target.checked)} /> {f.label || f.name}
              </label>
            )}
            {f.type === 'file' && (
              <div className="mt-1 flex items-center gap-2 text-sm">
                <input
                  id={id}
                  type="file"
                  className="text-xs"
                  onChange={(e) => {
                    const file = e.target.files?.[0]
                    if (file) void upload(f, file)
                  }}
                />
                {uploading === f.name && <span className="text-xs text-zinc-500">uploading…</span>}
                {(v as { name?: string })?.name && <span className="font-mono text-xs text-emerald-700">✔ {(v as { name: string }).name}</span>}
              </div>
            )}
            {f.description && <p className="mt-0.5 text-[11px] text-zinc-500">{f.description}</p>}
          </div>
        )
      })}
      {uploadError && <p className="text-xs text-rose-600">{uploadError}</p>}
    </div>
  )
}
