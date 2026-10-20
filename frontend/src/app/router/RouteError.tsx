import { isRouteErrorResponse, Link, useRouteError } from 'react-router'
import { humanError } from '@/shared/api'
import { routes } from '@/shared/config'
import { EmptyState } from '@/shared/ui'
import styles from './RouteError.module.css'

export function RouteError() {
  const error = useRouteError()
  const message = isRouteErrorResponse(error)
    ? `${error.status} ${error.statusText}`
    : humanError(error, 'Неизвестная ошибка')

  return (
    <div className={styles.root}>
      <EmptyState
        title="Что-то пошло не так"
        description={message}
        actions={<Link to={routes.projects()}>К проектам</Link>}
      />
    </div>
  )
}
