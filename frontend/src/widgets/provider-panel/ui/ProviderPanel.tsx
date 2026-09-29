import { useState } from 'react'
import {
  ALLOWED_MODEL_LICENSES,
  capabilityLabel,
  MODEL_PARAMETER_LIMIT_B,
  useActiveProviderSession,
  useCreateProviderSession,
  useDeleteProviderSession,
  useTestProviderSession,
} from '@/entities/provider-session'
import type { ActiveProviderSession, ProbeStatus } from '@/entities/provider-session'
import { FEATURE_PATHS, useCapabilityFlag } from '@/entities/system'
import { formatDateTime } from '@/shared/lib/format'
import { Button, Drawer } from '@/shared/ui'
import { EMPTY_PROVIDER_FORM, providerFormFromSession, toProviderSessionCreate } from '../model/form'
import type { ProviderForm } from '../model/form'
import { probeDetail } from '../model/probes'
import { ProviderFormView } from './ProviderForm'
import styles from './ProviderPanel.module.css'

interface ProviderPanelProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

function errorText(error: unknown): string | null {
  if (!error) return null
  return error instanceof Error ? error.message : String(error)
}

function ModelRule() {
  return (
    <div className={styles.rule}>
      <span className={styles.ruleTitle}>
        Только открытые модели · {ALLOWED_MODEL_LICENSES.join(' или ')} · до {MODEL_PARAMETER_LIMIT_B.text}B
      </span>
      <span>
        Изображения — до {MODEL_PARAMETER_LIMIT_B.image}B. Закрытые API не используются.
      </span>
    </div>
  )
}

const STATUS_TEXT: Record<ProbeStatus, string> = { ok: 'ok', fail: 'fail', skip: 'skip' }

function SessionSummary({
  session,
  testing,
  testError,
  disconnecting,
  disconnectError,
  onTest,
  onEdit,
  onDisconnect,
}: {
  session: ActiveProviderSession
  testing: boolean
  testError: string | null
  disconnecting: boolean
  disconnectError: string | null
  onTest: () => void
  onEdit: () => void
  onDisconnect: () => void
}) {
  const rows: [string, string | null | undefined, boolean][] = [
    ['Подключение', session.label, false],
    ['Адрес API', session.baseUrl, true],
    ['Текст', session.models.text, true],
    ['Зрение', session.models.vision, true],
    ['Эмбеддинги', session.models.embedding, true],
    ['Изображения', session.models.image, true],
  ]
  const results = session.lastTest?.results ?? []

  return (
    <>
      <section className={styles.summary} aria-label="Активное подключение">
        <div className={styles.summaryHead}>
          <span className={styles.statusDot} aria-hidden="true" />
          <span className={styles.summaryTitle}>Подключено</span>
          <span className={styles.summaryMeta}>до {formatDateTime(session.expiresAt)}</span>
        </div>
        <dl className={styles.details}>
          {rows
            .filter(([, value]) => Boolean(value))
            .map(([label, value, mono]) => (
              <div key={label} className={styles.detailRow}>
                <dt>{label}</dt>
                <dd className={mono ? styles.mono : undefined}>{value}</dd>
              </div>
            ))}
          <div className={styles.detailRow}>
            <dt>Токен</dt>
            <dd>Передан сервису · не показывается</dd>
          </div>
        </dl>
      </section>
      <div className={styles.actions}>
        <Button variant="secondary" onClick={onTest} disabled={testing}>
          {testing ? 'Проверяю…' : 'Проверить подключение'}
        </Button>
        <Button variant="ghost" onClick={onEdit}>
          Изменить
        </Button>
        <Button variant="ghost" onClick={onDisconnect} disabled={disconnecting}>
          {disconnecting ? 'Отключаю…' : 'Отключить'}
        </Button>
      </div>
      {testError && (
        <p className={styles.formError} role="alert">
          Проверка не удалась: {testError}
        </p>
      )}
      {disconnectError && (
        <p className={styles.formError} role="alert">
          Не удалось отключить: {disconnectError}
        </p>
      )}
      {results.length > 0 && (
        <section className={styles.probes} aria-label="Проверка возможностей">
          <div className={styles.probesHead}>
            <span className={styles.probesTitle}>Проверка возможностей</span>
            {session.lastTest && <span className={styles.summaryMeta}>{formatDateTime(session.lastTest.tested_at)}</span>}
          </div>
          <ul className={styles.probeList}>
            {results.map((probe) => (
              <li key={probe.capability} className={styles.probe}>
                <span className={styles.probeStatus} data-status={probe.status}>
                  {STATUS_TEXT[probe.status]}
                </span>
                <span className={styles.probeText}>
                  <span className={styles.probeLabel}>{capabilityLabel(probe.capability)}</span>
                  <span className={styles.probeDetail}>{probeDetail(probe)}</span>
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  )
}

function ProviderPanelBody() {
  const session = useActiveProviderSession()
  const modelAuto = useCapabilityFlag(FEATURE_PATHS.modelAuto)
  const [editing, setEditing] = useState(false)
  const create = useCreateProviderSession()
  const test = useTestProviderSession()
  const disconnect = useDeleteProviderSession()

  const connect = (form: ProviderForm) => {
    const previous = session
    create.mutate(toProviderSessionCreate(form), {
      onSuccess: (created) => {
        setEditing(false)
        test.mutate(created.id)
        if (previous && previous.id !== created.id) disconnect.mutate(previous.id)
      },
    })
  }

  if (!session || editing) {
    return (
      <>
        <ModelRule />
        {modelAuto && !session && (
          <div className={styles.serverModel}>
            <span className={styles.serverModelDot} />
            <div>
              <strong>Модель сервера подключена</strong>
              <p>Сервер использует встроенную языковую модель. Своя модель не обязательна, но вы можете подключить другую ниже.</p>
            </div>
          </div>
        )}
        <ProviderFormView
          initial={session ? providerFormFromSession(session) : EMPTY_PROVIDER_FORM}
          submitting={create.isPending}
          error={errorText(create.error)}
          replacing={Boolean(session)}
          onSubmit={connect}
          onCancel={session ? () => setEditing(false) : undefined}
        />
      </>
    )
  }

  return (
    <>
      <ModelRule />
      <SessionSummary
        session={session}
        testing={test.isPending}
        testError={errorText(test.error)}
        disconnecting={disconnect.isPending}
        disconnectError={errorText(disconnect.error)}
        onTest={() => test.mutate(session.id)}
        onEdit={() => setEditing(true)}
        onDisconnect={() => disconnect.mutate(session.id)}
      />
    </>
  )
}

export function ProviderPanel({ open, onOpenChange }: ProviderPanelProps) {
  return (
    <Drawer open={open} onOpenChange={onOpenChange} title="Провайдер моделей">
      <ProviderPanelBody />
    </Drawer>
  )
}
