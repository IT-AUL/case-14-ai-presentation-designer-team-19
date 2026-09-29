import { useCreateExport } from '@/entities/passport'
import { Button, useToast } from '@/shared/ui'
import styles from './RequestPdfExport.module.css'

interface RequestPdfExportProps {
  variantId: string
  variantName: string
  size?: 'sm' | 'md'
}

export function RequestPdfExport({ variantId, variantName, size = 'sm' }: RequestPdfExportProps) {
  const toast = useToast()
  const createExport = useCreateExport(variantId)
  const result = createExport.data

  if (result?.status === 'unavailable') {
    return (
      <p className={styles.note} role="status">
        PDF для этой ревизии пока недоступен: {result.message}
      </p>
    )
  }

  const request = () =>
    createExport.mutate(['pdf'], {
      onSuccess: (outcome) => {
        if (outcome.status === 'created') toast.show(`PDF «${variantName}» создан, превью обновится`)
      },
      onError: (error) => toast.show(`Не удалось создать PDF: ${error.message}`),
    })

  return (
    <Button size={size} onClick={request} disabled={createExport.isPending} aria-busy={createExport.isPending}>
      {createExport.isPending ? 'Создаём PDF…' : 'Создать PDF для превью'}
    </Button>
  )
}
