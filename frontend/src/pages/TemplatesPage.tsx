import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { Template } from '../api/types'
import { Button, ErrorBanner } from '../components/ui'
import { formatDate } from '../lib/format'
import { STEP_META } from '../lib/steps'

/** Built-in and personal templates. "Use template" creates a new flow from it. */
export function TemplatesPage() {
  const navigate = useNavigate()
  const [templates, setTemplates] = useState<Template[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setTemplates(await api.listTemplates())
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])
  useEffect(() => {
    void load()
  }, [load])

  const use = async (t: Template) => {
    setBusy(t.id)
    setError(null)
    try {
      const flow = await api.useTemplate(t.id)
      navigate(`/flows/${flow.id}`)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  const remove = async (t: Template) => {
    if (!window.confirm(`Delete template "${t.name}"?`)) return
    try {
      await api.deleteTemplate(t.id)
      await load()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const Card = ({ t }: { t: Template }) => (
    <li className="flex flex-col rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="flex items-start justify-between gap-2">
        <h2 className="font-semibold">{t.name}</h2>
        {!t.builtin && <span className="text-[11px] text-zinc-500">{formatDate(t.created_at)}</span>}
      </div>
      <p className="mt-1 text-sm text-zinc-500">{t.description || `${t.node_count} steps`}</p>
      {(Object.keys(t.skills ?? {}).length > 0 || (t.rules ?? []).length > 0) && (
        <p className="mt-1 text-xs text-zinc-500" data-testid="template-skills">
          {Object.keys(t.skills ?? {}).length > 0 && <>Skills: {[...new Set(Object.values(t.skills ?? {}).flat())].join(', ')}</>}
          {(t.rules ?? []).length > 0 && <> · {(t.rules ?? []).length} rule{(t.rules ?? []).length > 1 ? 's' : ''}</>}
        </p>
      )}
      {(t.input_schema ?? []).length > 0 && (
        <p className="mt-2 text-xs text-zinc-500" data-testid="template-inputs">
          Inputs: {t.input_schema.map((f) => `${f.name}${f.required ? '*' : ''}`).join(', ')}
        </p>
      )}
      <ol className="mt-3 flex flex-wrap gap-1">
        {t.nodes.map((n) => (
          <li key={n.id} className="flex items-center gap-1 rounded-full border border-zinc-200 px-2 py-0.5 text-xs dark:border-zinc-700">
            <span className={`h-2 w-2 rounded-full ${STEP_META[n.data.type]?.color ?? 'bg-zinc-400'}`} />
            {n.data.label || n.id}
          </li>
        ))}
      </ol>
      <div className="mt-4 flex justify-end gap-2">
        {!t.builtin && (
          <Button variant="ghost" className="text-rose-600" onClick={() => void remove(t)}>
            Delete
          </Button>
        )}
        <Button variant="primary" disabled={busy !== null} onClick={() => void use(t)}>
          {busy === t.id ? 'Creating…' : 'Use template'}
        </Button>
      </div>
    </li>
  )

  const builtin = templates?.filter((t) => t.builtin) ?? []
  const mine = templates?.filter((t) => !t.builtin) ?? []

  return (
    <div className="mx-auto max-w-5xl p-6">
      <h1 className="mb-1 text-2xl font-semibold">Templates</h1>
      <p className="mb-4 text-sm text-zinc-500">Start a new flow from a ready-made pipeline, or reuse one you saved from the editor (“Save as template”).</p>
      <ErrorBanner message={error} onClose={() => setError(null)} />
      {templates === null ? (
        <p className="text-sm text-zinc-500">Loading…</p>
      ) : (
        <>
          <h2 className="mb-2 text-sm font-semibold text-zinc-600 dark:text-zinc-400">Built-in</h2>
          <ul className="grid gap-3 sm:grid-cols-2">
            {builtin.map((t) => (
              <Card key={t.id} t={t} />
            ))}
          </ul>
          <h2 className="mb-2 mt-6 text-sm font-semibold text-zinc-600 dark:text-zinc-400">My templates</h2>
          {mine.length === 0 ? (
            <p className="rounded-md border border-dashed border-zinc-300 p-6 text-center text-sm text-zinc-500 dark:border-zinc-700">
              None yet. Open a flow in the editor and click “Save as template”.
            </p>
          ) : (
            <ul className="grid gap-3 sm:grid-cols-2">
              {mine.map((t) => (
                <Card key={t.id} t={t} />
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  )
}
