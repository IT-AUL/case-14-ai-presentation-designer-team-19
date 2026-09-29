import type { CatalogStrategy, VariantAxes, VariantSummary } from './types'

export type ProfileLevel = 1 | 2 | 3

export interface StrategyProfile {
  exemplarLayouts: ProfileLevel
  text: ProfileLevel
  visuals: ProfileLevel
}

export interface StrategyInfo {
  id: string
  name: string
  axis: string
  audience: string
  rationale: string
  profile: StrategyProfile | null
  recommended: boolean
}

export const STRATEGY_ORDER: readonly CatalogStrategy[] = ['faithful', 'balanced', 'visual']

export const DEFAULT_STRATEGY: CatalogStrategy = 'balanced'

export const STRATEGY_AXIS =
  'Одни правила шаблона, разный баланс текста и визуализации.'

export const STRATEGY_PROFILE_LABEL: Record<keyof StrategyProfile, string> = {
  exemplarLayouts: 'Макеты-образцы',
  text: 'Текст',
  visuals: 'Визуализация',
}

export const STRATEGIES: Record<CatalogStrategy, StrategyInfo> = {
  faithful: {
    id: 'faithful',
    name: 'Близко к шаблону',
    axis: 'Ближе к образцам шаблона, больше текста.',
    audience: 'Когда колода должна выглядеть как «родная» в библиотеке компании.',
    rationale: 'Макеты, ближайшие к образцам шаблона. Узнаваемо и предсказуемо.',
    profile: { exemplarLayouts: 3, text: 3, visuals: 1 },
    recommended: false,
  },
  balanced: {
    id: 'balanced',
    name: 'Сбалансированный',
    axis: 'Разнообразные макеты, умеренная визуализация.',
    audience: 'Для большинства выступлений: история читается, слайды не однообразны.',
    rationale: 'Макеты чередуются, текст и визуализация в равновесии.',
    profile: { exemplarLayouts: 2, text: 2, visuals: 2 },
    recommended: true,
  },
  visual: {
    id: 'visual',
    name: 'Визуальный',
    axis: 'Упор на графики, меньше текста.',
    audience: 'Когда решение принимают по цифрам и важно, чтобы данные бросались в глаза.',
    rationale: 'Числа становятся графиками, таблицами и схемами, текста меньше.',
    profile: { exemplarLayouts: 1, text: 1, visuals: 3 },
    recommended: false,
  },
}

function isCatalogStrategy(strategy: string): strategy is CatalogStrategy {
  return strategy in STRATEGIES
}

export function strategyInfo(strategy: string): StrategyInfo {
  if (isCatalogStrategy(strategy)) return STRATEGIES[strategy]
  return {
    id: strategy,
    name: strategy === 'custom' ? 'Своя стратегия' : strategy,
    axis: 'Стратегия задана вручную.',
    audience: 'Для особых случаев, когда стандартных стратегий недостаточно.',
    rationale: 'Параметры стратегии задал пользователь.',
    profile: null,
    recommended: false,
  }
}

export function variantRationale(variant: Pick<VariantSummary, 'strategy' | 'rationale'>): string {
  return variant.rationale?.trim() || strategyInfo(variant.strategy).rationale
}

export function orderVariants<T extends Pick<VariantSummary, 'strategy'>>(variants: readonly T[]): T[] {
  const rank = (strategy: string) => {
    const position = STRATEGY_ORDER.findIndex((known) => known === strategy)
    return position === -1 ? STRATEGY_ORDER.length : position
  }
  return [...variants].sort((a, b) => rank(a.strategy) - rank(b.strategy))
}

export interface AxisView {
  key: string
  label: string
  value: number
  caption: string
}

const AXIS_LABEL: Record<keyof VariantAxes, string> = {
  text_density: 'Текст',
  layout_diversity: 'Разнообразие макетов',
  visualization: 'Визуализация',
}

const AXIS_ORDER: readonly (keyof VariantAxes)[] = ['text_density', 'layout_diversity', 'visualization']

const PROFILE_ORDER: readonly (keyof StrategyProfile)[] = ['text', 'exemplarLayouts', 'visuals']

const PROFILE_MAX = 3

function unit(value: number): number {
  return Math.min(1, Math.max(0, value))
}

export function variantAxes(variant: Pick<VariantSummary, 'strategy' | 'axes'>): AxisView[] {
  const axes = variant.axes
  if (axes) {
    const measured = AXIS_ORDER.flatMap((key): AxisView[] => {
      const value = axes[key]
      if (typeof value !== 'number' || !Number.isFinite(value)) return []
      const share = unit(value)
      return [{ key, label: AXIS_LABEL[key], value: share, caption: `${AXIS_LABEL[key]}: ${Math.round(share * 100)}%` }]
    })
    if (measured.length > 0) return measured
  }
  const profile = strategyInfo(variant.strategy).profile
  if (!profile) return []
  return PROFILE_ORDER.map((key) => ({
    key,
    label: STRATEGY_PROFILE_LABEL[key],
    value: profile[key] / PROFILE_MAX,
    caption: `${STRATEGY_PROFILE_LABEL[key]}: ${profile[key]} из ${PROFILE_MAX}`,
  }))
}
