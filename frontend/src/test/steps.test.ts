import { describe, expect, it } from 'vitest'
import type { FlowEdge, FlowNode } from '../api/types'
import { defaultStepData, dropDanglingGotos, parseOnFail, reconcileEdgesForType } from '../lib/steps'

describe('steps helpers', () => {
  it('parses on_fail', () => {
    expect(parseOnFail('retry')).toEqual({ mode: 'retry', target: '' })
    expect(parseOnFail('goto:code')).toEqual({ mode: 'goto', target: 'code' })
    expect(parseOnFail('weird')).toEqual({ mode: 'stop', target: '' })
  })

  it('reconciles edges when a node becomes a condition and back', () => {
    const edges: FlowEdge[] = [
      { id: '1', source: 'a', target: 'b', sourceHandle: null },
      { id: '2', source: 'a', target: 'c', sourceHandle: null },
      { id: '3', source: 'a', target: 'd', sourceHandle: null },
      { id: '4', source: 'z', target: 'a', sourceHandle: null },
    ]
    const cond = reconcileEdgesForType('a', 'condition', edges)
    const fromA = cond.filter((e) => e.source === 'a')
    expect(fromA.map((e) => e.sourceHandle)).toEqual(['true', 'false'])
    expect(cond.find((e) => e.id === '4')?.sourceHandle).toBeNull()
    const back = reconcileEdgesForType('a', 'code', cond)
    expect(back.filter((e) => e.source === 'a').every((e) => e.sourceHandle === null)).toBe(true)
  })

  it('drops gotos pointing at deleted nodes', () => {
    const nodes: FlowNode[] = [
      { id: 'a', type: 'step', position: { x: 0, y: 0 }, data: { ...defaultStepData('code'), on_fail: 'goto:gone' } },
      { id: 'b', type: 'step', position: { x: 0, y: 0 }, data: { ...defaultStepData('code'), on_fail: 'goto:a' } },
    ]
    const out = dropDanglingGotos(nodes)
    expect(out[0]!.data.on_fail).toBe('stop')
    expect(out[1]!.data.on_fail).toBe('goto:a')
  })
})
