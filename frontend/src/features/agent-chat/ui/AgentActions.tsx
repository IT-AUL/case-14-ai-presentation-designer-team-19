import { Check, X, LoaderCircle } from 'lucide-react'
import { Link, useSearchParams } from 'react-router'
import type { AgentAction, AgentStep } from '@/entities/assistant'
import styles from './AgentChat.module.css'

function safeUrl(url: string): string | undefined {
  try {
    const parsed = new URL(url)
    return ['https:', 'http:'].includes(parsed.protocol) ? parsed.href : undefined
  } catch {
    return undefined
  }
}

export function AgentActions({ actions }: { actions: readonly (AgentAction | AgentStep)[] }) {
  const [params] = useSearchParams()
  return (
    <ol className={styles.steps} aria-label="Шаги помощника">
      {actions.map((action, index) => {
        const pending = 'phase' in action && action.phase === 'started'
        const Icon = pending ? LoaderCircle : action.ok ? Check : X
        const slideParams = new URLSearchParams(params)
        if (action.slide !== null) slideParams.set('slide', String(action.slide))
        return (
          <li key={index} className={styles.step} data-state={pending ? 'running' : action.ok ? 'success' : 'failed'}>
            <Icon
              size={16}
              className={pending ? styles.spinner : undefined}
              aria-label={pending ? 'Выполняю' : action.ok ? 'Выполнено' : 'Не удалось'}
            />
            <div>
              <p>{pending ? action.text : action.summary || ('text' in action ? action.text : '')}</p>
              {action.slide !== null && (
                <Link className={styles.slideLink} to={`?${slideParams}`}>
                  Слайд {action.slide}
                </Link>
              )}
              {action.sources?.map((source, sourceIndex) => {
                const url = safeUrl(source.url)
                return url ? (
                  <a key={sourceIndex} className={styles.source} href={url} target="_blank" rel="noreferrer noopener">
                    <span>интернет</span> {source.title}
                    {source.publisher ? ` · ${source.publisher}` : ''}
                  </a>
                ) : null
              })}
            </div>
          </li>
        )
      })}
    </ol>
  )
}
