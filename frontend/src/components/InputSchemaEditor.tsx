import { useState } from 'react'
import { INPUT_TYPES, type InputField, type InputType } from '../api/types'
import { Button } from './ui'

const field = 'mt-1 w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900'
const label = 'block text-[11px] font-medium text-zinc-600 dark:text-zinc-400'

export function newField(existing: InputField[]): InputField {
  let i = existing.length + 1
  while (existing.some((f) => f.name === `field${i}`)) i += 1
  return { name: `field${i}`, label: '', type: 'text', required: false, default: null, options: [], placeholder: '', description: '' }
}

/** Form builder for a flow's input schema. */
export function InputSchemaEditor({ schema, onChange }: { schema: InputField[]; onChange: (s: InputField[]) => void }) {
  // Raw text of the options box while typing (commas would otherwise be normalised away).
  const [optionsText, setOptionsText] = useState<Record<string, string>>({})
  const update = (i: number, patch: Partial<InputField>) => onChange(schema.map((f, j) => (j === i ? { ...f, ...patch } : f)))
  const remove = (i: number) => onChange(schema.filter((_, j) => j !== i))
  const move = (i: number, dir: -1 | 1) => {
    const j = i + dir
    if (j < 0 || j >= schema.length) return
    const copy = schema.slice()
    ;[copy[i], copy[j]] = [copy[j]!, copy[i]!]
    onChange(copy)
  }
  const names = schema.map((f) => f.name)
  const dupes = names.filter((n, i) => names.indexOf(n) !== i)

  return (
    <div className="space-y-2" data-testid="schema-editor">
      <p className="text-xs text-zinc-500">
        Fields appear on the Run page and are available to prompts as <code>{'{{input.<name>}}'}</code>.
      </p>
      {schema.length === 0 && <p className="rounded border border-dashed border-zinc-300 p-3 text-center text-xs text-zinc-500 dark:border-zinc-700">No inputs yet.</p>}
      {schema.map((f, i) => (
        <div key={i} className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-800" data-testid={`field-${i}`}>
          <div className="grid gap-2 sm:grid-cols-3">
            <div>
              <label htmlFor={`f-name-${i}`} className={label}>Name</label>
              <input id={`f-name-${i}`} className={`${field} font-mono`} value={f.name} onChange={(e) => update(i, { name: e.target.value.replace(/[^a-zA-Z0-9_]/g, '_') })} />
            </div>
            <div>
              <label htmlFor={`f-label-${i}`} className={label}>Label</label>
              <input id={`f-label-${i}`} className={field} value={f.label} onChange={(e) => update(i, { label: e.target.value })} />
            </div>
            <div>
              <label htmlFor={`f-type-${i}`} className={label}>Type</label>
              <select id={`f-type-${i}`} className={field} value={f.type} onChange={(e) => update(i, { type: e.target.value as InputType, default: null })}>
                {INPUT_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="mt-2 grid gap-2 sm:grid-cols-3">
            {f.type === 'select' ? (
              <div className="sm:col-span-2">
                <label htmlFor={`f-options-${i}`} className={label}>Options (comma separated)</label>
                <input
                  id={`f-options-${i}`}
                  className={field}
                  value={optionsText[f.name] ?? f.options.join(', ')}
                  onChange={(e) => {
                    setOptionsText({ ...optionsText, [f.name]: e.target.value })
                    update(i, { options: e.target.value.split(',').map((s) => s.trim()).filter(Boolean) })
                  }}
                  onBlur={() => setOptionsText(({ [f.name]: _drop, ...rest }) => rest)}
                />
              </div>
            ) : f.type !== 'file' && f.type !== 'boolean' ? (
              <div>
                <label htmlFor={`f-default-${i}`} className={label}>Default</label>
                <input id={`f-default-${i}`} className={field} value={f.default === null ? '' : String(f.default)} onChange={(e) => update(i, { default: e.target.value === '' ? null : f.type === 'number' ? Number(e.target.value) : e.target.value })} />
              </div>
            ) : (
              <div />
            )}
            {f.type !== 'file' && f.type !== 'boolean' && f.type !== 'select' && (
              <div>
                <label htmlFor={`f-ph-${i}`} className={label}>Placeholder</label>
                <input id={`f-ph-${i}`} className={field} value={f.placeholder} onChange={(e) => update(i, { placeholder: e.target.value })} />
              </div>
            )}
            <div>
              <label htmlFor={`f-desc-${i}`} className={label}>Help text</label>
              <input id={`f-desc-${i}`} className={field} value={f.description} onChange={(e) => update(i, { description: e.target.value })} />
            </div>
          </div>
          <div className="mt-2 flex items-center gap-3 text-xs">
            <label className="flex items-center gap-1">
              <input type="checkbox" checked={f.required} onChange={(e) => update(i, { required: e.target.checked })} /> required
            </label>
            {dupes.includes(f.name) && <span className="text-rose-600">duplicate name</span>}
            <span className="ml-auto flex gap-1">
              <Button variant="ghost" className="px-2 py-0.5 text-xs" onClick={() => move(i, -1)} disabled={i === 0} aria-label="Move up">↑</Button>
              <Button variant="ghost" className="px-2 py-0.5 text-xs" onClick={() => move(i, 1)} disabled={i === schema.length - 1} aria-label="Move down">↓</Button>
              <Button variant="ghost" className="px-2 py-0.5 text-xs text-rose-600" onClick={() => remove(i)}>Remove</Button>
            </span>
          </div>
        </div>
      ))}
      <Button onClick={() => onChange([...schema, newField(schema)])}>Add field</Button>
    </div>
  )
}
