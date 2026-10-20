import { useState } from 'react'
import type { Insight, InsightPatch, Understanding, UnderstandingBlock } from '@/entities/assistant'
import { Button } from '@/shared/ui'
import { blockTitle, CHART_TYPE_LABEL, insightEntries, insightTitle, MiniChart, needsDecision, type Selection } from '@/widgets/draft-deck'
import styles from './UnderstandingPage.module.css'

interface SelectionDetailsProps {
  understanding: Understanding
  selection: Selection
  onPatchInsight: (id: string, patch: InsightPatch) => void
  onEditBlock: (id: string, patch: { title?: string; text?: string }) => void
  onRemoveBlock: (id: string) => void
  onEditModeChange: (edit: boolean) => void
}

function profileSentence(profile: string | null): string | null {
  if (!profile) return null
  const parts = profile.split(';').map((part) => part.trim()).filter(Boolean)
  const size = parts.filter((part) => /строк|колон/.test(part)).join(', ')
  const gaps = parts.some((part) => part.startsWith('есть пропуски')) ? ' Есть пропуски — учту их на графике.' : ''
  return size ? `${size.charAt(0).toUpperCase()}${size.slice(1)}.${gaps}` : null
}

function chartName(type: string | null | undefined): string {
  return type ? (CHART_TYPE_LABEL[type] ?? type).toLowerCase() : ''
}

function BlockDetails({ block, editing, onEditModeChange, onEditBlock, onRemoveBlock }: { block: UnderstandingBlock; editing: boolean; onEditModeChange: SelectionDetailsProps['onEditModeChange']; onEditBlock: SelectionDetailsProps['onEditBlock']; onRemoveBlock: SelectionDetailsProps['onRemoveBlock'] }) {
  const [draft, setDraft] = useState(block.text ?? '')
  const title = blockTitle(block)

  if (editing) {
    return (
      <div className={styles.details}>
        <textarea className={styles.noteInput} rows={8} value={draft} onChange={(event) => setDraft(event.target.value)} aria-label="Текст блока" />
        <div className={styles.noteActions}>
          <Button
            variant="secondary"
            onClick={() => {
              onEditBlock(block.id, { text: draft })
              onEditModeChange(false)
            }}
          >
            Сохранить
          </Button>
          <Button variant="ghost" onClick={() => onEditModeChange(false)}>
            Отмена
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className={styles.details}>
      <p className={styles.detailLead}>{title}</p>
      {block.text && block.text.trim() !== title && <p className={styles.detailText}>{block.text}</p>}
      <div className={styles.noteActions}>
        <Button variant="secondary" onClick={() => onEditModeChange(true)}>
          Изменить
        </Button>
        <Button variant="ghost" onClick={() => onRemoveBlock(block.id)}>
          Не включать в презентацию
        </Button>
      </div>
    </div>
  )
}

function InsightDetails({ insight, entry, editing, onEditModeChange, onPatchInsight }: { insight: Insight; entry: ReturnType<typeof insightEntries>[number]; editing: boolean; onEditModeChange: SelectionDetailsProps['onEditModeChange']; onPatchInsight: SelectionDetailsProps['onPatchInsight'] }) {
  const [draftText, setDraftText] = useState(insight.headline || insight.fact)
  const [draftMeaning, setDraftMeaning] = useState(insight.meaning ?? '')
  const [draftImplication, setDraftImplication] = useState(insight.implication ?? '')

  const { dataset, preview } = entry
  const title = insightTitle(insight)
  const chosen = dataset.recommendations[0]?.chart_type
  const alternatives = dataset.recommendations.slice(1, 3).map((item) => chartName(item.chart_type)).filter(Boolean)
  const profile = profileSentence(dataset.profile_summary)
  const decide = needsDecision(insight)

  const saveEdit = () => {
    const cleanText = draftText.trim()
    if (!cleanText) return
    const patch: InsightPatch = {
      text: cleanText,
      confirmed: true,
      ...(draftMeaning.trim() !== (insight.meaning ?? '') ? { meaning: draftMeaning.trim() || null } : {}),
      ...(draftImplication.trim() !== (insight.implication ?? '') ? { implication: draftImplication.trim() || null } : {}),
    }
    onPatchInsight(insight.id, patch)
    onEditModeChange(false)
  }

  return (
    <div className={styles.details}>
      {editing ? (
        <div className={styles.editForm}>
          <label className={styles.editField}>
            <span className={styles.editLabel}>Формулировка вывода</span>
            <textarea
              className={styles.noteInput}
              rows={3}
              value={draftText}
              aria-label="Формулировка вывода"
              onChange={(event) => setDraftText(event.target.value)}
            />
          </label>
          <label className={styles.editField}>
            <span className={styles.editLabel}>Что это значит</span>
            <textarea
              className={styles.noteInput}
              rows={2}
              value={draftMeaning}
              placeholder="Например: рост ускорился во втором полугодии"
              aria-label="Что это значит"
              onChange={(event) => setDraftMeaning(event.target.value)}
            />
          </label>
          <label className={styles.editField}>
            <span className={styles.editLabel}>Что из этого следует</span>
            <textarea
              className={styles.noteInput}
              rows={2}
              value={draftImplication}
              placeholder="Например: закрепить фокус на ключевом канале"
              aria-label="Что из этого следует"
              onChange={(event) => setDraftImplication(event.target.value)}
            />
          </label>
          <div className={styles.noteActions}>
            <Button variant="secondary" disabled={!draftText.trim()} onClick={saveEdit}>
              Сохранить
            </Button>
            <Button variant="ghost" onClick={() => onEditModeChange(false)}>
              Отмена
            </Button>
          </div>
        </div>
      ) : (
        <>
          <p className={styles.detailLead}>{title}</p>
          {decide && <p className={styles.detailMuted}>Это моё предложение. Оставлю его в презентации, только если вы согласны.</p>}
          <div className={styles.noteActions}>
            {decide ? (
              <Button variant="primary" onClick={() => onPatchInsight(insight.id, { confirmed: true })}>
                Согласен
              </Button>
            ) : insight.confirmed ? (
              <Button variant="ghost" onClick={() => onPatchInsight(insight.id, { confirmed: false })}>
                Отменить согласие
              </Button>
            ) : null}
            <Button variant="secondary" onClick={() => onEditModeChange(true)}>
              Изменить формулировку
            </Button>
          </div>
        </>
      )}

      <div className={styles.detailBlock}>
        <h3 className={styles.detailHeading}>Данные и график</h3>
        {preview && (
          <div className={styles.detailChart}>
            <MiniChart preview={preview} chartType={chosen} label={`График: ${dataset.title ?? ''}`} />
          </div>
        )}
        <p className={styles.detailText}>
          {dataset.file ?? dataset.title}
          {profile ? `. ${profile}` : ''}
        </p>
        {dataset.why && <p className={styles.detailText}>{dataset.why}</p>}
        {alternatives.length > 0 && <p className={styles.detailMuted}>Подойдёт и так: {alternatives.join(', ')}. Сменить график можно на шаге «План».</p>}
      </div>
    </div>
  )
}

export function SelectionDetails({ understanding, selection, onPatchInsight, onEditBlock, onRemoveBlock, onEditModeChange }: SelectionDetailsProps) {
  if (selection.kind === 'block') {
    const block = understanding.blocks.find((item) => item.id === selection.id)
    if (!block) return null
    return <BlockDetails block={block} editing={selection.edit === true} onEditModeChange={onEditModeChange} onEditBlock={onEditBlock} onRemoveBlock={onRemoveBlock} />
  }

  const entry = insightEntries(understanding).find((item) => item.insight.id === selection.id)
  if (!entry) return null
  return <InsightDetails insight={entry.insight} entry={entry} editing={selection.edit === true} onEditModeChange={onEditModeChange} onPatchInsight={onPatchInsight} />
}
