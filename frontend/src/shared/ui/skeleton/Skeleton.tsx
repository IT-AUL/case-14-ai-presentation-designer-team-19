import { clsx } from 'clsx'
import type { CSSProperties } from 'react'
import styles from './Skeleton.module.css'

interface SkeletonProps {
  className?: string
  style?: CSSProperties
  delay?: number
}

export function Skeleton({ className, style, delay = 0 }: SkeletonProps) {
  return <div aria-hidden className={clsx(styles.skeleton, className)} style={{ animationDelay: `${delay}s`, ...style }} />
}
