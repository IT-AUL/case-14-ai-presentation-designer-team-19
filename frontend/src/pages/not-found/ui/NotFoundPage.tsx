import { EmptyState, PageHeader } from '@/shared/ui'
import styles from './NotFoundPage.module.css'

export function NotFoundPage() {
  return (
    <div className={styles.page}>
      <PageHeader title="Страница не найдена" />
      <EmptyState title="Страница не найдена" />
    </div>
  )
}
