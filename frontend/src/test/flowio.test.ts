import { describe, expect, it } from 'vitest'
import { ImportError, exportFilename, parseImport, toExport } from '../lib/flowio'
import { defaultStepData } from '../lib/steps'

describe('flow import/export', () => {
  it('round-trips an exported flow', () => {
    const flow = {
      name: 'Fix issue',
      description: 'd',
      nodes: [
        { id: 'a', type: 'step', position: { x: 0, y: 0 }, data: defaultStepData('plan') },
        { id: 'c', type: 'step', position: { x: 1, y: 1 }, data: { ...defaultStepData('condition'), expression: 'contains("ok")' } },
      ],
      edges: [{ id: 'e', source: 'a', target: 'c', sourceHandle: null }],
    }
    const doc = toExport(flow)
    expect(doc.flowforge).toBe(1)
    const back = parseImport(JSON.stringify(doc))
    expect(back).toEqual({ ...flow, input_schema: [] })
    expect(exportFilename('Fix issue!')).toBe('fix-issue.flow.json')
  })

  it('fills defaults for minimal hand-written documents', () => {
    const back = parseImport(JSON.stringify({ nodes: [{ id: 'x', data: { type: 'custom', system_prompt: 'hi {{task}}' } }, { id: 'y' }], edges: [{ source: 'x', target: 'y', sourceHandle: 'bogus' }] }))
    expect(back.name).toBe('Imported flow')
    expect(back.nodes[0]!.data.type).toBe('custom')
    expect(back.nodes[0]!.data.max_turns).toBe(20)
    expect(back.nodes[1]!.data.type).toBe('code')
    expect(back.nodes[1]!.position).toEqual({ x: 260, y: 0 })
    expect(back.edges[0]).toEqual({ id: 'x->y', source: 'x', target: 'y', sourceHandle: null })
  })

  it('rejects malformed documents with readable errors', () => {
    expect(() => parseImport('{')).toThrow(ImportError)
    expect(() => parseImport('{"nodes": 1}')).toThrow(/nodes.*edges/)
    expect(() => parseImport('{"nodes":[{"id":"a"},{"id":"a"}],"edges":[]}')).toThrow(/unique/)
    expect(() => parseImport('{"nodes":[{"id":"a"}],"edges":[{"source":"a","target":"zz"}]}')).toThrow(/unknown node/)
  })
})
