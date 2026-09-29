import { useId } from 'react'
import { useCancelGeneration } from '@/entities/generation'
import { Button, Icon, useToast, X } from '@/shared/ui'
import styles from './CancelGenerationButton.module.css'

interface CancelGenerationButtonProps {
  generationId: string | null
  onCanceled?: () => void
}

const WAITING_HINT = 'Отменить можно, когда сервис примет запуск — обычно это пара секунд'

export function CancelGenerationButton({ generationId, onCanceled }: CancelGenerationButtonProps) {
  const cancel = useCancelGeneration()
  const toast = useToast()
  const hintId = useId()

  if (!generationId) {
    return (
      <span className={styles.wrap} title={WAITING_HINT}>
        <Button size="lg" disabled aria-describedby={hintId}>
          <Icon as={X} />
          Отменить
        </Button>
        <span id={hintId} className={styles.srOnly}>
          {WAITING_HINT}
        </span>
      </span>
    )
  }

  const handleClick = () => {
    cancel.mutate(generationId, {
      onSuccess: () => {
        toast.show('Генерация отменена. Бриф сохранён.')
        onCanceled?.()
      },
      onError: (error) => toast.show(error.message, { tone: 'error' }),
    })
  }

  return (
    <Button size="lg" onClick={handleClick} disabled={cancel.isPending}>
      <Icon as={X} />
      {cancel.isPending ? 'Отменяем…' : 'Отменить'}
    </Button>
  )
}
