import * as Dialog from '@radix-ui/react-dialog'
import { useState } from 'react'
import { Button, Icon, RefreshCw } from '@/shared/ui'
import styles from './RebuildDialog.module.css'

interface RebuildDialogProps {
  label: string
  title: string
  description: string
  confirmLabel: string
  disabled?: boolean
  pending?: boolean
  onConfirm: () => void
}

export function RebuildDialog({ label, title, description, confirmLabel, disabled, pending, onConfirm }: RebuildDialogProps) {
  const [open, setOpen] = useState(false)

  const confirm = () => {
    setOpen(false)
    onConfirm()
  }

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger asChild>
        <Button variant="secondary" size="lg" disabled={disabled}>
          <Icon as={RefreshCw} />
          {pending ? 'Запускаем…' : label}
        </Button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className={styles.overlay} />
        <Dialog.Content className={styles.content}>
          <Dialog.Title className={styles.title}>{title}</Dialog.Title>
          <Dialog.Description className={styles.description}>{description}</Dialog.Description>
          <div className={styles.actions}>
            <Dialog.Close asChild>
              <Button size="lg">Отмена</Button>
            </Dialog.Close>
            <Button variant="primary" size="lg" onClick={confirm}>
              {confirmLabel}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
