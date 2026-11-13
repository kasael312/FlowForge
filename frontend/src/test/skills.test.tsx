import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { FlowNode, Skill, StepData } from '../api/types'
import { StepSidebar } from '../components/StepSidebar'
import { renderMarkdown } from '../lib/markdown'
import { SkillsContext } from '../lib/SkillsContext'
import { defaultStepData } from '../lib/steps'
import { SkillsPage } from '../pages/SkillsPage'

const skill = (over: Partial<Skill> = {}): Skill => ({
  id: 's1',
  name: 'python-testing',
  kind: 'skill',
  description: 'How to test',
  content: '# Testing\n\n- use pytest',
  tools_required: ['run_shell'],
  files: [{ path: 'patterns.md', size: 120, content_type: 'text/markdown' }],
  builtin: true,
  skill_md: '---\nname: python-testing\n---\n# Testing',
  created_at: '2026-11-10T00:00:00Z',
  updated_at: '2026-11-10T00:00:00Z',
  ...over,
})

describe('renderMarkdown', () => {
  it('renders headings, lists, code and escapes html', () => {
    const html = renderMarkdown('# Title\n\nSome **bold** `code` <b>x</b>\n\n- a\n- [x] done\n\n1. one\n\n```\nraw <tag>\n```')
    expect(html).toContain('<h1>Title</h1>')
    expect(html).toContain('<strong>bold</strong>')
    expect(html).toContain('<code>code</code>')
    expect(html).toContain('&lt;b&gt;x&lt;/b&gt;')
    expect(html).toContain('<ul>\n<li>a</li>\n<li><input type="checkbox" disabled checked> done</li>\n</ul>')
    expect(html).toContain('<ol>\n<li>one</li>\n</ol>')
    expect(html).toContain('<pre><code>raw &lt;tag&gt;</code></pre>')
  })
})

describe('StepSidebar skill picker', () => {
  function Harness({ node, spy }: { node: FlowNode; spy: (d: StepData) => void }) {
    const [current, setCurrent] = useState(node)
    return (
      <SkillsContext.Provider value={{ skills: [skill(), skill({ id: 's2', name: 'git-workflow', builtin: false })], reload: async () => {} }}>
        <StepSidebar
          node={current}
          nodes={[current]}
          onChange={(d) => {
            spy(d)
            setCurrent({ ...current, data: d })
          }}
          onChangeType={vi.fn()}
          onDelete={vi.fn()}
        />
      </SkillsContext.Provider>
    )
  }

  it('toggles skill ids on LLM steps', async () => {
    const spy = vi.fn()
    render(<Harness node={{ id: 'c1', type: 'step', position: { x: 0, y: 0 }, data: defaultStepData('custom') }} spy={spy} />)
    const picker = screen.getByTestId('skill-picker')
    await userEvent.click(within(picker).getByLabelText(/python-testing/))
    expect((spy.mock.lastCall![0] as StepData).skill_ids).toEqual(['s1'])
    await userEvent.click(within(picker).getByLabelText(/git-workflow/))
    expect((spy.mock.lastCall![0] as StepData).skill_ids).toEqual(['s1', 's2'])
    await userEvent.click(within(picker).getByLabelText(/python-testing/))
    expect((spy.mock.lastCall![0] as StepData).skill_ids).toEqual(['s2'])
  })

  it('selects a single persona separately from skills', async () => {
    const spy = vi.fn()
    function PHarness() {
      const [current, setCurrent] = useState<FlowNode>({ id: 'c1', type: 'step', position: { x: 0, y: 0 }, data: defaultStepData('custom') })
      return (
        <SkillsContext.Provider value={{ skills: [skill(), skill({ id: 'p1', name: 'researcher', kind: 'persona' }), skill({ id: 'p2', name: 'copywriter', kind: 'persona' })], reload: async () => {} }}>
          <StepSidebar node={current} nodes={[current]} onChange={(d) => { spy(d); setCurrent({ ...current, data: d }) }} onChangeType={vi.fn()} onDelete={vi.fn()} />
        </SkillsContext.Provider>
      )
    }
    render(<PHarness />)
    await userEvent.selectOptions(screen.getByLabelText(/Persona/), 'p1')
    expect((spy.mock.lastCall![0] as StepData).skill_ids).toEqual(['p1'])
    await userEvent.click(within(screen.getByTestId('skill-picker')).getByLabelText(/python-testing/))
    expect((spy.mock.lastCall![0] as StepData).skill_ids).toEqual(['p1', 's1'])
    await userEvent.selectOptions(screen.getByLabelText(/Persona/), 'p2')
    expect((spy.mock.lastCall![0] as StepData).skill_ids).toEqual(['p2', 's1'])
    expect(within(screen.getByTestId('skill-picker')).queryByLabelText(/researcher/)).toBeNull()
  })

  it('hides the picker for non-LLM steps', () => {
    render(<Harness node={{ id: 'sh', type: 'step', position: { x: 0, y: 0 }, data: defaultStepData('shell') }} spy={vi.fn()} />)
    expect(screen.queryByTestId('skill-picker')).toBeNull()
  })
})

describe('SkillsPage', () => {
  const fetchMock = vi.fn<typeof fetch>()
  const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }))
  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('lists skills, shows a read-only built-in with preview, creates a new one', async () => {
    const mine: Skill[] = []
    fetchMock.mockImplementation((input, init) => {
      const url = String(input)
      if (url.endsWith('/skills') && init?.method === 'POST') {
        const body = JSON.parse(String(init.body)) as { name: string; content: string; tools_required: string[] }
        const created = skill({ id: 'new1', name: body.name, content: body.content, tools_required: body.tools_required, builtin: false, files: [] })
        mine.push(created)
        return json(created, 201)
      }
      if (url.endsWith('/skills')) return json([skill(), ...mine])
      return json({ detail: 'unexpected' }, 500)
    })
    render(<SkillsPage />)
    const row = await screen.findByRole('button', { name: /python-testing/ })
    await userEvent.click(row)
    expect(screen.getByText('built-in · read-only')).toBeInTheDocument()
    expect(screen.getByLabelText('SKILL.md content')).toBeDisabled()
    expect(screen.getByTestId('skill-preview').innerHTML).toContain('<h1>Testing</h1>')
    expect(within(screen.getByTestId('skill-files')).getByText('patterns.md')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull()

    await userEvent.click(screen.getByRole('button', { name: 'New skill' }))
    await userEvent.type(screen.getByLabelText(/^Name/), 'my-tone')
    await userEvent.type(screen.getByLabelText(/Tools required/), 'web_search, scrape')
    const content = screen.getByLabelText('SKILL.md content')
    await userEvent.clear(content)
    await userEvent.type(content, '# Tone')
    expect(screen.getByTestId('skill-preview').innerHTML).toContain('<h1>Tone</h1>')
    await userEvent.click(screen.getByRole('button', { name: 'Create' }))
    await waitFor(() => expect(screen.getByRole('button', { name: /my-tone/ })).toBeInTheDocument())
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')!
    expect(JSON.parse(String(post[1]!.body))).toEqual({ name: 'my-tone', kind: 'skill', description: '', content: '# Tone', tools_required: ['web_search', 'scrape'] })
    expect(screen.getByRole('button', { name: 'Save' })).toBeInTheDocument()
  })
})
