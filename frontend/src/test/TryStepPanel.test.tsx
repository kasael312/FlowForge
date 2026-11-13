import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { FlowNode } from '../api/types'
import { TryStepPanel } from '../components/TryStepPanel'
import { defaultStepData } from '../lib/steps'

const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }))

describe('TryStepPanel', () => {
  const fetchMock = vi.fn<typeof fetch>()
  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })

  it('sends the unsaved node with simulated inputs and shows the result', async () => {
    const node: FlowNode = { id: 'c1', type: 'step', position: { x: 0, y: 0 }, data: { ...defaultStepData('custom'), label: 'Explain', system_prompt: 'Explain {{task}}' } }
    fetchMock.mockImplementation(() =>
      json({ step_id: 'c1', type: 'custom', success: true, output: 'I would explain it.', error: null, tokens: 42, system_prompt: 'SYS Explain fix X', user_message: 'USER' }),
    )
    render(<TryStepPanel flowId="f1" node={node} onClose={vi.fn()} />)
    await userEvent.clear(screen.getByLabelText('Task'))
    await userEvent.type(screen.getByLabelText('Task'), 'fix X')
    await userEvent.type(screen.getByLabelText('Previous step output'), 'prev')
    await userEvent.click(screen.getByRole('button', { name: 'Run step' }))

    const status = await screen.findByRole('status')
    expect(status).toHaveTextContent('success')
    expect(status).toHaveTextContent('42 tokens')
    expect(screen.getByText('I would explain it.')).toBeInTheDocument()
    const [url, init] = fetchMock.mock.calls[0]!
    expect(String(url)).toMatch(/\/flows\/f1\/steps\/c1\/try$/)
    const body = JSON.parse(String((init as RequestInit).body))
    expect(body.task).toBe('fix X')
    expect(body.prev_output).toBe('prev')
    expect(body.node.data.system_prompt).toBe('Explain {{task}}')

    await userEvent.click(screen.getByText('show the prompts that were sent'))
    expect(screen.getByText('SYS Explain fix X')).toBeInTheDocument()
  })

  it('shows errors for conditions and hides the task field', async () => {
    const node: FlowNode = { id: 'k', type: 'step', position: { x: 0, y: 0 }, data: { ...defaultStepData('condition'), expression: 'contains(' } }
    fetchMock.mockImplementation(() => json({ step_id: 'k', type: 'condition', success: false, output: '', error: 'invalid expression: ...', tokens: 0, system_prompt: '', user_message: '' }))
    render(<TryStepPanel flowId="f1" node={node} onClose={vi.fn()} />)
    expect(screen.queryByLabelText('Task')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Run step' }))
    expect(await screen.findByRole('status')).toHaveTextContent('failed')
    expect(screen.getByText(/invalid expression/)).toBeInTheDocument()
  })
})
