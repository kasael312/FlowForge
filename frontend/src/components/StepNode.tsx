import { Handle, Position, type NodeProps, type Node } from '@xyflow/react'
import { BRANCH_HANDLES, type StepData } from '../api/types'
import { STEP_META } from '../lib/steps'
import { modelLabel, useModels } from '../lib/ModelsContext'
import { skillNames, useSkills } from '../lib/SkillsContext'

export type StepFlowNode = Node<StepData, 'step'>

/** Custom React Flow node: colored header per step type, 1 or 2 source handles. */
export function StepNode({ data, selected }: NodeProps<StepFlowNode>) {
  const meta = STEP_META[data.type]
  const isCondition = data.type === 'condition'
  const handles = BRANCH_HANDLES[data.type]
  const models = useModels()
  const tag = meta.usesLlm ? modelLabel(models, data.model_id) : null
  const skills = useSkills()
  const persona = meta.usesLlm ? skills?.find((s) => s.kind === 'persona' && (data.skill_ids ?? []).includes(s.id)) : undefined
  const skillCount = meta.usesLlm ? (data.skill_ids ?? []).filter((id) => id !== persona?.id).length : 0
  const subtitle =
    data.type === 'shell' ? data.command
    : isCondition ? data.expression
    : data.type === 'loop' ? `for each: ${data.items_expression ?? ''}`
    : data.type === 'http_request' ? `${data.method ?? 'GET'} ${data.url ?? ''}`
    : data.type === 'data_transform' ? `${data.language ?? 'python'}: ${(data.code ?? '').split('\n')[0]}`
    : data.type === 'output' ? `${data.output_kind ?? 'text'} ← ${data.output_source ?? ''}`
    : data.type === 'approval' ? (data.system_prompt || 'wait for approval').split('\n')[0]
    : data.type === 'template' ? (data.output_path ? `→ ${data.output_path}` : (data.template ?? '').split('\n')[0])
    : data.type === 'merge' ? `merge ${(data.merge_steps ?? []).join(', ') || 'all'}`
    : data.system_prompt
  return (
    <div
      className={`w-52 rounded-lg border bg-white text-xs shadow-sm dark:bg-zinc-900 ${
        selected ? 'border-zinc-900 ring-2 ring-zinc-900/20 dark:border-zinc-100 dark:ring-zinc-100/20' : 'border-zinc-300 dark:border-zinc-700'
      }`}
    >
      <Handle type="target" position={Position.Left} className="!h-2.5 !w-2.5 !bg-zinc-500" />
      <div className={`flex items-center gap-2 rounded-t-lg px-3 py-1.5 text-white ${meta.color}`}>
        <span className="font-semibold uppercase tracking-wide opacity-80">{meta.label}</span>
      </div>
      <div className="px-3 py-2">
        <div className="truncate font-medium text-zinc-900 dark:text-zinc-100">{data.label || data.type}</div>
        <div className="mt-0.5 line-clamp-2 font-mono text-[10px] text-zinc-500">{subtitle || meta.description}</div>
        {meta.usesLlm && data.tools_allowed.length > 0 && (
          <span className="mr-1 mt-1 inline-block rounded bg-zinc-100 px-1.5 py-0.5 text-[9px] text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300" title={data.tools_allowed.join(', ')} data-testid="tool-count">
            {data.tools_allowed.length} tools
          </span>
        )}
        {persona && (
          <span className="mr-1 mt-1 inline-block rounded bg-violet-100 px-1.5 py-0.5 text-[9px] text-violet-800 dark:bg-violet-900 dark:text-violet-100" title={`persona: ${persona.name}`} data-testid="persona-tag">
            {persona.name}
          </span>
        )}
        {skillCount > 0 && (
          <span className="mr-1 mt-1 inline-block rounded bg-amber-100 px-1.5 py-0.5 text-[9px] text-amber-800 dark:bg-amber-900 dark:text-amber-100" title={skillNames(skills, (data.skill_ids ?? []).filter((id) => id !== persona?.id)).join(', ')} data-testid="skill-count">
            {skillCount} {skillCount === 1 ? 'skill' : 'skills'}
          </span>
        )}
        {tag && (
          <div className="mt-1 inline-block max-w-full truncate rounded bg-zinc-100 px-1.5 py-0.5 text-[9px] font-medium text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300" title={`model: ${tag}`} data-testid="model-tag">
            {tag}
          </div>
        )}
      </div>
      {handles ? (
        <>
          <Handle id={handles[0]} type="source" position={Position.Right} style={{ top: '35%' }} className="!h-2.5 !w-2.5 !bg-emerald-500" />
          <Handle id={handles[1]} type="source" position={Position.Right} style={{ top: '70%' }} className="!h-2.5 !w-2.5 !bg-rose-500" />
          <span className="pointer-events-none absolute right-3 top-[26%] text-[9px] font-semibold text-emerald-600">{handles[0]}</span>
          <span className="pointer-events-none absolute right-3 top-[61%] text-[9px] font-semibold text-rose-600">{handles[1]}</span>
        </>
      ) : (
        <Handle type="source" position={Position.Right} className="!h-2.5 !w-2.5 !bg-zinc-500" />
      )}
    </div>
  )
}
