import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { InputField } from '../api/types'
import { InputForm, defaultValues, validateValues, type InputValues } from '../components/InputForm'
import { InputSchemaEditor } from '../components/InputSchemaEditor'

const schema: InputField[] = [
  { name: 'topic', label: 'Topic', type: 'text', required: true, default: null, options: [], placeholder: 'e.g. AI', description: 'What to research' },
  { name: 'depth', label: 'Depth', type: 'select', required: false, default: 'quick', options: ['quick', 'deep'], placeholder: '', description: '' },
  { name: 'count', label: '', type: 'number', required: false, default: 3, options: [], placeholder: '', description: '' },
  { name: 'doc', label: 'Document', type: 'file', required: false, default: null, options: [], placeholder: '', description: '' },
  { name: 'verbose', label: 'Verbose', type: 'boolean', required: false, default: null, options: [], placeholder: '', description: '' },
]

function Host({ onValues }: { onValues: (v: InputValues) => void }) {
  const [values, setValues] = useState<InputValues>(defaultValues(schema))
  return (
    <InputForm
      schema={schema}
      values={values}
      onChange={(v) => {
        setValues(v)
        onValues(v)
      }}
    />
  )
}

describe('InputForm', () => {
  const fetchMock = vi.fn<typeof fetch>()
  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })

  it('renders every field type with defaults, edits values and uploads files', async () => {
    fetchMock.mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ id: 'up1', name: 'a.pdf', size: 10, content_type: 'application/pdf', created_at: '' }), { status: 201, headers: { 'content-type': 'application/json' } })))
    const spy = vi.fn()
    render(<Host onValues={spy} />)
    expect(defaultValues(schema)).toEqual({ depth: 'quick', count: 3, verbose: false })
    expect(screen.getByLabelText(/Topic/)).toHaveAttribute('placeholder', 'e.g. AI')
    expect(screen.getByText('What to research')).toBeInTheDocument()
    expect(screen.getByLabelText('Depth')).toHaveValue('quick')
    expect(screen.getByLabelText('count')).toHaveValue(3)

    await userEvent.type(screen.getByLabelText(/Topic/), 'LLM agents')
    await userEvent.selectOptions(screen.getByLabelText('Depth'), 'deep')
    await userEvent.click(screen.getByLabelText('Verbose'))
    await userEvent.upload(screen.getByLabelText('Document'), new File(['x'], 'a.pdf', { type: 'application/pdf' }))
    expect(await screen.findByText('✔ a.pdf')).toBeInTheDocument()
    expect(spy).toHaveBeenLastCalledWith({ depth: 'deep', count: 3, verbose: true, topic: 'LLM agents', doc: { upload_id: 'up1', name: 'a.pdf', size: 10 } })
    const upload = fetchMock.mock.calls[0]!
    expect(String(upload[0])).toMatch(/\/uploads$/)
    expect((upload[1] as RequestInit).body).toBeInstanceOf(FormData)
  })

  it('validates required, number and url fields', () => {
    expect(validateValues(schema, {})).toBe('Topic is required')
    expect(validateValues(schema, { topic: 'x', count: 'abc' })).toBe('count must be a number')
    const withUrl: InputField[] = [{ ...schema[0]!, name: 'site', label: 'Site', type: 'url' }]
    expect(validateValues(withUrl, { site: 'example.com' })).toBe('Site must be a URL')
    expect(validateValues(withUrl, { site: 'https://example.com' })).toBeNull()
  })
})

describe('InputSchemaEditor', () => {
  it('adds, edits, reorders and removes fields', async () => {
    function Host2({ spy }: { spy: (s: InputField[]) => void }) {
      const [s, setS] = useState<InputField[]>([])
      return (
        <InputSchemaEditor
          schema={s}
          onChange={(n) => {
            setS(n)
            spy(n)
          }}
        />
      )
    }
    const spy = vi.fn()
    render(<Host2 spy={spy} />)
    expect(screen.getByText('No inputs yet.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Add field' }))
    await userEvent.click(screen.getByRole('button', { name: 'Add field' }))
    await userEvent.clear(screen.getByLabelText('Name', { selector: '#f-name-0' }))
    await userEvent.type(screen.getByLabelText('Name', { selector: '#f-name-0' }), 'my topic!')
    await userEvent.selectOptions(screen.getByLabelText('Type', { selector: '#f-type-1' }), 'select')
    await userEvent.type(screen.getByLabelText(/Options/), 'a, b ,c')
    await userEvent.click(screen.getAllByLabelText('Move up')[1]!)
    let last = spy.mock.lastCall![0] as InputField[]
    expect(last.map((f) => f.name)).toEqual(['field2', 'my_topic_'])
    expect(last[0]!.type).toBe('select')
    expect(last[0]!.options).toEqual(['a', 'b', 'c'])
    await userEvent.click(screen.getAllByRole('button', { name: 'Remove' })[0]!)
    last = spy.mock.lastCall![0] as InputField[]
    expect(last.map((f) => f.name)).toEqual(['my_topic_'])
  })
})
