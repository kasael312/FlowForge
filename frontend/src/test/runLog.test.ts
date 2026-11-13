import { describe, expect, it } from 'vitest'
import type { LogEvent } from '../api/types'
import { initialRunLogState, isTerminal, runLogReducer } from '../lib/runLog'

function fold(events: LogEvent[]) {
  return events.reduce(runLogReducer, initialRunLogState)
}

describe('runLogReducer', () => {
  it('groups events by step run and tracks status', () => {
    const s = fold([
      { event: 'run_status', status: 'running' },
      { event: 'log', message: 'cloned repo' },
      { event: 'step_start', step_id: 'plan', step_run_id: 'sr1', type: 'plan', label: 'Plan', attempt: 1 },
      { event: 'text', step_id: 'plan', step_run_id: 'sr1', text: 'thinking' },
      { event: 'tool_call', step_id: 'plan', step_run_id: 'sr1', tool: 'read_file', input: { path: 'a.py' }, tool_use_id: 't1' },
      { event: 'tool_result', step_id: 'plan', step_run_id: 'sr1', tool: 'read_file', output: 'x = 1', is_error: false, duration: 0.1 },
      { event: 'step_end', step_id: 'plan', step_run_id: 'sr1', status: 'success', duration: 2.5 },
      { event: 'step_start', step_id: 'code', step_run_id: 'sr2', type: 'code', label: 'Code', attempt: 1 },
      { event: 'step_end', step_id: 'code', step_run_id: 'sr2', status: 'failed', duration: 1, error: 'max_turns' },
      { event: 'log', step_id: 'code', message: 'retrying (1/2)' },
      { event: 'step_start', step_id: 'code', step_run_id: 'sr3', type: 'code', label: 'Code', attempt: 2 },
      { event: 'run_status', status: 'failed', error: 'a step failed' },
      { event: 'end' },
    ])
    expect(s.status).toBe('failed')
    expect(s.error).toBe('a step failed')
    expect(s.ended).toBe(true)
    expect(s.runLogs).toEqual([{ kind: 'log', message: 'cloned repo', level: 'info' }])
    expect(s.steps.map((st) => [st.stepId, st.attempt, st.status])).toEqual([
      ['plan', 1, 'success'],
      ['code', 1, 'failed'],
      ['code', 2, 'running'],
    ])
    const plan = s.steps[0]!
    expect(plan.duration).toBe(2.5)
    expect(plan.entries.map((e) => e.kind)).toEqual(['text', 'tool_call', 'tool_result'])
    // a log carrying step_id but no step_run_id lands on the latest run of that step
    expect(s.steps[1]!.entries).toEqual([{ kind: 'log', message: 'retrying (1/2)', level: 'info' }])
    expect(s.steps[1]!.error).toBe('max_turns')
  })

  it('marks replayed streams and ignores unknown step_end', () => {
    const s = fold([
      { event: 'run_status', status: 'running', replayed: true },
      { event: 'step_end', step_id: 'ghost', status: 'success' },
    ])
    expect(s.replayed).toBe(true)
    expect(s.steps).toEqual([])
  })

  it('isTerminal', () => {
    expect(isTerminal('running')).toBe(false)
    expect(isTerminal('connecting')).toBe(false)
    expect(isTerminal('success')).toBe(true)
    expect(isTerminal('cancelled')).toBe(true)
  })
})
