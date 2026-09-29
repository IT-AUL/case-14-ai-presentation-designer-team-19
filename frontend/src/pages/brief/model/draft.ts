import { useCallback, useRef, useState } from 'react'
import { readJson, writeJson } from '@/shared/lib/storage'
import { clampSlideCount, createBriefForm, PURPOSES } from './form'
import type { BriefDefaults, BriefForm, ParsedContent } from './form'

const DRAFT_PREFIX = 'deckdna.brief-draft.v1:'

function draftKey(projectId: string): string {
  return `${DRAFT_PREFIX}${projectId}`
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function stringList(value: unknown): string[] | undefined {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : undefined
}

function parsedContent(value: unknown): ParsedContent | null | undefined {
  if (value === null) return null
  if (!isRecord(value) || typeof value.packId !== 'string' || typeof value.sourceKey !== 'string') return undefined
  return {
    packId: value.packId,
    sourceKey: value.sourceKey,
    label: typeof value.label === 'string' ? value.label : value.packId,
    sizeBytes: typeof value.sizeBytes === 'number' ? value.sizeBytes : null,
  }
}

export function restoreBriefForm(raw: unknown, defaults: BriefDefaults = {}): BriefForm {
  const base = createBriefForm(defaults)
  if (!isRecord(raw)) return base
  const purpose = PURPOSES.find((option) => option.value === raw.purpose)?.value ?? null
  const parsed = parsedContent(raw.parsed)
  return {
    purpose,
    customPurpose: typeof raw.customPurpose === 'string' ? raw.customPurpose : base.customPurpose,
    audience: typeof raw.audience === 'string' ? raw.audience : base.audience,
    tone: typeof raw.tone === 'string' ? raw.tone : base.tone,
    language: raw.language === 'en' || raw.language === 'ru' ? raw.language : base.language,
    slideCount: typeof raw.slideCount === 'number' ? clampSlideCount(raw.slideCount) : base.slideCount,
    mandatorySections: stringList(raw.mandatorySections) ?? base.mandatorySections,
    forbiddenClaims: stringList(raw.forbiddenClaims) ?? base.forbiddenClaims,
    contentMode: raw.contentMode === 'text' ? 'text' : 'file',
    text: typeof raw.text === 'string' ? raw.text : base.text,
    useLlm: typeof raw.useLlm === 'boolean' ? raw.useLlm : base.useLlm,
    parsed: parsed ?? base.parsed,
  }
}

export function readBriefDraft(projectId: string, defaults: BriefDefaults = {}): BriefForm {
  return restoreBriefForm(readJson<unknown>(draftKey(projectId), null, 'session'), defaults)
}

export function useBriefDraft(projectId: string, defaults: BriefDefaults) {
  const [form, setForm] = useState<BriefForm>(() => readBriefDraft(projectId, defaults))
  const latest = useRef(form)

  const update = useCallback(
    (patch: Partial<BriefForm>) => {
      const next = { ...latest.current, ...patch }
      latest.current = next
      writeJson(draftKey(projectId), next, 'session')
      setForm(next)
    },
    [projectId],
  )

  return [form, update] as const
}
