import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { FlowNode, ToolInfo } from '../api/types'
import { StepSidebar } from '../components/StepSidebar'
import { ToolsContext } from '../lib/ToolsContext'
import { defaultStepData } from '../lib/steps'

const t = (name: string, category: ToolInfo['category'], status: ToolInfo['status'] = 'ready'): ToolInfo => ({
  name,
  description: '',
  category,
  input_schema: {},
  required_credentials: [],
  missing_credentials: status === 'ready' ? [] : ['x'],
  needs_sandbox: false,
  status,
  server_id: null,
})

describe('step tool picker', () => {
  it('lists registry tools by category and toggles them', async () => {
    const onChange = vi.fn()
    const node: FlowNode = { id: 'c', type: 'step', position: { x: 0, y: 0 }, data: { ...defaultStepData('custom'), tools_allowed: ['read_file', 'mcp:s1:old'] } }
    render(
      <ToolsContext.Provider value={[t('read_file', 'code'), t('fetch_url', 'web'), t('slack_post', 'communication', 'missing_credentials'), t('mcp:s1:search', 'mcp')]}>
        <StepSidebar node={node} nodes={[node]} onChange={onChange} onChangeType={vi.fn()} onDelete={vi.fn()} />
      </ToolsContext.Provider>,
    )
    expect(screen.getByLabelText('read_file')).toBeChecked()
    expect(screen.getByLabelText('fetch_url')).not.toBeChecked()
    expect(screen.getByLabelText('search')).toBeInTheDocument() // mcp names shown without prefix
    expect(screen.getByText(/Unavailable: mcp:s1:old/)).toBeInTheDocument()
    expect(screen.getByText('2 selected')).toBeInTheDocument()

    await userEvent.click(screen.getByLabelText('fetch_url'))
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ tools_allowed: ['read_file', 'mcp:s1:old', 'fetch_url'] }))
    await userEvent.click(screen.getByLabelText('read_file'))
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ tools_allowed: ['mcp:s1:old'] }))
  })
})
