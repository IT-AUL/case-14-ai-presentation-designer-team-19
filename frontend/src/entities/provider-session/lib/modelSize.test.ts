import { SUGGESTED_MODELS } from '../model/models'
import { exceedsParameterLimit, modelParameterCountB } from './modelSize'

describe('model size rule', () => {
  it('extracts the parameter count from common model ids', () => {
    expect(modelParameterCountB('Qwen/Qwen3-32B')).toBe(32)
    expect(modelParameterCountB('openai/gpt-oss-120b')).toBe(120)
    expect(modelParameterCountB('Qwen/Qwen3-30B-A3B')).toBe(30)
    expect(modelParameterCountB('qwen2.5:0.5b')).toBe(0.5)
    expect(modelParameterCountB('intfloat/e5-large-335M')).toBeCloseTo(0.335)
    expect(modelParameterCountB('BAAI/bge-m3')).toBeNull()
  })

  it('flags models above the case limit', () => {
    expect(exceedsParameterLimit('Qwen/Qwen2.5-VL-72B-Instruct', 'vision')).toBe(true)
    expect(exceedsParameterLimit('Qwen/Qwen3-32B', 'text')).toBe(false)
    expect(exceedsParameterLimit('some-t2i-24B', 'image')).toBe(true)
    expect(exceedsParameterLimit('unknown-model', 'text')).toBe(false)
  })

  it('suggests only models that satisfy the limits and licenses', () => {
    for (const model of Object.values(SUGGESTED_MODELS)) {
      expect(exceedsParameterLimit(model.id, model.role)).toBe(false)
      expect(['Apache-2.0', 'MIT']).toContain(model.license)
    }
  })
})
