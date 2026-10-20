import { describe, expect, it } from 'vitest'
import { ApiError, humanError } from './errors'

describe('humanError', () => {
  it('turns browser network failures into plain Russian', () => {
    expect(humanError(new TypeError('Failed to fetch'))).toBe('Нет связи с сервером. Проверьте соединение и попробуйте ещё раз.')
  })

  it('hides raw English backend messages', () => {
    expect(humanError(new ApiError({ code: 'not_found', message: "Generation 'run_1' not found", status: 404 }))).toBe(
      'Не нашёл эти данные. Обновите страницу или вернитесь на шаг назад.',
    )
    expect(humanError(new ApiError({ code: 'x', message: 'HTTP 502', status: 502 }))).toBe('Сервер не справился. Попробуйте ещё раз через минуту.')
    expect(humanError(new ApiError({ code: 'x', message: 'Validation error', status: 422 }), 'Не получилось.')).toBe('Не получилось.')
  })

  it('hides English detail even when it quotes a Russian filename', () => {
    expect(humanError(new ApiError({ code: 'x', message: 'File "Инструкция к запуску.pptx" not found in project', status: 400 }), 'Не получилось.')).toBe(
      'Не получилось.',
    )
  })

  it('keeps an explanation the service wrote in Russian', () => {
    expect(humanError(new ApiError({ code: 'x', message: 'Бриф не прошёл проверку', status: 422 }))).toBe('Бриф не прошёл проверку')
  })
})
