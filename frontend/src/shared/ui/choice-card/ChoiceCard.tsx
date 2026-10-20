import { clsx } from 'clsx'
import type { ReactNode } from 'react'
import { Check, Icon } from '../icon'
import styles from './ChoiceCard.module.css'

export interface ChoiceCardProps {
  title: ReactNode
  description?: ReactNode
  checked: boolean
  recommended?: boolean
  disabled?: boolean
  badge?: ReactNode
  shortcut?: number | string | null
  ariaLabel?: string
  radioName?: string
  as?: 'button' | 'div' | 'section'
  className?: string
  descriptionClassName?: string
  onSelect: () => void
  children?: ReactNode
  [key: `data-${string}`]: string | boolean | undefined
}

export function ChoiceCard({
  title,
  description,
  checked,
  recommended = false,
  disabled = false,
  badge,
  shortcut,
  ariaLabel,
  radioName,
  as = 'button',
  className,
  descriptionClassName,
  onSelect,
  children,
  ...dataAttrs
}: ChoiceCardProps) {
  if (radioName) {
    const Tag = as === 'div' ? 'div' : 'section'
    return (
      <Tag
        className={clsx(styles.card, styles.containerCard, className)}
        data-selected={checked || undefined}
        data-checked={checked || undefined}
        data-recommended={recommended || undefined}
        onClick={() => {
          if (!disabled) onSelect()
        }}
        {...dataAttrs}
      >
        <label className={styles.headLabel}>
          <input
            type="radio"
            name={radioName}
            className={styles.srInput}
            checked={checked}
            disabled={disabled}
            onChange={onSelect}
            aria-label={ariaLabel}
          />
          <span className={styles.radioDot} data-checked={checked || undefined} aria-hidden="true">
            {checked && <Icon as={Check} size={11} />}
          </span>
          <span className={styles.title}>{title}</span>
          {badge && <span className={styles.badgeWrap}>{badge}</span>}
          {shortcut !== undefined && shortcut !== null && (
            <kbd className={styles.keyCap} aria-hidden="true">
              {shortcut}
            </kbd>
          )}
        </label>
        {description && (
          <p
            className={clsx(styles.description, styles.descriptionFlush, descriptionClassName)}
            title={typeof description === 'string' ? description : undefined}
          >
            {description}
          </p>
        )}
        {children}
      </Tag>
    )
  }

  if (as === 'div' || as === 'section' || children !== undefined) {
    const Tag = as === 'section' ? 'section' : 'div'
    return (
      <Tag
        className={clsx(styles.card, styles.containerCard, className)}
        data-selected={checked || undefined}
        data-checked={checked || undefined}
        data-recommended={recommended || undefined}
        {...dataAttrs}
      >
        <button
          type="button"
          role="radio"
          aria-checked={checked}
          aria-label={ariaLabel}
          disabled={disabled}
          className={styles.trigger}
          data-checked={checked || undefined}
          onClick={onSelect}
        >
          <span className={styles.head}>
            <span className={styles.radioDot} data-checked={checked || undefined} aria-hidden="true">
              {checked && <Icon as={Check} size={11} />}
            </span>
            <span className={styles.title}>{title}</span>
            {badge && <span className={styles.badgeWrap}>{badge}</span>}
            {shortcut !== undefined && shortcut !== null && (
              <kbd className={styles.keyCap} aria-hidden="true">
                {shortcut}
              </kbd>
            )}
          </span>
          {description && <span className={clsx(styles.description, descriptionClassName)}>{description}</span>}
        </button>
        {children}
      </Tag>
    )
  }

  return (
    <button
      type="button"
      role="radio"
      aria-checked={checked}
      aria-label={ariaLabel}
      disabled={disabled}
      className={clsx(styles.card, styles.buttonCard, className)}
      data-selected={checked || undefined}
      data-checked={checked || undefined}
      data-recommended={recommended || undefined}
      onClick={onSelect}
      {...dataAttrs}
    >
      <span className={styles.head}>
        <span className={styles.radioDot} data-checked={checked || undefined} aria-hidden="true">
          {checked && <Icon as={Check} size={11} />}
        </span>
        <span className={styles.title}>{title}</span>
        {badge && <span className={styles.badgeWrap}>{badge}</span>}
        {shortcut !== undefined && shortcut !== null && (
          <kbd className={styles.keyCap} aria-hidden="true">
            {shortcut}
          </kbd>
        )}
      </span>
      {description && <span className={clsx(styles.description, descriptionClassName)}>{description}</span>}
    </button>
  )
}
