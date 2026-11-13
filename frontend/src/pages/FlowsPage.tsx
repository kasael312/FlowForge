import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { FlowSummary, Template } from '../api/types'
import { BLANK_FLOW } from '../lib/steps'
import { Button, ErrorBanner, Modal } from '../components/ui'
import { formatDate } from '../lib/format'

export function FlowsPage() {
  const navigate = useNavigate()
  const [flows, setFlows] = useState<FlowSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [showTemplates, setShowTemplates] = useState(false)
  const [templates, setTemplates] = useState<Template[] | null>(null)
  const [busy, setBusy] = useState(false)
  const importInput = useRef<HTMLInputElement>(null)
  const importFile = async (file: File | undefined) => {
    if (!file) return
    setBusy(true)
    try {
      const doc: unknown = JSON.parse(await file.text())
      const result = await api.importFlow(doc)
      navigate(`/flows/${result.flow.id}`, { state: { notice: `Imported "${result.flow.name}" from ${file.name}.${result.warnings.length ? `\nWarnings:\n- ${result.warnings.join('\n- ')}` : ''}` } })
    } catch (e) {
      setError(e instanceof SyntaxError ? 'The file is not valid JSON.' : (e as Error).message)
    } finally {
      setBusy(false)
      if (importInput.current) importInput.current.value = ''
    }
  }

  const load = useCallback(async () => {
    try {
      setFlows(await api.listFlows())
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const run = async (fn: () => Promise<void>) => {
    setBusy(true)
    setError(null)
    try {
      await fn()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const openTemplates = () => {
    setShowTemplates(true)
    if (templates === null) api.listTemplates().then(setTemplates).catch((e: Error) => setError(e.message))
  }

  const createBlank = () =>
    run(async () => {
      const flow = await api.createFlow({ ...BLANK_FLOW, nodes: [], edges: [] })
      setShowTemplates(false)
      navigate(`/flows/${flow.id}`)
    })

  const createFromTemplate = (id: string) =>
    run(async () => {
      const flow = await api.useTemplate(id)
      setShowTemplates(false)
      navigate(`/flows/${flow.id}`)
    })

  const duplicate = (id: string) =>
    run(async () => {
      await api.duplicateFlow(id)
      await load()
    })

  const remove = (flow: FlowSummary) => {
    if (!window.confirm(`Delete flow "${flow.name}" and all of its runs?`)) return
    void run(async () => {
      await api.deleteFlow(flow.id)
      await load()
    })
  }

  return (
    <div className="mx-auto max-w-5xl p-6">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Flows</h1>
        <div className="flex gap-2">
          <input ref={importInput} type="file" accept="application/json,.json" className="hidden" data-testid="import-flow" onChange={(e) => void importFile(e.target.files?.[0])} />
          <Button onClick={() => importInput.current?.click()} disabled={busy}>
            Import
          </Button>
          <Button variant="primary" onClick={openTemplates}>
            New flow
          </Button>
        </div>
      </div>
      <ErrorBanner message={error} onClose={() => setError(null)} />

      {flows === null ? (
        <p className="text-sm text-zinc-500">Loading…</p>
      ) : flows.length === 0 ? (
        <p className="rounded-md border border-dashed border-zinc-300 p-8 text-center text-sm text-zinc-500 dark:border-zinc-700">
          No flows yet. Create one from a template.
        </p>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase text-zinc-500">
            <tr>
              <th className="py-2">Name</th>
              <th>Steps</th>
              <th>Updated</th>
              <th className="text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {flows.map((f) => (
              <tr key={f.id} className="border-t border-zinc-200 dark:border-zinc-800">
                <td className="py-2">
                  <Link to={`/flows/${f.id}`} className="font-medium hover:underline">
                    {f.name}
                  </Link>
                  {f.description && <div className="text-xs text-zinc-500">{f.description}</div>}
                </td>
                <td>{f.node_count}</td>
                <td className="text-zinc-500">{formatDate(f.updated_at)}</td>
                <td className="space-x-1 text-right">
                  <Button variant="ghost" onClick={() => navigate(`/flows/${f.id}/run`)}>
                    Run
                  </Button>
                  <Button variant="ghost" disabled={busy} onClick={() => void duplicate(f.id)}>
                    Duplicate
                  </Button>
                  <Button variant="ghost" disabled={busy} onClick={() => remove(f)} className="text-rose-600">
                    Delete
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {showTemplates && (
        <Modal title="New flow" onClose={() => setShowTemplates(false)}>
          <ul className="max-h-[60vh] space-y-2 overflow-y-auto">
            <li>
              <button type="button" disabled={busy} onClick={() => void createBlank()} className="w-full rounded-md border border-zinc-200 p-3 text-left hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:hover:bg-zinc-800">
                <div className="font-medium">Blank</div>
                <div className="text-xs text-zinc-500">Start from an empty canvas.</div>
              </button>
            </li>
            {templates === null && <li className="px-1 text-xs text-zinc-500">Loading templates…</li>}
            {templates?.map((t) => (
              <li key={t.id}>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void createFromTemplate(t.id)}
                  className="w-full rounded-md border border-zinc-200 p-3 text-left hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:hover:bg-zinc-800"
                >
                  <div className="flex items-center gap-2 font-medium">
                    {t.name}
                    {!t.builtin && <span className="rounded bg-zinc-200 px-1.5 text-[10px] font-normal text-zinc-700 dark:bg-zinc-700 dark:text-zinc-200">mine</span>}
                  </div>
                  <div className="text-xs text-zinc-500">{t.description || `${t.node_count} steps`}</div>
                </button>
              </li>
            ))}
          </ul>
        </Modal>
      )}
    </div>
  )
}
