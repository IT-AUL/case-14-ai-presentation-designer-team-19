import { abortable } from './abortable'

it('keeps fulfilled and rejected upload results', async () => {
  const controller = new AbortController()
  await expect(abortable(Promise.resolve(1), controller.signal)).resolves.toBe(1)
  await expect(abortable(Promise.reject(new Error('upload')), controller.signal)).rejects.toThrow('upload')
  await expect(abortable(Promise.resolve(2))).resolves.toBe(2)
})
it('settles even when the upload helper does not reject on xhr.abort', async () => {
  const controller = new AbortController()
  const request = abortable(new Promise(() => undefined), controller.signal)
  controller.abort()
  await expect(request).rejects.toHaveProperty('name', 'AbortError')
})
it('handles an already aborted signal', async () => {
  const controller = new AbortController()
  controller.abort()
  await expect(abortable(Promise.resolve(1), controller.signal)).rejects.toHaveProperty('name', 'AbortError')
})
