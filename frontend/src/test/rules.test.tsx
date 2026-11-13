import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ResolvedPrompt, Rule } from '../api/types'
import { ResolvedPromptModal } from '../components/ResolvedPromptModal'
import { RulesEditor } from '../components/RulesEditor'

const rule = (over: Partial<Rule>): Rule => ({
  id: 'r1',
  key: null,
  name: 'Rule',
  content: 'text',
  scope: 'global',
  flow_id: null,
  step_id: null,
  priority: 100,
  enabled: true,
  created_at: '2026-11-10T00:00:00Z',
  updated_at: '2026-11-10T00:00:00Z',
  ...over,
})

const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }))

describe('RulesEditor', () => {
  const fetchMock = vi.fn<typeof fetch>()
  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('lists global rules, toggles and adds one', async () => {
    const rows: Rule[] = [rule({ id: 'b1', key: 'no-secrets', name: 'No secrets', priority: 10 }), rule({ id: 'b2', key: 'vietnamese-comments', name: 'Vietnamese comments', enabled: false })]
    fetchMock.mockImplementation((input, init) => {
      const url = String(input)
      if (init?.method === 'PUT') {
        const body = JSON.parse(String(init.body)) as Partial<Rule>
        const i = rows.findIndex((r) => url.endsWith(`/rules/${r.id}`))
        rows[i] = { ...rows[i]!, ...body }
        return json(rows[i])
      }
      if (init?.method === 'POST') {
        const body = JSON.parse(String(init.body)) as Partial<Rule>
        rows.push(rule({ id: 'n1', ...body }))
        return json(rows.at(-1), 201)
      }
      expect(url).toMatch(/\/rules\?scope=global$/)
      return json(rows)
    })
    render(<RulesEditor scope="global" />)
    const list = await screen.findByTestId('rules-global')
    expect(await within(list).findAllByText('built-in')).toHaveLength(2)
    await userEvent.click(within(list).getByLabelText('Enable Vietnamese comments'))
    await waitFor(() => expect(rows[1]!.enabled).toBe(true))

    await userEvent.click(within(list).getByRole('button', { name: 'Add' }))
    await userEvent.type(screen.getByLabelText('Name'), 'Be terse')
    await userEvent.type(screen.getByLabelText('Rule text'), 'Three lines max.')
    await userEvent.click(screen.getByRole('button', { name: 'Add rule' }))
    await waitFor(() => expect(within(screen.getByTestId('rules-global')).getByText('Be terse')).toBeInTheDocument())
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')!
    expect(JSON.parse(String(post[1]!.body))).toEqual({ name: 'Be terse', content: 'Three lines max.', scope: 'global', flow_id: null, step_id: null, priority: 100, enabled: true })
  })

  it('adds a step rule inside a flow', async () => {
    const rows: Rule[] = [rule({ id: 'f1', name: 'Flow rule', scope: 'flow', flow_id: 'flow1' })]
    fetchMock.mockImplementation((input, init) => {
      if (init?.method === 'POST') {
        const body = JSON.parse(String(init.body)) as Partial<Rule>
        rows.push(rule({ id: 's1', ...body }))
        return json(rows.at(-1), 201)
      }
      expect(String(input)).toMatch(/\/rules\?flow_id=flow1$/)
      return json(rows)
    })
    render(<RulesEditor scope="flow+step" flowId="flow1" steps={[{ id: 'plan', label: 'Plan' }, { id: 'code', label: 'Code' }]} initialStepId="code" />)
    expect(await within(await screen.findByTestId('rules-flow')).findByText('Flow rule')).toBeInTheDocument()
    await userEvent.click(within(screen.getByTestId('rules-step')).getByRole('button', { name: 'Add' }))
    expect(screen.getByLabelText('Step')).toHaveValue('code')
    await userEvent.type(screen.getByLabelText('Name'), 'Only tests')
    await userEvent.click(screen.getByRole('button', { name: 'Add rule' }))
    await waitFor(() => expect(within(screen.getByTestId('rules-step')).getByText('Only tests')).toBeInTheDocument())
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')!
    expect(JSON.parse(String(post[1]!.body))).toMatchObject({ scope: 'step', flow_id: 'flow1', step_id: 'code' })
    expect(within(screen.getByTestId('rules-step')).getByText('step: Code')).toBeInTheDocument()
  })
})

describe('ResolvedPromptModal', () => {
  const fetchMock = vi.fn<typeof fetch>()
  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('shows sections in order and the raw prompt', async () => {
    const data: ResolvedPrompt = {
      step_id: 'code',
      system_prompt: 'BASE\n\nRULE\n\nPERSONA\n\nSTEP',
      sections: [
        { kind: 'base', name: 'Base rules', text: 'BASE' },
        { kind: 'rule', name: 'No secrets (global)', text: 'RULE' },
        { kind: 'persona', name: 'researcher', text: 'PERSONA' },
        { kind: 'step', name: 'Step prompt (code)', text: 'STEP' },
      ],
    }
    fetchMock.mockImplementation((input) => {
      expect(String(input)).toMatch(/\/flows\/flow1\/steps\/code\/resolved-prompt$/)
      return json(data)
    })
    render(<ResolvedPromptModal flowId="flow1" stepId="code" onClose={vi.fn()} />)
    const panel = await screen.findByTestId('resolved-prompt')
    expect(within(panel).getAllByRole('listitem').map((li) => li.textContent)).toEqual(['baseBase rulesBASE', 'ruleNo secrets (global)RULE', 'personaresearcherPERSONA', 'stepStep prompt (code)STEP'])
    await userEvent.click(screen.getByRole('button', { name: 'Raw' }))
    expect(within(panel).getByText(/BASE\s+RULE\s+PERSONA\s+STEP/)).toBeInTheDocument()
  })
})
