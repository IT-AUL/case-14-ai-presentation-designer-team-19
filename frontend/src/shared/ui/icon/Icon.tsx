import { clsx } from 'clsx'
import type { LucideIcon, LucideProps } from 'lucide-react'
import styles from './Icon.module.css'

export type IconComponent = LucideIcon

interface IconProps extends Omit<LucideProps, 'ref'> {
  as: IconComponent
  size?: number
}

export function Icon({ as: Component, size = 16, strokeWidth = 2, className, ...rest }: IconProps) {
  return <Component size={size} strokeWidth={strokeWidth} className={clsx(styles.icon, className)} aria-hidden focusable={false} {...rest} />
}
