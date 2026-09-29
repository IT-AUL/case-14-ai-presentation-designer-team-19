import { createContext, useContext } from 'react'

export type ToastTone = 'info' | 'error'

export interface ToastOptions {
  tone?: ToastTone
}

export interface ToastApi {
  show: (message: string, options?: ToastOptions) => void
}

export const ToastContext = createContext<ToastApi | null>(null)

export function useToast(): ToastApi {
  const context = useContext(ToastContext)
  if (!context) throw new Error('useToast must be used within ToastProvider')
  return context
}
