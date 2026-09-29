import { useEffect, useMemo, useState } from 'react'
import { Button, Check, Copy, Icon, useToast } from '@/shared/ui'
import { buildReproductionCommand, CONTENT_PLACEHOLDER, TEMPLATE_PLACEHOLDER, type ReproductionInput } from '../lib/reproductionCommand'
import styles from './ReproduceCommand.module.css'

const COPIED_MS = 1800

const PLACEHOLDER_HINT: Record<string, string> = {
  [TEMPLATE_PLACEHOLDER]: 'путь к шаблону',
  [CONTENT_PLACEHOLDER]: 'путь к файлу контента',
}

export function ReproduceCommand(input: ReproductionInput) {
  const { templateFile, contentFile, outputDir, strategy, slideCount } = input
  const { command, placeholders } = useMemo(
    () => buildReproductionCommand({ templateFile, contentFile, outputDir, strategy, slideCount }),
    [templateFile, contentFile, outputDir, strategy, slideCount],
  )
  const [copied, setCopied] = useState(false)
  const toast = useToast()

  useEffect(() => {
    if (!copied) return
    const timer = window.setTimeout(() => setCopied(false), COPIED_MS)
    return () => window.clearTimeout(timer)
  }, [copied])

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(command)
      setCopied(true)
    } catch {
      toast.show('Не удалось скопировать — выделите команду вручную')
    }
  }

  return (
    <section className={styles.root} aria-labelledby="reproduce-title">
      <h3 id="reproduce-title" className={styles.title}>
        Воспроизвести прогон
      </h3>
      <code className={styles.command} data-testid="reproduce-command">
        {command}
      </code>
      {placeholders.length > 0 && (
        <p className={styles.hint}>
          {placeholders.map((placeholder) => `${placeholder} — ${PLACEHOLDER_HINT[placeholder] ?? 'значение'}`).join(', ')}
        </p>
      )}
      <Button size="sm" className={styles.button} onClick={copy}>
        <Icon as={copied ? Check : Copy} size={14} />
        <span aria-live="polite">{copied ? 'Скопировано' : 'Скопировать команду'}</span>
      </Button>
    </section>
  )
}
