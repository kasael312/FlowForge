import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { api } from '../api/client'
import type { Skill, SkillInput, SkillKind } from '../api/types'
import { Button, ErrorBanner } from '../components/ui'
import { formatDate } from '../lib/format'
import { renderMarkdown } from '../lib/markdown'

const field = 'mt-1 w-full rounded border border-zinc-300 bg-white px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-900'
const label = 'block text-xs font-medium text-zinc-600 dark:text-zinc-400'

type Draft = { name: string; kind: SkillKind; description: string; tools: string; content: string }
const EMPTY: Draft = { name: '', kind: 'skill', description: '', tools: '', content: '# My skill\n\n1. First rule\n2. Second rule\n' }
const EMPTY_PERSONA: Draft = { name: '', kind: 'persona', description: '', tools: '', content: '# My persona\n\nYou are a …\n\n- Tone: …\n- Always: …\n- Never: …\n' }

function toDraft(s: Skill): Draft {
  return { name: s.name, kind: s.kind, description: s.description, tools: s.tools_required.join(', '), content: s.content }
}
function toInput(d: Draft): SkillInput {
  return {
    name: d.name.trim().toLowerCase(),
    kind: d.kind,
    description: d.description.trim(),
    content: d.content,
    tools_required: d.tools
      .split(',')
      .map((t) => t.trim())
      .filter(Boolean),
  }
}

function formatSize(n: number): string {
  return n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`
}

/** Skills page: built-in and personal skills, markdown editor with preview, files, zip import/export. */
export function SkillsPage() {
  const [skills, setSkills] = useState<Skill[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [creating, setCreating] = useState(false)
  const [busy, setBusy] = useState(false)
  const [view, setView] = useState<'edit' | 'preview' | 'split'>('split')
  const importRef = useRef<HTMLInputElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const load = useCallback(async () => {
    try {
      setSkills(await api.listSkills())
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])
  useEffect(() => {
    void load()
  }, [load])

  const selected = skills?.find((s) => s.id === selectedId) ?? null
  const editable = Boolean(selected && !selected.builtin) || creating

  const open = (s: Skill) => {
    setCreating(false)
    setSelectedId(s.id)
    setDraft(toDraft(s))
  }
  const startCreate = (kind: SkillKind = 'skill') => {
    setCreating(true)
    setSelectedId(null)
    setDraft({ ...(kind === 'persona' ? EMPTY_PERSONA : EMPTY) })
  }

  const save = async (e: FormEvent) => {
    e.preventDefault()
    if (!draft) return
    setBusy(true)
    try {
      const saved = creating ? await api.createSkill(toInput(draft)) : await api.updateSkill(selectedId!, toInput(draft))
      await load()
      setCreating(false)
      setSelectedId(saved.id)
      setDraft(toDraft(saved))
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const remove = async (s: Skill) => {
    if (!window.confirm(`Delete skill "${s.name}"?`)) return
    try {
      await api.deleteSkill(s.id)
      if (selectedId === s.id) {
        setSelectedId(null)
        setDraft(null)
      }
      await load()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  const duplicate = async (s: Skill) => {
    try {
      const copy = await api.duplicateSkill(s.id)
      await load()
      open(copy)
    } catch (err) {
      setError((err as Error).message)
    }
  }

  const importZip = async (file: File | undefined) => {
    if (!file) return
    setBusy(true)
    try {
      const s = await api.importSkill(file)
      await load()
      open(s)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
      if (importRef.current) importRef.current.value = ''
    }
  }

  const uploadFile = async (file: File | undefined) => {
    if (!file || !selected) return
    setBusy(true)
    try {
      const path = window.prompt('Path inside the skill', file.name) ?? file.name
      const s = await api.uploadSkillFile(selected.id, file, path)
      setSkills((prev) => prev?.map((x) => (x.id === s.id ? s : x)) ?? null)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const removeFile = async (path: string) => {
    if (!selected) return
    try {
      const s = await api.deleteSkillFile(selected.id, path)
      setSkills((prev) => prev?.map((x) => (x.id === s.id ? s : x)) ?? null)
    } catch (err) {
      setError((err as Error).message)
    }
  }

  const builtin = skills?.filter((s) => s.builtin) ?? []
  const mine = skills?.filter((s) => !s.builtin) ?? []

  const group = (title: string, items: Skill[]) => (
    <>
      <h2 className="px-2 pt-1 text-[11px] font-semibold uppercase tracking-wide text-zinc-500">{title}</h2>
      <ul className="mb-2">
        {items.map((s) => row(s))}
        {items.length === 0 && <li className="px-2 py-1 text-xs text-zinc-500">None yet.</li>}
      </ul>
    </>
  )

  const row = (s: Skill) => (
    <li key={s.id}>
      <button
        type="button"
        onClick={() => open(s)}
        className={`flex w-full flex-col rounded px-2 py-1.5 text-left hover:bg-zinc-100 dark:hover:bg-zinc-800 ${selectedId === s.id ? 'bg-zinc-100 dark:bg-zinc-800' : ''}`}
      >
        <span className="font-mono text-sm">{s.name}</span>
        <span className="line-clamp-1 text-[11px] text-zinc-500">{s.description || `${s.files.length} files`}</span>
      </button>
    </li>
  )

  return (
    <div className="mx-auto max-w-6xl p-6">
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="mr-auto">
          <h1 className="text-2xl font-semibold">Skills</h1>
          <p className="text-sm text-zinc-500">
            A <b>persona</b> says who the agent is (role, tone, standards); a <b>skill</b> says how to do a task. Both are a <code>SKILL.md</code> plus reference files. Attach skills to LLM steps in the editor: the text is added to the step's system prompt and the files are
            mounted at <code>/skills/&lt;name&gt;/</code> in the sandbox.
          </p>
        </div>
        <input ref={importRef} type="file" accept=".zip,application/zip" className="hidden" data-testid="import-zip" onChange={(e) => void importZip(e.target.files?.[0])} />
        <Button onClick={() => importRef.current?.click()} disabled={busy}>
          Import zip
        </Button>
        <Button onClick={() => startCreate('persona')}>New persona</Button>
        <Button variant="primary" onClick={() => startCreate('skill')}>
          New skill
        </Button>
      </div>
      <ErrorBanner message={error} onClose={() => setError(null)} />
      <div className="grid gap-4 md:grid-cols-[16rem_1fr]">
        <aside className="rounded-lg border border-zinc-200 p-2 dark:border-zinc-800">
          {skills === null && <p className="p-2 text-sm text-zinc-500">Loading…</p>}
          {skills && (
            <>
              {group('Built-in personas', builtin.filter((s) => s.kind === 'persona'))}
              {group('Built-in skills', builtin.filter((s) => s.kind !== 'persona'))}
              {group('My personas', mine.filter((s) => s.kind === 'persona'))}
              {group('My skills', mine.filter((s) => s.kind !== 'persona'))}
            </>
          )}
        </aside>

        <section className="min-w-0 rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
          {!draft && <p className="text-sm text-zinc-500">Select a skill or create a new one.</p>}
          {draft && (
            <form onSubmit={(e) => void save(e)} className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="mr-auto font-semibold">
                  {creating ? (draft.kind === 'persona' ? 'New persona' : 'New skill') : selected?.name}
                  {selected && <span className="ml-2 rounded bg-violet-100 px-1.5 py-0.5 text-[10px] font-normal text-violet-800 dark:bg-violet-900 dark:text-violet-100">{selected.kind}</span>}
                  {selected?.builtin && <span className="ml-2 rounded bg-zinc-200 px-1.5 py-0.5 text-[10px] font-normal text-zinc-700 dark:bg-zinc-700 dark:text-zinc-200">built-in · read-only</span>}
                </h2>
                {selected && (
                  <>
                    <a className="text-xs text-sky-700 underline dark:text-sky-400" href={api.skillExportUrl(selected.id)}>
                      Export zip
                    </a>
                    <Button onClick={() => void duplicate(selected)}>Duplicate</Button>
                    {!selected.builtin && (
                      <Button variant="ghost" className="text-rose-600" onClick={() => void remove(selected)}>
                        Delete
                      </Button>
                    )}
                  </>
                )}
                {editable && (
                  <Button type="submit" variant="primary" disabled={busy || !draft.name.trim()}>
                    {busy ? 'Saving…' : creating ? 'Create' : 'Save'}
                  </Button>
                )}
              </div>
              <div className="grid gap-2 sm:grid-cols-4">
                <div>
                  <label htmlFor="skill-kind" className={label}>Kind</label>
                  <select id="skill-kind" className={field} value={draft.kind} disabled={!editable} onChange={(e) => setDraft({ ...draft, kind: e.target.value as SkillKind })}>
                    <option value="skill">skill — how to do a task</option>
                    <option value="persona">persona — who the agent is</option>
                  </select>
                </div>
                <div>
                  <label htmlFor="skill-name" className={label}>Name (a-z, 0-9, -)</label>
                  <input id="skill-name" className={`${field} font-mono`} value={draft.name} disabled={!editable} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
                </div>
                <div className="sm:col-span-2">
                  <label htmlFor="skill-description" className={label}>Description</label>
                  <input id="skill-description" className={field} value={draft.description} disabled={!editable} onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
                </div>
                <div className="sm:col-span-4">
                  <label htmlFor="skill-tools" className={label}>Tools required (comma separated, informational)</label>
                  <input id="skill-tools" className={`${field} font-mono`} value={draft.tools} disabled={!editable} onChange={(e) => setDraft({ ...draft, tools: e.target.value })} />
                </div>
              </div>
              <div>
                <div className="flex items-center gap-1">
                  <span className={`${label} mr-auto`}>SKILL.md</span>
                  {(['edit', 'split', 'preview'] as const).map((v) => (
                    <button key={v} type="button" onClick={() => setView(v)} className={`rounded px-2 py-0.5 text-[11px] capitalize ${view === v ? 'bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900' : 'text-zinc-500'}`}>
                      {v}
                    </button>
                  ))}
                </div>
                <div className={`mt-1 grid gap-2 ${view === 'split' ? 'md:grid-cols-2' : ''}`}>
                  {view !== 'preview' && (
                    <textarea id="skill-content" aria-label="SKILL.md content" className={`${field} h-96 font-mono text-xs`} value={draft.content} disabled={!editable} onChange={(e) => setDraft({ ...draft, content: e.target.value })} />
                  )}
                  {view !== 'edit' && (
                    <div className="prose-sm h-96 overflow-auto rounded border border-zinc-200 p-3 text-sm dark:border-zinc-800 [&_code]:rounded [&_code]:bg-zinc-100 [&_code]:px-1 [&_code]:text-xs dark:[&_code]:bg-zinc-800 [&_h1]:text-lg [&_h1]:font-semibold [&_h2]:mt-2 [&_h2]:font-semibold [&_li]:ml-4 [&_ol]:list-decimal [&_p]:my-1 [&_pre]:my-2 [&_pre]:overflow-auto [&_pre]:bg-zinc-100 [&_pre]:p-2 dark:[&_pre]:bg-zinc-800 [&_ul]:list-disc" data-testid="skill-preview" dangerouslySetInnerHTML={{ __html: renderMarkdown(draft.content) }} />
                  )}
                </div>
              </div>
              {selected && (
                <div>
                  <div className="flex items-center gap-2">
                    <span className={`${label} mr-auto`}>Reference files ({selected.files.length})</span>
                    {!selected.builtin && (
                      <>
                        <input ref={fileRef} type="file" className="hidden" data-testid="upload-file" onChange={(e) => void uploadFile(e.target.files?.[0])} />
                        <Button onClick={() => fileRef.current?.click()} disabled={busy}>
                          Add file
                        </Button>
                      </>
                    )}
                  </div>
                  <ul className="mt-1 divide-y divide-zinc-100 rounded border border-zinc-200 text-xs dark:divide-zinc-800 dark:border-zinc-800" data-testid="skill-files">
                    {selected.files.map((f) => (
                      <li key={f.path} className="flex items-center gap-2 px-2 py-1">
                        <a className="mr-auto font-mono text-sky-700 underline dark:text-sky-400" href={api.skillFileUrl(selected.id, f.path)} target="_blank" rel="noreferrer">
                          {f.path}
                        </a>
                        <span className="text-zinc-500">{formatSize(f.size)}</span>
                        {!selected.builtin && (
                          <button type="button" className="text-rose-600" onClick={() => void removeFile(f.path)} aria-label={`Remove ${f.path}`}>
                            ✕
                          </button>
                        )}
                      </li>
                    ))}
                    {selected.files.length === 0 && <li className="px-2 py-1 text-zinc-500">No files.</li>}
                  </ul>
                  <p className="mt-1 text-[10px] text-zinc-500">Updated {formatDate(selected.updated_at)}. Agents read files at /skills/{selected.name}/…</p>
                </div>
              )}
            </form>
          )}
        </section>
      </div>
    </div>
  )
}
