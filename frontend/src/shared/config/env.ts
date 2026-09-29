export const API_BASE = '/api/v1'

export const API_MODE: 'live' | 'mock' = import.meta.env.VITE_API_MODE === 'mock' ? 'mock' : 'live'

export const GENERATION_BUDGET_SECONDS = 300
