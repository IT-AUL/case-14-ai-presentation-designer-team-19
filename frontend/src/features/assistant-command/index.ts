export { AssistantCommandBar } from './ui/AssistantCommandBar'
export type { AssistantCommandBarProps } from './ui/AssistantCommandBar'
export {
  MAX_COMMAND_HISTORY,
  extractSlideNumber,
  resolveCommandSlideId,
  formatAcceptedSummary,
  formatClarificationQuestion,
  resolveClarificationOptions,
  pushCommandHistory,
  loadCommandHistory,
  saveCommandHistory,
  clearCommandHistoryCache,
} from './model/commandModel'
export type { CommandHistoryEntry, CommandSlideTarget } from './model/commandModel'
