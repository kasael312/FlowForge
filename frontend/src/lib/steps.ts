/** Step type metadata and defaults shared by the editor and node renderer. */

import { ALL_TOOLS, BRANCH_HANDLES, type FlowEdge, type FlowNode, type StepData, type StepType } from '../api/types'

export interface StepMeta {
  label: string
  description: string
  /** Tailwind classes for the node accent. */
  color: string
  usesLlm: boolean
}

export const STEP_META: Record<StepType, StepMeta> = {
  plan: { label: 'Plan', description: 'Analyse and plan, no edits', color: 'bg-sky-500', usesLlm: true },
  code: { label: 'Code', description: 'Implement changes', color: 'bg-violet-500', usesLlm: true },
  test: { label: 'Test', description: 'Run and fix tests', color: 'bg-emerald-500', usesLlm: true },
  review: { label: 'Review', description: 'Review the diff', color: 'bg-amber-500', usesLlm: true },
  custom: { label: 'Custom', description: 'Your own prompt with {{variables}}', color: 'bg-teal-500', usesLlm: true },
  shell: { label: 'Shell', description: 'Run one whitelisted command', color: 'bg-zinc-500', usesLlm: false },
  condition: { label: 'Condition', description: 'Branch on previous result', color: 'bg-rose-500', usesLlm: false },
  data_transform: { label: 'Transform', description: 'Small Python/JS script in the sandbox', color: 'bg-indigo-500', usesLlm: false },
  http_request: { label: 'HTTP', description: 'Call an HTTP endpoint', color: 'bg-cyan-600', usesLlm: false },
  loop: { label: 'Loop', description: 'Run the body branch for each item', color: 'bg-orange-500', usesLlm: false },
  merge: { label: 'Merge', description: 'Combine outputs of earlier steps', color: 'bg-lime-600', usesLlm: false },
  template: { label: 'Template', description: 'Render Jinja to text or a file', color: 'bg-fuchsia-500', usesLlm: false },
  output: { label: 'Output', description: 'Declare a final result (text/JSON/file) and deliver it', color: 'bg-zinc-800', usesLlm: false },
  approval: { label: 'Approval', description: 'Pause until a person approves or rejects', color: 'bg-yellow-500', usesLlm: false },
}

export function defaultStepData(type: StepType, label?: string): StepData {
  return {
    label: label ?? STEP_META[type].label,
    type,
    system_prompt: '',
    tools_allowed: type === 'plan' || type === 'review' ? ['read_file', 'list_files', 'run_shell', 'git_diff'] : [...ALL_TOOLS],
    max_turns: 20,
    on_fail: 'stop',
    retry_limit: 1,
    command: type === 'shell' ? '' : null,
    skill_ids: [],
    expression: type === 'condition' ? 'success' : null,
    ...(type === 'data_transform' ? { language: 'python', code: "emit({'result': prev.get('output')})" } : {}),
    ...(type === 'http_request' ? { url: '', method: 'GET', headers: {}, body: '' } : {}),
    ...(type === 'loop' ? { items_expression: 'input.items' } : {}),
    ...(type === 'merge' ? { merge_steps: [], merge_mode: 'concat' } : {}),
    ...(type === 'template' ? { template: '# Report\n\n{{ prev_output }}', output_path: '' } : {}),
    ...(type === 'output' ? { output_kind: 'text', output_source: '{{prev_output}}', deliver_tool: '', deliver_args: {} } : {}),
    ...(type === 'approval' ? { system_prompt: 'Approve to continue?\n\n{{prev_output}}', approval_timeout_seconds: 86400 } : {}),
  }
}

let counter = 0
/** Unique-enough node id based on type and a counter. */
export function newNodeId(type: StepType, existing: Set<string>): string {
  let id = ''
  do {
    counter += 1
    id = `${type}-${counter}`
  } while (existing.has(id))
  return id
}

export function parseOnFail(onFail: string): { mode: 'retry' | 'stop' | 'goto'; target: string } {
  if (onFail === 'retry' || onFail === 'stop') return { mode: onFail, target: '' }
  if (onFail.startsWith('goto:')) return { mode: 'goto', target: onFail.slice(5) }
  return { mode: 'stop', target: '' }
}

/**
 * When a node becomes/stops being a condition its outgoing edges need
 * `sourceHandle` true/false (or none). Keeps at most two edges for a condition.
 */
export function reconcileEdgesForType(nodeId: string, type: StepType, edges: FlowEdge[]): FlowEdge[] {
  const out = edges.filter((e) => e.source === nodeId)
  const rest = edges.filter((e) => e.source !== nodeId)
  const handles = BRANCH_HANDLES[type]
  if (!handles) {
    return [...rest, ...out.map((e) => ({ ...e, sourceHandle: null }))]
  }
  const [a, b] = handles
  const fixed = out.slice(0, 2).map((e, i) => ({
    ...e,
    sourceHandle: e.sourceHandle === a || e.sourceHandle === b ? e.sourceHandle : i === 0 ? a : b,
  }))
  return [...rest, ...fixed]
}

/** Drop goto targets pointing at nodes that no longer exist. */
export function dropDanglingGotos(nodes: FlowNode[]): FlowNode[] {
  const ids = new Set(nodes.map((n) => n.id))
  return nodes.map((n) => {
    const { mode, target } = parseOnFail(n.data.on_fail)
    if (mode === 'goto' && !ids.has(target)) return { ...n, data: { ...n.data, on_fail: 'stop' } }
    return n
  })
}

/** Variables that are substituted into prompts at run time. */
export const PROMPT_VARIABLES: { name: string; description: string }[] = [
  { name: 'task', description: 'the task text entered when starting the run' },
  { name: 'prev_output', description: 'output of the previous step' },
  { name: 'repo', description: 'repository URL' },
  { name: 'branch', description: 'branch name' },
  { name: 'step_id', description: 'id of this step' },
  { name: 'input.<field>', description: 'a run input' },
  { name: 'steps.<id>.output', description: 'output of an earlier step (also .data for JSON)' },
  { name: 'items.current', description: 'current loop item (also items.index, items.total)' },
]

export const BLANK_FLOW = { name: 'Untitled flow', description: '', nodes: [], edges: [] } as const
