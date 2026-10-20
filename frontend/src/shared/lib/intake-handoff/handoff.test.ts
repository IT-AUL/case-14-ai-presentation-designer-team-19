import { intakeHandoffState, readIntakeHandoff } from './handoff'

describe('intake handoff', () => {
  it('round-trips the topic and files through router state', () => {
    const file = new File(['x'], 'brand.pptx')
    expect(readIntakeHandoff(intakeHandoffState({ idea: 'Итоги года', files: [file] }))).toEqual({ idea: 'Итоги года', files: [file] })
  })

  it('ignores empty or foreign state', () => {
    expect(readIntakeHandoff(null)).toBeNull()
    expect(readIntakeHandoff({ other: 1 })).toBeNull()
    expect(readIntakeHandoff(intakeHandoffState({ idea: '  ', files: [] }))).toBeNull()
  })
})
