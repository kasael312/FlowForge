import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { api } from '../api/client'
import type { Rule, RuleInput, RuleScope } from '../api/types'
import { Button, ErrorBanner } from './ui'

const field = 'mt-1 w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900'
const label = 'block text-[11px] font-medium text-zinc-600 dark:text-zinc-400'

type Draft = { name: string; content: string; priority: string; step_id: string }
const EMPTY: Draft = { name: '', content: '', priority: '100', step_id: '' }

interface Props {
  /** Scope these rules are managed at. Flow/step scope needs a flow. */
  scope: Exclude<RuleScope, 'step'> | 'flow+step'
  flowId?: string
  /** Steps of the flow (for step-scoped rules). */
  steps?: { id: string; label: string }[]
  /** Preselect this step when adding. */
  initialStepId?: string | null
}

/** List + add/edit/toggle/delete rules of one scope (global page, or flow & step inside the editor). */
export function RulesEditor({ scope, flowId, steps = [], initialStepId = null }: Props) {
  const [rules, setRules] = useState<Rule[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<{ id: string | null; draft: Draft; scope: RuleScope } | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const rows = scope === 'global' ? await api.listRules({ scope: 'global' }) : await api.listRules({ flow_id: flowId })
      setRules(rows)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [scope, flowId])
  useEffect(() => {
    void load()
  }, [load])

  const startAdd = (s: RuleScope) => setEditing({ id: null, scope: s, draft: { ...EMPTY, step_id: initialStepId ?? steps[0]?.id ?? '' } })
  const startEdit = (r: Rule) => setEditing({ id: r.id, scope: r.scope, draft: { name: r.name, content: r.content, priority: String(r.priority), step_id: r.step_id ?? '' } })

  const save = async (e: FormEvent) => {
    e.preventDefault()
    if (!editing) return
    setBusy(true)
    const body: RuleInput = {
      name: editing.draft.name.trim(),
      content: editing.draft.content,
      scope: editing.scope,
      flow_id: editing.scope === 'global' ? null : flowId,
      step_id: editing.scope === 'step' ? editing.draft.step_id : null,
      priority: Number(editing.draft.priority) || 100,
      enabled: true,
    }
    try {
      if (editing.id) await api.updateRule(editing.id, { name: body.name, content: body.content, priority: body.priority, step_id: body.step_id ?? undefined })
      else await api.createRule(body)
      setEditing(null)
      await load()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const toggle = async (r: Rule) => {
    setRules((prev) => prev?.map((x) => (x.id === r.id ? { ...x, enabled: !r.enabled } : x)) ?? null) // optimistic
    try {
      const updated = await api.updateRule(r.id, { enabled: !r.enabled })
      setRules((prev) => prev?.map((x) => (x.id === r.id ? updated : x)) ?? null)
    } catch (err) {
      setRules((prev) => prev?.map((x) => (x.id === r.id ? r : x)) ?? null)
      setError((err as Error).message)
    }
  }

  const remove = async (r: Rule) => {
    if (!window.confirm(`Delete rule "${r.name}"?`)) return
    try {
      await api.deleteRule(r.id)
      setRules((prev) => prev?.filter((x) => x.id !== r.id) ?? null)
    } catch (err) {
      setError((err as Error).message)
    }
  }

  const stepLabel = (id: string | null) => steps.find((s) => s.id === id)?.label ?? id ?? ''

  const section = (title: string, s: RuleScope, hint: string) => {
    const rows = (rules ?? []).filter((r) => r.scope === s)
    return (
      <section className="mb-4" data-testid={`rules-${s}`}>
        <div className="flex items-center gap-2">
          <h3 className="mr-auto text-sm font-semibold">{title}</h3>
          <Button className="px-2 py-0.5 text-xs" onClick={() => startAdd(s)}>
            Add
          </Button>
        </div>
        <p className="text-[11px] text-zinc-500">{hint}</p>
        <ul className="mt-2 divide-y divide-zinc-100 rounded border border-zinc-200 dark:divide-zinc-800 dark:border-zinc-800">
          {rows.map((r) => (
            <li key={r.id} className={`flex items-start gap-2 px-2 py-1.5 text-xs ${r.enabled ? '' : 'opacity-60'}`}>
              <input type="checkbox" className="mt-0.5" checked={r.enabled} onChange={() => void toggle(r)} aria-label={`Enable ${r.name}`} />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{r.name}</span>
                  {r.key && <span className="rounded bg-zinc-100 px-1 text-[9px] text-zinc-500 dark:bg-zinc-800">built-in</span>}
                  {s === 'step' && <span className="font-mono text-[10px] text-zinc-500">step: {stepLabel(r.step_id)}</span>}
                  <span className="ml-auto text-[10px] text-zinc-400">priority {r.priority}</span>
                </div>
                <p className="line-clamp-2 whitespace-pre-wrap text-zinc-600 dark:text-zinc-400">{r.content}</p>
              </div>
              <button type="button" className="text-sky-700 hover:underline dark:text-sky-400" onClick={() => startEdit(r)}>
                Edit
              </button>
              <button type="button" className="text-rose-600 hover:underline" onClick={() => void remove(r)}>
                Delete
              </button>
            </li>
          ))}
          {rows.length === 0 && <li className="px-2 py-1.5 text-xs text-zinc-500">No rules.</li>}
        </ul>
      </section>
    )
  }

  return (
    <div>
      <ErrorBanner message={error} onClose={() => setError(null)} />
      {rules === null && <p className="text-sm text-zinc-500">Loading…</p>}
      {scope === 'global' && section('Global rules', 'global', 'Apply to every LLM step of every flow you own. Lower priority runs first.')}
      {scope === 'flow+step' && (
        <>
          {section('Flow rules', 'flow', 'Apply to every LLM step of this flow, after global rules.')}
          {section('Step rules', 'step', 'Apply to one step only, after flow rules.')}
        </>
      )}
      {editing && (
        <form onSubmit={(e) => void save(e)} className="mt-2 space-y-2 rounded border border-zinc-300 p-3 dark:border-zinc-700" data-testid="rule-form">
          <div className="grid grid-cols-[1fr_6rem] gap-2">
            <div>
              <label htmlFor="rule-name" className={label}>Name</label>
              <input id="rule-name" className={field} value={editing.draft.name} onChange={(e) => setEditing({ ...editing, draft: { ...editing.draft, name: e.target.value } })} required />
            </div>
            <div>
              <label htmlFor="rule-priority" className={label}>Priority</label>
              <input id="rule-priority" type="number" min={0} className={field} value={editing.draft.priority} onChange={(e) => setEditing({ ...editing, draft: { ...editing.draft, priority: e.target.value } })} />
            </div>
          </div>
          {editing.scope === 'step' && (
            <div>
              <label htmlFor="rule-step" className={label}>Step</label>
              <select id="rule-step" className={field} value={editing.draft.step_id} onChange={(e) => setEditing({ ...editing, draft: { ...editing.draft, step_id: e.target.value } })}>
                {steps.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.label} ({s.id})
                  </option>
                ))}
              </select>
            </div>
          )}
          <div>
            <label htmlFor="rule-content" className={label}>Rule text</label>
            <textarea id="rule-content" className={`${field} h-24`} value={editing.draft.content} onChange={(e) => setEditing({ ...editing, draft: { ...editing.draft, content: e.target.value } })} />
          </div>
          <div className="flex justify-end gap-2">
            <Button onClick={() => setEditing(null)}>Cancel</Button>
            <Button type="submit" variant="primary" disabled={busy || !editing.draft.name.trim()}>
              {editing.id ? 'Save rule' : 'Add rule'}
            </Button>
          </div>
        </form>
      )}
    </div>
  )
}
