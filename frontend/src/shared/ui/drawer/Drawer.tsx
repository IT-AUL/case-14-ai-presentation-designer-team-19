import * as Dialog from '@radix-ui/react-dialog'
import type { ReactNode } from 'react'
import styles from './Drawer.module.css'
import { Icon, X } from '../icon'

interface DrawerProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: string
  children: ReactNode
}

export function Drawer({ open, onOpenChange, title, description, children }: DrawerProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className={styles.overlay} />
        <Dialog.Content className={styles.content}>
          <header className={styles.header}>
            <Dialog.Title className={styles.title}>{title}</Dialog.Title>
            <Dialog.Close className={styles.close} aria-label="Закрыть">
              <Icon as={X} size={16} />
            </Dialog.Close>
          </header>
          {description && <Dialog.Description className={styles.description}>{description}</Dialog.Description>}
          <div className={styles.body}>{children}</div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
