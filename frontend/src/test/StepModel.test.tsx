import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { FlowNode, LlmModel, StepData } from '../api/types'
import { StepSidebar } from '../components/StepSidebar'
import { ModelsContext } from '../lib/ModelsContext'
import { defaultStepData } from '../lib/steps'

const models: LlmModel[] = [
  { id: 'm1', provider: 'anthropic', model_name: 'claude-sonnet-5-5', label: 'Sonnet', base_url: null, api_key_id: null, api_key_last4: null, params: {}, is_default: true, created_at: '', pricing_key: '', price: null },
  { id: 'm2', provider: 'ollama', model_name: 'llama3.2', label: 'Local', base_url: null, api_key_id: null, api_key_last4: null, params: {}, is_default: false, created_at: '', pricing_key: '', price: null },
]

const node: FlowNode = { id: 'code-1', type: 'step', position: { x: 0, y: 0 }, data: defaultStepData('code') }

describe('step model selection', () => {
  it('offers the user models with the default labelled, and sets model + fallback', async () => {
    const onChange = vi.fn()
    render(
      <ModelsContext.Provider value={models}>
        <StepSidebar node={node} nodes={[node]} onChange={onChange} onChangeType={vi.fn()} onDelete={vi.fn()} />
      </ModelsContext.Provider>,
    )
    const select = screen.getByLabelText('Model')
    expect(select).toHaveTextContent('Default (Sonnet)')
    await userEvent.selectOptions(select, 'm2')
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ model_id: 'm2' }))
    await userEvent.selectOptions(screen.getByLabelText('Fallback model'), 'm1')
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ fallback_model_id: 'm1' }))
  })

  it('hides model fields for shell/condition steps and tolerates no models', () => {
    const shell: FlowNode = { ...node, data: { ...defaultStepData('shell') } as StepData }
    const { rerender } = render(<StepSidebar node={shell} nodes={[shell]} onChange={vi.fn()} onChangeType={vi.fn()} onDelete={vi.fn()} />)
    expect(screen.queryByLabelText('Model')).toBeNull()
    rerender(<StepSidebar node={node} nodes={[node]} onChange={vi.fn()} onChangeType={vi.fn()} onDelete={vi.fn()} />)
    expect(screen.getByLabelText('Model')).toHaveTextContent('Default')
  })
})
