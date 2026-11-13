import { ALL_TOOLS, STEP_TYPES, TOOL_CATEGORIES, type FlowNode, type StepData, type StepType, type ToolName } from '../api/types'
import { PROMPT_VARIABLES, STEP_META, parseOnFail } from '../lib/steps'
import { useModels } from '../lib/ModelsContext'
import { skillNames, useSkills } from '../lib/SkillsContext'
import { useTools } from '../lib/ToolsContext'
import { NumberField } from './NumberField'

interface Props {
  node: FlowNode
  /** Other nodes, used for goto targets. */
  nodes: FlowNode[]
  onChange: (data: StepData) => void
  onChangeType: (type: StepType) => void
  onDelete: () => void
  /** Opens the dry-run panel for this step (optional). */
  onTry?: () => void
  /** Opens the composed-prompt modal for this step (optional, LLM steps). */
  onViewPrompt?: () => void
  /** Opens the rules panel focused on this step (optional, LLM steps). */
  onRules?: () => void
}

const field = 'mt-1 w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900'
const label = 'block text-xs font-medium text-zinc-600 dark:text-zinc-400'

/** Property editor for the selected step. Pure: emits new data via callbacks. */
export function StepSidebar({ node, nodes, onChange, onChangeType, onDelete, onTry, onViewPrompt, onRules }: Props) {
  const d = node.data
  const meta = STEP_META[d.type]
  const models = useModels()
  const registryTools = useTools()
  const skills = useSkills()
  const toggleSkill = (id: string) => {
    const cur = d.skill_ids ?? []
    set('skill_ids', cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id])
  }
  const onFail = parseOnFail(d.on_fail)
  const others = nodes.filter((n) => n.id !== node.id)
  const set = <K extends keyof StepData>(key: K, value: StepData[K]) => onChange({ ...d, [key]: value })

  const toggleTool = (tool: ToolName) => {
    const next = d.tools_allowed.includes(tool) ? d.tools_allowed.filter((t) => t !== tool) : [...d.tools_allowed, tool]
    set('tools_allowed', next)
  }

  return (
    <aside className="flex h-full w-80 flex-col gap-3 overflow-y-auto border-l border-zinc-200 bg-zinc-50 p-4 dark:border-zinc-800 dark:bg-zinc-950" aria-label="Step properties">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">
          Step <span className="font-mono text-zinc-500">{node.id}</span>
        </h2>
        <div className="flex gap-3">
          {onTry && (
            <button type="button" onClick={onTry} className="text-xs text-teal-700 hover:underline dark:text-teal-300">
              Try this step
            </button>
          )}
          {meta.usesLlm && onRules && (
            <button type="button" onClick={onRules} className="text-xs text-rose-700 hover:underline dark:text-rose-300">
              Rules
            </button>
          )}
          {meta.usesLlm && onViewPrompt && (
            <button type="button" onClick={onViewPrompt} className="text-xs text-violet-700 hover:underline dark:text-violet-300">
              Composed prompt
            </button>
          )}
          <button type="button" onClick={onDelete} className="text-xs text-rose-600 hover:underline">
            Delete
          </button>
        </div>
      </div>

      <div>
        <label htmlFor="step-label" className={label}>Label</label>
        <input id="step-label" className={field} value={d.label} onChange={(e) => set('label', e.target.value)} />
      </div>

      <div>
        <label htmlFor="step-type" className={label}>Type</label>
        <select id="step-type" className={field} value={d.type} onChange={(e) => onChangeType(e.target.value as StepType)}>
          {STEP_TYPES.map((t) => (
            <option key={t} value={t}>
              {STEP_META[t].label} — {STEP_META[t].description}
            </option>
          ))}
        </select>
      </div>

      {meta.usesLlm && (
        <>
          <div>
            <label htmlFor="step-prompt" className={label}>System prompt</label>
            <textarea
              id="step-prompt"
              className={`${field} h-40 font-mono text-xs`}
              placeholder={d.type === 'custom' ? 'Write the full instructions for this step. Use {{task}}, {{prev_output}}, {{repo}}…' : `Leave empty to use the default ${meta.label} prompt.`}
              value={d.system_prompt}
              onChange={(e) => set('system_prompt', e.target.value)}
            />
            <div className="mt-1 flex flex-wrap gap-1" aria-label="Insert variable">
              {PROMPT_VARIABLES.map((v) => (
                <button
                  key={v.name}
                  type="button"
                  title={v.description}
                  onClick={() => set('system_prompt', `${d.system_prompt}${d.system_prompt && !d.system_prompt.endsWith(' ') ? ' ' : ''}{{${v.name}}}`)}
                  className="rounded border border-zinc-300 px-1.5 py-0.5 font-mono text-[10px] hover:bg-zinc-200 dark:border-zinc-700 dark:hover:bg-zinc-800"
                >
                  {`{{${v.name}}}`}
                </button>
              ))}
            </div>
          </div>
          <fieldset>
            <legend className={label}>Tools allowed</legend>
            {registryTools && registryTools.length > 0 ? (
              <div className="mt-1 max-h-56 space-y-2 overflow-y-auto rounded border border-zinc-200 p-2 dark:border-zinc-800">
                {TOOL_CATEGORIES.map((cat) => {
                  const items = registryTools.filter((t) => t.category === cat)
                  if (items.length === 0) return null
                  return (
                    <div key={cat}>
                      <div className="text-[10px] font-semibold uppercase tracking-wide text-zinc-500">{cat}</div>
                      <div className="grid grid-cols-2 gap-x-2 gap-y-0.5">
                        {items.map((t) => (
                          <label key={t.name} className="flex items-center gap-1.5 text-xs" title={t.description}>
                            <input type="checkbox" checked={d.tools_allowed.includes(t.name)} onChange={() => toggleTool(t.name)} />
                            <span className={`truncate font-mono ${t.status !== 'ready' ? 'text-amber-600' : ''}`}>{t.category === 'mcp' ? `${t.name.split(':').slice(2).join(':')}${t.description.match(/^\[(.+?)\]/)?.[1] ? ` · ${t.description.match(/^\[(.+?)\]/)![1]}` : ''}` : t.name}</span>
                          </label>
                        ))}
                      </div>
                    </div>
                  )
                })}
                {d.tools_allowed.filter((n) => !registryTools.some((t) => t.name === n)).length > 0 && (
                  <p className="text-[10px] text-rose-600">Unavailable: {d.tools_allowed.filter((n) => !registryTools.some((t) => t.name === n)).join(', ')}</p>
                )}
              </div>
            ) : (
              <div className="mt-1 grid grid-cols-2 gap-1">
                {ALL_TOOLS.map((tool) => (
                  <label key={tool} className="flex items-center gap-1.5 text-xs">
                    <input type="checkbox" checked={d.tools_allowed.includes(tool)} onChange={() => toggleTool(tool)} />
                    <span className="font-mono">{tool}</span>
                  </label>
                ))}
              </div>
            )}
            <p className="mt-1 text-[10px] text-zinc-500">{d.tools_allowed.length} selected</p>
          </fieldset>
          {skills && skills.some((s) => s.kind === 'persona') && (
            <div data-testid="persona-picker">
              <label htmlFor="step-persona" className={label}>Persona (who the agent is)</label>
              <select
                id="step-persona"
                className={field}
                value={(d.skill_ids ?? []).find((id) => skills.some((s) => s.id === id && s.kind === 'persona')) ?? ''}
                onChange={(e) => {
                  const rest = (d.skill_ids ?? []).filter((id) => !skills.some((s) => s.id === id && s.kind === 'persona'))
                  set('skill_ids', e.target.value ? [e.target.value, ...rest] : rest)
                }}
              >
                <option value="">None</option>
                {skills
                  .filter((s) => s.kind === 'persona')
                  .map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                      {s.builtin ? ' (built-in)' : ''}
                    </option>
                  ))}
              </select>
            </div>
          )}
          {skills && (
            <fieldset data-testid="skill-picker">
              <legend className={label}>Skills (added to the prompt, files mounted at /skills/&lt;name&gt;/)</legend>
              {skills.filter((s) => s.kind !== 'persona').length === 0 ? (
                <p className="mt-1 text-[10px] text-zinc-500">No skills yet. Create one on the Skills page.</p>
              ) : (
                <div className="mt-1 max-h-40 space-y-0.5 overflow-y-auto rounded border border-zinc-200 p-2 dark:border-zinc-800">
                  {skills.filter((s) => s.kind !== 'persona').map((s) => (
                    <label key={s.id} className="flex items-center gap-1.5 text-xs" title={s.description}>
                      <input type="checkbox" checked={(d.skill_ids ?? []).includes(s.id)} onChange={() => toggleSkill(s.id)} />
                      <span className="truncate font-mono">{s.name}</span>
                      {s.builtin && <span className="text-[9px] text-zinc-400">built-in</span>}
                    </label>
                  ))}
                </div>
              )}
              {(d.skill_ids ?? []).some((id) => !skills.some((s) => s.id === id)) && (
                <p className="text-[10px] text-rose-600">Unknown skill ids: {skillNames(skills, d.skill_ids).filter((n) => !skills.some((s) => s.name === n)).join(', ')}</p>
              )}
            </fieldset>
          )}
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label htmlFor="step-model" className={label}>Model</label>
              <select id="step-model" className={field} value={d.model_id ?? ''} onChange={(e) => set('model_id', e.target.value || null)}>
                <option value="">Default{models?.find((m) => m.is_default) ? ` (${models.find((m) => m.is_default)!.label})` : ''}</option>
                {(models ?? []).map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.label}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="step-fallback" className={label}>Fallback model</label>
              <select id="step-fallback" className={field} value={d.fallback_model_id ?? ''} onChange={(e) => set('fallback_model_id', e.target.value || null)}>
                <option value="">None</option>
                {(models ?? [])
                  .filter((m) => m.id !== d.model_id)
                  .map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.label}
                    </option>
                  ))}
              </select>
            </div>
          </div>
          <div>
            <label htmlFor="step-max-turns" className={label}>Max turns</label>
            <NumberField id="step-max-turns" min={1} max={200} className={field} value={d.max_turns} onCommit={(n) => set('max_turns', n)} />
          </div>
        </>
      )}

      {d.type === 'shell' && (
        <div>
          <label htmlFor="step-command" className={label}>Command (whitelisted executable + args, no shell syntax)</label>
          <input id="step-command" className={`${field} font-mono`} placeholder="pytest -q" value={d.command ?? ''} onChange={(e) => set('command', e.target.value)} />
        </div>
      )}

      {d.type === 'data_transform' && (
        <>
          <div>
            <label htmlFor="step-language" className={label}>Language</label>
            <select id="step-language" className={field} value={d.language ?? 'python'} onChange={(e) => set('language', e.target.value)}>
              <option value="python">Python</option>
              <option value="javascript">JavaScript (node)</option>
            </select>
          </div>
          <div>
            <label htmlFor="step-code" className={label}>Code</label>
            <textarea id="step-code" className={`${field} h-40 font-mono text-xs`} value={d.code ?? ''} onChange={(e) => set('code', e.target.value)} />
            <p className="mt-1 text-[11px] text-zinc-500">
              Available: <code>input</code>, <code>steps</code>, <code>items</code>, <code>prev</code>, <code>task</code>; call <code>emit(value)</code> (or print JSON) to produce the output. Runs in the sandbox.
            </p>
          </div>
        </>
      )}
      {d.type === 'http_request' && (
        <>
          <div className="grid grid-cols-[6rem_1fr] gap-2">
            <div>
              <label htmlFor="step-method" className={label}>Method</label>
              <select id="step-method" className={field} value={d.method ?? 'GET'} onChange={(e) => set('method', e.target.value)}>
                {['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="step-url" className={label}>URL</label>
              <input id="step-url" className={`${field} font-mono`} placeholder="https://api.example.com/items?q={{input.q}}" value={d.url ?? ''} onChange={(e) => set('url', e.target.value)} />
            </div>
          </div>
          <div>
            <label htmlFor="step-headers" className={label}>Headers (KEY=value per line, variables allowed)</label>
            <textarea
              id="step-headers"
              className={`${field} h-16 font-mono text-xs`}
              value={Object.entries(d.headers ?? {}).map(([k, v]) => `${k}=${v}`).join('\n')}
              onChange={(e) => {
                const headers: Record<string, string> = {}
                for (const line of e.target.value.split('\n')) {
                  const [k, ...rest] = line.split('=')
                  if (k?.trim()) headers[k.trim()] = rest.join('=').trim()
                }
                set('headers', headers)
              }}
            />
          </div>
          <div>
            <label htmlFor="step-body" className={label}>Body (JSON or text, variables allowed)</label>
            <textarea id="step-body" className={`${field} h-20 font-mono text-xs`} value={d.body ?? ''} onChange={(e) => set('body', e.target.value)} />
          </div>
          <p className="text-[11px] text-zinc-500">Response is available as <code>steps.{node.id}.output</code> and, for JSON, <code>steps.{node.id}.data.body</code>.</p>
        </>
      )}
      {d.type === 'loop' && (
        <div>
          <label htmlFor="step-items" className={label}>Items</label>
          <input id="step-items" className={`${field} font-mono`} placeholder="steps.fetch.data.body.items" value={d.items_expression ?? ''} onChange={(e) => set('items_expression', e.target.value)} />
          <p className="mt-1 text-[11px] text-zinc-500">
            A variable path (<code>input.list</code>, <code>steps.x.data.rows</code>), JSON array or comma list. The <b>body</b> handle runs once per item with <code>items.current</code>, <code>items.index</code>, <code>items.total</code>; connect the last body step back to this node. <b>done</b> continues afterwards with <code>steps.{node.id}.data</code> = list of iteration results.
          </p>
        </div>
      )}
      {d.type === 'merge' && (
        <>
          <div>
            <label htmlFor="step-merge-steps" className={label}>Steps to merge (comma separated ids, empty = all successful)</label>
            <input id="step-merge-steps" className={`${field} font-mono`} value={(d.merge_steps ?? []).join(', ')} onChange={(e) => set('merge_steps', e.target.value.split(',').map((x) => x.trim()).filter(Boolean))} />
          </div>
          <div>
            <label htmlFor="step-merge-mode" className={label}>Mode</label>
            <select id="step-merge-mode" className={field} value={d.merge_mode ?? 'concat'} onChange={(e) => set('merge_mode', e.target.value)}>
              <option value="concat">concat — markdown sections</option>
              <option value="json">json — object keyed by step id</option>
            </select>
          </div>
        </>
      )}
      {d.type === 'template' && (
        <>
          <div>
            <label htmlFor="step-template" className={label}>Jinja template</label>
            <textarea id="step-template" className={`${field} h-40 font-mono text-xs`} value={d.template ?? ''} onChange={(e) => set('template', e.target.value)} />
            <p className="mt-1 text-[11px] text-zinc-500">Full Jinja2: <code>{'{{ steps.x.output }}'}</code>, <code>{'{% for r in steps.loop.data %}…{% endfor %}'}</code>, filters.</p>
          </div>
          <div>
            <label htmlFor="step-output-path" className={label}>Write to file (optional, relative path)</label>
            <input id="step-output-path" className={`${field} font-mono`} placeholder="output/report.md" value={d.output_path ?? ''} onChange={(e) => set('output_path', e.target.value)} />
          </div>
        </>
      )}
      {d.type === 'approval' && (
        <>
          <div>
            <label htmlFor="step-approval-question" className={label}>Question shown to the reviewer (variables allowed)</label>
            <textarea id="step-approval-question" className={`${field} h-28 text-xs`} value={d.system_prompt} onChange={(e) => set('system_prompt', e.target.value)} />
          </div>
          <div>
            <label htmlFor="step-approval-timeout" className={label}>Wait at most (seconds)</label>
            <input id="step-approval-timeout" type="number" min={30} className={field} value={d.approval_timeout_seconds ?? 86400} onChange={(e) => set('approval_timeout_seconds', Number(e.target.value) || 86400)} />
            <p className="mt-1 text-[11px] text-zinc-500">The run shows status <code>waiting</code>; approve or reject from the run page or <code>POST /runs/{'{id}'}/approve</code>. Rejection follows the step's on-fail policy.</p>
          </div>
        </>
      )}
      {d.type === 'output' && (
        <>
          <div className="grid grid-cols-[7rem_1fr] gap-2">
            <div>
              <label htmlFor="step-output-kind" className={label}>Kind</label>
              <select id="step-output-kind" className={field} value={d.output_kind ?? 'text'} onChange={(e) => set('output_kind', e.target.value)}>
                <option value="text">text</option>
                <option value="json">json</option>
                <option value="file">file</option>
              </select>
            </div>
            <div>
              <label htmlFor="step-output-source" className={label}>{d.output_kind === 'file' ? 'File path (relative)' : 'Value (variables / Jinja)'}</label>
              <input id="step-output-source" className={`${field} font-mono`} placeholder={d.output_kind === 'file' ? 'output/report.docx' : '{{steps.report.output}}'} value={d.output_source ?? ''} onChange={(e) => set('output_source', e.target.value)} />
            </div>
          </div>
          <div>
            <label htmlFor="step-deliver" className={label}>Deliver with tool (optional)</label>
            <select id="step-deliver" className={field} value={d.deliver_tool ?? ''} onChange={(e) => set('deliver_tool', e.target.value)}>
              <option value="">— none —</option>
              {(registryTools ?? []).filter((t) => !t.needs_sandbox && t.category !== 'code').map((t) => (
                <option key={t.name} value={t.name}>{t.name}</option>
              ))}
            </select>
          </div>
          {d.deliver_tool && (
            <div>
              <label htmlFor="step-deliver-args" className={label}>Tool arguments (JSON; use {'{{output}}'} for the value)</label>
              <textarea
                id="step-deliver-args"
                className={`${field} h-20 font-mono text-xs`}
                defaultValue={JSON.stringify(d.deliver_args ?? {}, null, 2)}
                onBlur={(e) => {
                  try {
                    set('deliver_args', JSON.parse(e.target.value || '{}'))
                  } catch {
                    /* keep previous args until valid */
                  }
                }}
              />
            </div>
          )}
        </>
      )}
      {d.type === 'condition' && (
        <div>
          <label htmlFor="step-expression" className={label}>Expression over previous step</label>
          <input
            id="step-expression"
            className={`${field} font-mono`}
            placeholder="success"
            value={d.expression ?? ''}
            onChange={(e) => set('expression', e.target.value)}
          />
          <p className="mt-1 text-[11px] text-zinc-500">
            Names: <code>success</code>, <code>output</code>, <code>exit_code</code>, <code>type</code>. Helpers: <code>contains("passed")</code>,{' '}
            <code>icontains("error")</code>, <code>matches("\\d+ passed")</code>. Combine with <code>and</code>, <code>or</code>, <code>not</code>. True → green
            handle, false → red handle.
          </p>
        </div>
      )}

      {d.type !== 'condition' && (
        <>
          <div>
            <label htmlFor="step-on-fail" className={label}>On failure</label>
            <select
              id="step-on-fail"
              className={field}
              value={onFail.mode}
              onChange={(e) => {
                const mode = e.target.value
                if (mode === 'goto') set('on_fail', `goto:${others[0]?.id ?? ''}`)
                else set('on_fail', mode)
              }}
            >
              <option value="stop">{d.type === 'loop' ? 'stop — stop at the first failing iteration' : 'stop — fail the run'}</option>
              <option value="retry">{d.type === 'loop' ? 'retry — continue with remaining items' : 'retry — run this step again'}</option>
              <option value="goto" disabled={others.length === 0}>goto — jump to another step</option>
            </select>
          </div>
          {onFail.mode === 'goto' && (
            <div>
              <label htmlFor="step-goto" className={label}>Go to step</label>
              <select id="step-goto" className={field} value={onFail.target} onChange={(e) => set('on_fail', `goto:${e.target.value}`)}>
                {others.map((n) => (
                  <option key={n.id} value={n.id}>
                    {n.data.label || n.id} ({n.id})
                  </option>
                ))}
              </select>
            </div>
          )}
          {onFail.mode !== 'stop' && (
            <div>
              <label htmlFor="step-retry-limit" className={label}>Retry limit</label>
              <NumberField id="step-retry-limit" min={0} max={10} className={field} value={d.retry_limit} onCommit={(n) => set('retry_limit', n)} />
            </div>
          )}
        </>
      )}
    </aside>
  )
}
