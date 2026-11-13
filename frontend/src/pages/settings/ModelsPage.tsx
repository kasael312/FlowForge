import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { api } from '../../api/client'
import { MODEL_PROVIDERS, type ApiKey, type Effort, type KeyTestResult, type LlmModel, type LlmModelInput, type ModelProvider } from '../../api/types'
import { Button, ErrorBanner, Modal } from '../../components/ui'
import { formatDate, formatUsd } from '../../lib/format'
import { KEY_PROVIDER_FOR, MODEL_SUGGESTIONS, PROVIDER_HINT, PROVIDER_LABEL } from '../../lib/models'

const field = 'mt-1 w-full rounded border border-zinc-300 bg-white px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-900'
const label = 'block text-xs font-medium text-zinc-600 dark:text-zinc-400'

type Draft = {
  provider: ModelProvider
  model_name: string
  label: string
  base_url: string
  api_key_id: string
  temperature: string
  max_tokens: string
  effort: string
  is_default: boolean
}

const EMPTY: Draft = { provider: 'anthropic', model_name: '', label: '', base_url: '', api_key_id: '', temperature: '', max_tokens: '', effort: '', is_default: false }

function toDraft(m: LlmModel): Draft {
  return {
    provider: m.provider,
    model_name: m.model_name,
    label: m.label,
    base_url: m.base_url ?? '',
    api_key_id: m.api_key_id ?? '',
    temperature: m.params.temperature != null ? String(m.params.temperature) : '',
    max_tokens: m.params.max_tokens != null ? String(m.params.max_tokens) : '',
    effort: m.params.effort ?? '',
    is_default: m.is_default,
  }
}

function toInput(d: Draft): LlmModelInput {
  return {
    provider: d.provider,
    model_name: d.model_name.trim(),
    label: d.label.trim(),
    base_url: d.base_url.trim() || null,
    api_key_id: d.api_key_id || null,
    params: {
      temperature: d.temperature.trim() ? Number(d.temperature) : null,
      max_tokens: d.max_tokens.trim() ? Number(d.max_tokens) : null,
      effort: (d.effort || null) as Effort | null,
    },
    is_default: d.is_default,
  }
}

/** Settings → Models: configure which LLMs steps can use. */
export function ModelsPage() {
  const [models, setModels] = useState<LlmModel[] | null>(null)
  const [keys, setKeys] = useState<ApiKey[]>([])
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<{ id: string | null; draft: Draft } | null>(null)
  const [busy, setBusy] = useState(false)
  const [tests, setTests] = useState<Record<string, KeyTestResult | 'running'>>({})

  const load = useCallback(async () => {
    try {
      const [m, k] = await Promise.all([api.listModels(), api.listKeys()])
      setModels(m)
      setKeys(k)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])
  useEffect(() => {
    void load()
  }, [load])

  const save = async (e: FormEvent) => {
    e.preventDefault()
    if (!editing) return
    setBusy(true)
    setError(null)
    try {
      if (editing.id) await api.updateModel(editing.id, toInput(editing.draft))
      else await api.createModel(toInput(editing.draft))
      setEditing(null)
      await load()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const remove = async (m: LlmModel) => {
    if (!window.confirm(`Delete model "${m.label}"? Steps using it will fall back to your default model.`)) return
    try {
      await api.deleteModel(m.id)
      await load()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  const makeDefault = async (m: LlmModel) => {
    try {
      await api.setDefaultModel(m.id)
      await load()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  const test = async (m: LlmModel) => {
    setTests((t) => ({ ...t, [m.id]: 'running' }))
    try {
      setTests((t) => ({ ...t, [m.id]: { ok: false, message: '' } }))
      const r = await api.testModel(m.id)
      setTests((t) => ({ ...t, [m.id]: r }))
    } catch (err) {
      setTests((t) => ({ ...t, [m.id]: { ok: false, message: (err as Error).message } }))
    }
  }

  const d = editing?.draft
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => editing && setEditing({ ...editing, draft: { ...editing.draft, [k]: v } })
  const keyProvider = d ? KEY_PROVIDER_FOR[d.provider] : null
  const usableKeys = keys.filter((k) => k.provider === keyProvider)

  return (
    <div className="mx-auto max-w-5xl p-6">
      <div className="mb-1 flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Models</h1>
        <Button variant="primary" onClick={() => setEditing({ id: null, draft: { ...EMPTY, is_default: (models?.length ?? 0) === 0 } })}>
          Add model
        </Button>
      </div>
      <p className="mb-4 text-sm text-zinc-500">
        Each step can pick a model; steps without one use your default. Without any model, FlowForge uses Anthropic with your stored key.
      </p>
      <ErrorBanner message={error} onClose={() => setError(null)} />

      {models === null ? (
        <p className="text-sm text-zinc-500">Loading…</p>
      ) : models.length === 0 ? (
        <p className="rounded-md border border-dashed border-zinc-300 p-6 text-center text-sm text-zinc-500 dark:border-zinc-700">No models configured yet.</p>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase text-zinc-500">
            <tr>
              <th className="py-2">Label</th>
              <th>Provider</th>
              <th>Model</th>
              <th>Key / URL</th>
              <th>Price /1M</th>
              <th className="text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {models.map((m) => {
              const t = tests[m.id]
              return (
                <tr key={m.id} className="border-t border-zinc-200 align-top dark:border-zinc-800">
                  <td className="py-2 font-medium">
                    {m.label}
                    {m.is_default && <span className="ml-2 rounded bg-emerald-100 px-1.5 text-[10px] font-semibold text-emerald-800 dark:bg-emerald-900 dark:text-emerald-200">default</span>}
                    <div className="text-[11px] font-normal text-zinc-500">{formatDate(m.created_at)}</div>
                  </td>
                  <td>{PROVIDER_LABEL[m.provider].split(' (')[0]}</td>
                  <td className="font-mono text-xs">
                    {m.model_name}
                    {Object.keys(m.params).length > 0 && (
                      <div className="text-[10px] text-zinc-500">
                        {Object.entries(m.params)
                          .map(([k, v]) => `${k}=${String(v)}`)
                          .join(' ')}
                      </div>
                    )}
                  </td>
                  <td className="font-mono text-xs text-zinc-500">
                    {m.api_key_last4 ? `••••${m.api_key_last4}` : m.provider === 'ollama' ? 'no key' : 'stored / server key'}
                    {m.base_url && <div className="truncate" title={m.base_url}>{m.base_url}</div>}
                  </td>
                  <td className="text-xs text-zinc-500">{m.price ? `${formatUsd(m.price.input)} in · ${formatUsd(m.price.output)} out` : 'unknown'}</td>
                  <td className="space-x-1 whitespace-nowrap text-right">
                    {t && t !== 'running' && t.message && <span className={`mr-1 text-xs ${t.ok ? 'text-emerald-600' : 'text-rose-600'}`}>{t.message}</span>}
                    <Button variant="ghost" disabled={t === 'running'} onClick={() => void test(m)}>
                      {t === 'running' ? 'Testing…' : 'Test'}
                    </Button>
                    {!m.is_default && (
                      <Button variant="ghost" onClick={() => void makeDefault(m)}>
                        Set default
                      </Button>
                    )}
                    <Button variant="ghost" onClick={() => setEditing({ id: m.id, draft: toDraft(m) })}>
                      Edit
                    </Button>
                    <Button variant="ghost" className="text-rose-600" onClick={() => void remove(m)}>
                      Delete
                    </Button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}

      {editing && d && (
        <Modal title={editing.id ? 'Edit model' : 'Add model'} onClose={() => setEditing(null)}>
          <form onSubmit={(e) => void save(e)} className="space-y-3">
            <div>
              <label htmlFor="m-provider" className={label}>Provider</label>
              <select id="m-provider" className={field} value={d.provider} disabled={!!editing.id} onChange={(e) => set('provider', e.target.value as ModelProvider)}>
                {MODEL_PROVIDERS.map((p) => (
                  <option key={p} value={p}>
                    {PROVIDER_LABEL[p]}
                  </option>
                ))}
              </select>
              <p className="mt-1 text-[11px] text-zinc-500">{PROVIDER_HINT[d.provider]}</p>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <label htmlFor="m-label" className={label}>Label</label>
                <input id="m-label" required className={field} placeholder="e.g. Fast & cheap" value={d.label} onChange={(e) => set('label', e.target.value)} />
              </div>
              <div>
                <label htmlFor="m-name" className={label}>Model name</label>
                <input id="m-name" required list="model-suggestions" className={`${field} font-mono`} value={d.model_name} onChange={(e) => set('model_name', e.target.value)} />
                <datalist id="model-suggestions">
                  {MODEL_SUGGESTIONS[d.provider].map((s) => (
                    <option key={s} value={s} />
                  ))}
                </datalist>
              </div>
            </div>
            {(d.provider === 'ollama' || d.provider === 'openai_compatible') && (
              <div>
                <label htmlFor="m-url" className={label}>Base URL{d.provider === 'ollama' ? ' (optional, default http://localhost:11434)' : ''}</label>
                <input id="m-url" required={d.provider === 'openai_compatible'} className={`${field} font-mono`} placeholder={d.provider === 'ollama' ? 'http://localhost:11434' : 'https://api.groq.com/openai/v1'} value={d.base_url} onChange={(e) => set('base_url', e.target.value)} />
              </div>
            )}
            {keyProvider && (
              <div>
                <label htmlFor="m-key" className={label}>API key</label>
                <select id="m-key" className={field} value={d.api_key_id} onChange={(e) => set('api_key_id', e.target.value)}>
                  <option value="">{d.provider === 'openai_compatible' ? 'None (host without auth)' : 'Use my newest stored key / server key'}</option>
                  {usableKeys.map((k) => (
                    <option key={k.id} value={k.id}>
                      {k.label || k.provider} ••••{k.last4}
                    </option>
                  ))}
                </select>
                {usableKeys.length === 0 && <p className="mt-1 text-[11px] text-zinc-500">No stored key of this kind yet; add one under API Keys.</p>}
              </div>
            )}
            <div className="grid gap-3 sm:grid-cols-3">
              {d.provider !== 'anthropic' && (
                <div>
                  <label htmlFor="m-temp" className={label}>Temperature</label>
                  <input id="m-temp" type="number" step="0.1" min={0} max={2} className={field} value={d.temperature} onChange={(e) => set('temperature', e.target.value)} />
                </div>
              )}
              <div>
                <label htmlFor="m-max" className={label}>Max tokens</label>
                <input id="m-max" type="number" min={1} className={field} placeholder="16000" value={d.max_tokens} onChange={(e) => set('max_tokens', e.target.value)} />
              </div>
              {d.provider === 'anthropic' && (
                <div>
                  <label htmlFor="m-effort" className={label}>Effort</label>
                  <select id="m-effort" className={field} value={d.effort} onChange={(e) => set('effort', e.target.value)}>
                    <option value="">model default</option>
                    {['low', 'medium', 'high', 'xhigh', 'max'].map((x) => (
                      <option key={x} value={x}>
                        {x}
                      </option>
                    ))}
                  </select>
                </div>
              )}
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={d.is_default} onChange={(e) => set('is_default', e.target.checked)} /> Use as default model
            </label>
            <div className="flex justify-end gap-2">
              <Button onClick={() => setEditing(null)}>Cancel</Button>
              <Button type="submit" variant="primary" disabled={busy || !d.label.trim() || !d.model_name.trim()}>
                {busy ? 'Saving…' : editing.id ? 'Save changes' : 'Add model'}
              </Button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  )
}
