import { clsx } from 'clsx'
import type { DesignDna, RoleView } from '@/entities/template'
import { Badge } from '@/shared/ui'
import { LOW_CONFIDENCE, roleLabel, STRATEGY_LABEL } from '../model/labels'
import styles from './DesignDna.module.css'

export function RolesSection({ roles, className }: { roles: RoleView[]; className?: string }) {
  return (
    <section className={clsx(styles.section, className)} aria-labelledby="dna-roles">
      <div className={styles.sectionHead}>
        <h2 id="dna-roles" className={styles.sectionTitle}>
          Роли слайдов
        </h2>
        <div className={styles.sectionMeta}>слайдов · уверенность</div>
      </div>
      <ul className={styles.roles}>
        {roles.map((role) => {
          const low = role.confidence < LOW_CONFIDENCE
          return (
            <li key={role.role} className={clsx(styles.roleRow, low && styles.roleLow)}>
              <span className={styles.roleName} title={role.role}>
                {roleLabel(role.role)}
              </span>
              <span className={styles.roleCount}>{role.slides}</span>
              <span className={styles.roleBody}>
                <span className={styles.roleTrack}>
                  <span className={clsx(styles.roleFill, low && styles.roleFillLow)} style={{ width: `${role.confidence * 100}%` }} />
                </span>
                {role.evidence && <span className={styles.roleEvidence}>{role.evidence}</span>}
              </span>
              <span className={styles.roleValue}>{role.confidence.toFixed(2)}</span>
            </li>
          )
        })}
      </ul>
      <div className={styles.note}>Ниже {LOW_CONFIDENCE.toFixed(2)} — роль определена неуверенно и помечена цветом.</div>
    </section>
  )
}

export function UnsupportedSection({ features, className }: { features: DesignDna['unsupported_features']; className?: string }) {
  return (
    <section className={clsx(styles.section, className)} aria-labelledby="dna-unsupported">
      <div className={styles.sectionHead}>
        <h2 id="dna-unsupported" className={styles.sectionTitle}>
          Что система не умеет
        </h2>
        <div className={styles.sectionMeta}>и как поступает</div>
      </div>
      <dl className={styles.unsupported}>
        {features.map((feature, index) => {
          const strategy = STRATEGY_LABEL[feature.strategy]
          return (
            <div key={`${feature.feature}-${index}`} className={styles.unsupportedItem}>
              <dt>
                {feature.feature} · {feature.location}
                {feature.disclosure && <span className={styles.unsupportedDisclosure}>{feature.disclosure}</span>}
              </dt>
              <dd>
                <Badge tone={strategy.tone}>{strategy.label}</Badge>
              </dd>
            </div>
          )
        })}
      </dl>
    </section>
  )
}
