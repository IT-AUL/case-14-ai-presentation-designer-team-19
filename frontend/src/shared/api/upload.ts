import { ApiError } from './errors'

interface UploadOptions {
  url: string
  form: FormData
  onProgress?: (fraction: number) => void
  signal?: AbortSignal
}

export function uploadWithProgress<T>({ url, form, onProgress, signal }: UploadOptions): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', url)
    xhr.responseType = 'json'
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress?.(event.loaded / event.total)
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve(xhr.response as T)
      else reject(ApiError.fromResponse(xhr.status, xhr.response))
    }
    xhr.onerror = () => reject(new ApiError({ code: 'network_error', message: 'Сервис недоступен', status: 0, retryable: true }))
    signal?.addEventListener('abort', () => xhr.abort())
    xhr.send(form)
  })
}
