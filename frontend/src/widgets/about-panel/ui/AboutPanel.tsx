import type { ReactNode } from 'react'
import { CheckKindBadge, useRuleCatalog } from '@/entities/audit'
import { useCapabilities, useSkillManifest, useVersion } from '@/entities/system'
import { Badge, Drawer, Skeleton } from '@/shared/ui'
import { capabilityRows, countRules, describeManifest, formatRows, ruleGroups, versionRows } from '../lib/about'
import styles from './AboutPanel.module.css'

interface AboutPanelProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

function Block({ title, note, children }: { title: string; note?: ReactNode; children: ReactNode }) {
  return (
    <section className={styles.block} aria-label={title}>
      <h3 className={styles.blockTitle}>{title}</h3>
      {note && <p className={styles.note}>{note}</p>}
      {children}
    </section>
  )
}

function Failure({ error }: { error: Error }) {
  return (
    <p className={styles.error} role="alert">
      Сервис не ответил: {error.message}
    </p>
  )
}

function Loading() {
  return (
    <div className={styles.loading} aria-busy="true">
      <Skeleton className={styles.skeletonLine} />
      <Skeleton className={styles.skeletonLine} delay={0.1} />
      <Skeleton className={styles.skeletonLine} delay={0.2} />
    </div>
  )
}

function VersionBlock() {
  const { data, error, isPending } = useVersion()
  const rows = versionRows(data)
  return (
    <Block title="Версии">
      {isPending ? (
        <Loading />
      ) : error ? (
        <Failure error={error} />
      ) : (
        <dl className={styles.grid}>
          {rows.map((row) => (
            <div key={row.label} className={styles.gridRow}>
              <dt>{row.label}</dt>
              <dd className={styles.mono}>{row.value}</dd>
            </div>
          ))}
        </dl>
      )}
    </Block>
  )
}

function CapabilitiesBlock() {
  const { data, error, isPending } = useCapabilities()
  return (
    <Block title="Возможности сервиса">
      {isPending ? (
        <Loading />
      ) : error ? (
        <Failure error={error} />
      ) : (
        <>
          <ul className={styles.list}>
            {formatRows(data).map((row) => (
              <li key={row.label} className={styles.row}>
                <span className={styles.rowText}>
                  <span className={styles.rowLabel}>{row.label}</span>
                  <span className={styles.rowDetail}>{row.value}</span>
                </span>
                <Badge tone="ok">есть</Badge>
              </li>
            ))}
            {capabilityRows(data)
              .filter((row) => row.state === 'on')
              .map((row) => (
              <li key={row.id} className={styles.row}>
                <span className={styles.rowText}>
                  <span className={styles.rowLabel}>{row.label}</span>
                  <span className={styles.rowDetail}>{row.description}</span>
                </span>
                <Badge tone="ok">есть</Badge>
              </li>
              ))}
          </ul>
        </>
      )}
    </Block>
  )
}

function ManifestBlock() {
  const { data, error, isPending } = useSkillManifest()
  const manifest = describeManifest(data)
  return (
    <Block title="Скилл, промпты и конфиги" note="Каждая колода ссылается на эти версии в паспорте качества.">
      {isPending ? (
        <Loading />
      ) : error ? (
        <Failure error={error} />
      ) : (
        <>
          <div className={styles.skillHead}>
            <span className={styles.skillName}>{manifest.name ?? 'Скилл'}</span>
            {manifest.version && <span className={styles.version}>{manifest.version}</span>}
          </div>
          {manifest.description && <p className={styles.note}>{manifest.description}</p>}
          {manifest.meta.length > 0 && (
            <dl className={styles.grid}>
              {manifest.meta.map((row) => (
                <div key={row.label} className={styles.gridRow}>
                  <dt>{row.label}</dt>
                  <dd className={styles.mono}>{row.value}</dd>
                </div>
              ))}
            </dl>
          )}
          {manifest.groups.map((group) => (
            <div key={group.key} className={styles.group}>
              <div className={styles.groupTitle}>{group.title}</div>
              <ul className={styles.itemList}>
                {group.items.map((item) => (
                  <li key={`${item.name}-${item.version ?? ''}`} className={styles.item}>
                    <span className={styles.itemName}>{item.name}</span>
                    {item.version && <span className={styles.version}>{item.version}</span>}
                    {item.detail && <span className={styles.itemDetail}>{item.detail}</span>}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </>
      )}
    </Block>
  )
}

function RulesBlock() {
  useRuleCatalog()
  const groups = ruleGroups()
  const counts = countRules(groups)
  return (
    <Block
      title="Правила аудита"
      note={
        <>
          {counts.D} детерминированных <CheckKindBadge kind="D" /> — правило в коде, результат всегда один; {counts.N} контекстных{' '}
          <CheckKindBadge kind="N" /> — отвечает модель по картинке слайда.
        </>
      }
    >
      {groups.map((group) => (
        <div key={group.category} className={styles.group}>
          <div className={styles.groupTitle}>
            {group.label} <span className={styles.groupCount}>{group.rules.length}</span>
          </div>
          <ul className={styles.itemList}>
            {group.rules.map((rule) => (
              <li key={rule.code} className={styles.rule}>
                <CheckKindBadge kind={rule.kind} />
                <span className={styles.ruleText}>
                  <span className={styles.ruleName}>{rule.name}</span>
                  <span className={styles.ruleCode}>{rule.code}</span>
                </span>
                {rule.autoFix && (
                  <span className={styles.autoFix} title={rule.autoFix}>
                    автоисправление
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </Block>
  )
}

export function AboutPanel({ open, onOpenChange }: AboutPanelProps) {
  return (
    <Drawer open={open} onOpenChange={onOpenChange} title="О системе">
      <VersionBlock />
      <CapabilitiesBlock />
      <ManifestBlock />
      <RulesBlock />
    </Drawer>
  )
}
