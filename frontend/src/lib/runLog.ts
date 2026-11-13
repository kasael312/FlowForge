/** Folds `LogEvent`s from the WebSocket into a per-step view model. */

import type { LogEvent, RunStatus } from '../api/types'

export type LogEntry =
  | { kind: 'text'; text: string; turn?: number }
  | { kind: 'tool_call'; tool: string; input: Record<string, unknown>; toolUseId?: string }
  | { kind: 'tool_result'; tool: string; output: string; isError: boolean; duration?: number; toolUseId?: string }
  | { kind: 'log'; message: string; level: string }

export interface ApprovalRequest {
  stepId: string
  stepRunId: string
  question: string
  prevOutput: string
  timeoutSeconds: number
}

export interface StepLog {
  /** step_run_id when known, else `${step_id}#${attempt}`. */
  key: string
  stepId: string
  type: string
  label: string
  attempt: number
  status: 'running' | 'waiting' | 'success' | 'failed'
  entries: LogEntry[]
  duration?: number
  error?: string | null
  output?: string
}

export interface RunLogState {
  status: RunStatus | 'connecting'
  error: string | null
  steps: StepLog[]
  /** Events without a step (clone, retry/goto notices, errors). */
  runLogs: LogEntry[]
  /** Latest variable context (input, steps, items, prev) streamed by the executor. */
  variables: Record<string, unknown> | null
  /** Results declared by output nodes so far. */
  outputs: Record<string, unknown>[]
  /** Approval the run is currently waiting on (null once resolved). */
  approval: ApprovalRequest | null
  /** Files kept from /repo/output (announced at the end of the run). */
  artifacts: { id: string; name: string; size: number; content_type: string }[]
  ended: boolean
  replayed: boolean
}

export const initialRunLogState: RunLogState = {
  status: 'connecting',
  error: null,
  steps: [],
  runLogs: [],
  variables: null,
  outputs: [],
  approval: null,
  artifacts: [],
  ended: false,
  replayed: false,
}

function entryOf(ev: LogEvent): LogEntry | null {
  switch (ev.event) {
    case 'text':
      return { kind: 'text', text: ev.text ?? '', turn: ev.turn as number | undefined }
    case 'tool_call':
      return { kind: 'tool_call', tool: ev.tool ?? '?', input: ev.input ?? {}, toolUseId: ev.tool_use_id as string | undefined }
    case 'tool_result':
      return {
        kind: 'tool_result',
        tool: ev.tool ?? '?',
        output: ev.output ?? '',
        isError: Boolean(ev.is_error),
        duration: ev.duration,
        toolUseId: ev.tool_use_id as string | undefined,
      }
    case 'log':
      return { kind: 'log', message: ev.message ?? '', level: ev.level ?? 'info' }
    default:
      return null
  }
}

function findStepIndex(steps: StepLog[], ev: LogEvent): number {
  if (ev.step_run_id) {
    const i = steps.findIndex((s) => s.key === ev.step_run_id)
    if (i >= 0) return i
  }
  if (ev.step_id) {
    for (let i = steps.length - 1; i >= 0; i -= 1) {
      if (steps[i]!.stepId === ev.step_id) return i
    }
  }
  return -1
}

export function runLogReducer(state: RunLogState, ev: LogEvent): RunLogState {
  const replayed = state.replayed || Boolean(ev.replayed)
  switch (ev.event) {
    case 'run_status': {
      const status = (ev.status as RunStatus | undefined) ?? state.status
      return { ...state, status, error: ev.error ?? null, replayed }
    }
    case 'step_start': {
      const step: StepLog = {
        key: ev.step_run_id ?? `${ev.step_id}#${ev.attempt ?? 1}`,
        stepId: ev.step_id ?? '?',
        type: ev.type ?? 'code',
        label: ev.label || ev.step_id || '?',
        attempt: ev.attempt ?? 1,
        status: 'running',
        entries: [],
      }
      return { ...state, steps: [...state.steps, step], replayed }
    }
    case 'step_end': {
      const i = findStepIndex(state.steps, ev)
      if (i < 0) return state
      const status = ev.status === 'success' ? 'success' : 'failed'
      const steps = state.steps.slice()
      steps[i] = { ...steps[i]!, status, duration: ev.duration, error: ev.error ?? null, output: ev.output }
      return { ...state, steps, approval: state.approval?.stepRunId === steps[i]!.key ? null : state.approval, replayed }
    }
    case 'approval_requested': {
      const approval: ApprovalRequest = {
        stepId: ev.step_id ?? '',
        stepRunId: ev.step_run_id ?? '',
        question: (ev.question as string) ?? '',
        prevOutput: (ev.prev_output as string) ?? '',
        timeoutSeconds: (ev.timeout_seconds as number) ?? 0,
      }
      const i = findStepIndex(state.steps, ev)
      const steps = state.steps.slice()
      if (i >= 0) steps[i] = { ...steps[i]!, status: 'waiting' }
      return { ...state, steps, approval: replayed && state.ended ? null : approval, replayed }
    }
    case 'approval_resolved': {
      const i = findStepIndex(state.steps, ev)
      const steps = state.steps.slice()
      if (i >= 0) steps[i] = { ...steps[i]!, status: 'running', entries: [...steps[i]!.entries, { kind: 'log', level: 'info', message: `${ev.decision}${ev.comment ? `: ${ev.comment}` : ''}` }] }
      return { ...state, steps, approval: null, replayed }
    }
    case 'variables':
      return { ...state, variables: (ev.variables as Record<string, unknown>) ?? null }
    case 'output': {
      const rec = ev.output as unknown as Record<string, unknown> | undefined
      if (!rec) return state
      const others = state.outputs.filter((o) => o.step_id !== rec.step_id)
      return { ...state, outputs: [...others, rec] }
    }
    case 'artifacts':
      return { ...state, artifacts: (ev.artifacts as RunLogState['artifacts']) ?? [] }
    case 'end':
      return { ...state, ended: true, approval: null }
    default: {
      const entry = entryOf(ev)
      if (!entry) return state
      const i = findStepIndex(state.steps, ev)
      if (i < 0) return { ...state, runLogs: [...state.runLogs, entry], replayed }
      const steps = state.steps.slice()
      steps[i] = { ...steps[i]!, entries: [...steps[i]!.entries, entry] }
      return { ...state, steps, replayed }
    }
  }
}

export function isTerminal(status: RunLogState['status']): boolean {
  return status === 'success' || status === 'failed' || status === 'cancelled'
}
