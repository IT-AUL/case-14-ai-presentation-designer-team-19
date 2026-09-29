import { clsx } from 'clsx'
import type { HTMLAttributes } from 'react'
import styles from './Mono.module.css'

export function Mono({ className, ...rest }: HTMLAttributes<HTMLSpanElement>) {
  return <span className={clsx(styles.mono, className)} {...rest} />
}
