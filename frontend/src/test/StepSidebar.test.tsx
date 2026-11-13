import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import type { FlowNode, StepData, StepType } from '../api/types'
import { defaultStepData } from '../lib/steps'
import { StepSidebar } from '../components/StepSidebar'

function mk(id: string, data: Partial<StepData> = {}): FlowNode {
  return { id, type: 'step', position: { x: 0, y: 0 }, data: { ...defaultStepData('code'), ...data } }
}

/** Stateful host so the controlled inputs reflect edits, like the editor page does. */
function Harness({ node, others, spy, onChangeType = vi.fn(), onDelete = vi.fn() }: { node: FlowNode; others?: FlowNode[]; spy: (d: StepData) => void; onChangeType?: (t: StepType) => void; onDelete?: () => void }) {
  const [current, setCurrent] = useState(node)
  return (
    <StepSidebar
      node={current}
      nodes={[current, ...(others ?? [])]}
      onChange={(d) => {
        spy(d)
        setCurrent({ ...current, data: d })
      }}
      onChangeType={onChangeType}
      onDelete={onDelete}
    />
  )
}

describe('StepSidebar', () => {
  it('edits prompt, tools and max turns for LLM steps', async () => {
    const spy = vi.fn()
    render(<Harness node={mk('code-1', { label: 'Implement' })} others={[mk('test-1')]} spy={spy} />)

    await userEvent.type(screen.getByLabelText('System prompt'), 'Be brief')
    expect(screen.getByLabelText('System prompt')).toHaveValue('Be brief')
    expect(spy).toHaveBeenLastCalledWith(expect.objectContaining({ system_prompt: 'Be brief' }))

    await userEvent.click(screen.getByLabelText('write_file'))
    const afterToggle = spy.mock.lastCall![0] as StepData
    expect(afterToggle.tools_allowed).not.toContain('write_file')
    expect(afterToggle.tools_allowed).toContain('read_file')

    await userEvent.clear(screen.getByLabelText('Max turns'))
    await userEvent.type(screen.getByLabelText('Max turns'), '7')
    expect(screen.getByLabelText('Max turns')).toHaveValue(7)
    expect((spy.mock.lastCall![0] as StepData).max_turns).toBe(7)
  })

  it('switches on_fail to goto with a target and shows retry limit', async () => {
    const spy = vi.fn()
    render(<Harness node={mk('code-1')} others={[mk('test-1', { label: 'Run tests' })]} spy={spy} />)
    expect(screen.queryByLabelText('Retry limit')).toBeNull()

    await userEvent.selectOptions(screen.getByLabelText('On failure'), 'goto')
    expect(spy).toHaveBeenLastCalledWith(expect.objectContaining({ on_fail: 'goto:test-1' }))
    expect(screen.getByLabelText('Go to step')).toHaveValue('test-1')
    expect(screen.getByLabelText('Retry limit')).toHaveValue(1)
    expect(screen.getByText('Run tests (test-1)')).toBeInTheDocument()

    await userEvent.selectOptions(screen.getByLabelText('On failure'), 'retry')
    expect(spy).toHaveBeenLastCalledWith(expect.objectContaining({ on_fail: 'retry' }))
    expect(screen.queryByLabelText('Go to step')).toBeNull()
  })

  it('shows command for shell and expression for condition, hides LLM fields', () => {
    const shell = mk('s', { ...defaultStepData('shell'), command: 'pytest -q' })
    const { rerender } = render(<StepSidebar node={shell} nodes={[shell]} onChange={vi.fn()} onChangeType={vi.fn()} onDelete={vi.fn()} />)
    expect(screen.getByLabelText(/Command/)).toHaveValue('pytest -q')
    expect(screen.queryByLabelText('System prompt')).toBeNull()

    const cond = mk('c', defaultStepData('condition'))
    rerender(<StepSidebar node={cond} nodes={[cond]} onChange={vi.fn()} onChangeType={vi.fn()} onDelete={vi.fn()} />)
    expect(screen.getByLabelText(/Expression/)).toHaveValue('success')
    expect(screen.queryByLabelText('On failure')).toBeNull()
  })

  it('delegates type change and delete', async () => {
    const onChangeType = vi.fn()
    const onDelete = vi.fn()
    render(<Harness node={mk('x')} spy={vi.fn()} onChangeType={onChangeType} onDelete={onDelete} />)
    await userEvent.selectOptions(screen.getByLabelText('Type'), 'condition')
    expect(onChangeType).toHaveBeenCalledWith('condition')
    await userEvent.click(screen.getByText('Delete'))
    expect(onDelete).toHaveBeenCalled()
  })
})
