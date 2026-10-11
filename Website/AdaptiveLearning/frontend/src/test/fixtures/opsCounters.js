// Payloads of /api/admin/generation and /api/admin/refusals, as main.py builds them.

export const GENERATION_PATH = '/api/admin/generation?hours=24'
export const REFUSALS_PATH = '/api/admin/refusals?hours=24'

export function buildGeneration(over = {}) {
  return {
    retrieved: true,
    hours: 24,
    provider: 'claude',
    model: 'claude-haiku-4-5',
    outcomes: { 'claude:ok': 40, 'claude:unavailable:daily': 3, 'claude:error:ValueError': 1 },
    waits: { claude: { calls: 44, mean_ms: 2400, max_ms: 9100 } },
    tokens: { 'claude:in': 80000, 'claude:out': 12000 },
    estimated_cost_usd: 0.14,
    prices_per_mtok: { input: 1.0, output: 5.0 },
    questions: {
      'served:inline': 5, 'served:queue': 15, 'served:practice': 6,
      'generation_failed:ValueError': 2, prefetched: 16, prefetch_failed: 2,
    },
    hourly: [{ hour: '2026-10-08T14:00:00+00:00', ok: 40, failed: 4 }],
    daily_ceiling: { used: 44, limit: 2500, scope: 'this server process' },
    complete: true,
    dropped: 0,
    ...over,
  }
}

const refusal = (status, route, count, method = 'GET') => ({ status, method, route, count })

export function buildRefusals(over = {}) {
  return {
    retrieved: true,
    hours: 24,
    refusals: [
      refusal(503, '/api/generate-question', 6),
      refusal(403, '/api/students/{student_id}/report', 2),
    ],
    recent: [refusal(503, '/api/generate-question', 4)],
    recent_from: '2026-10-08T13:00:00+00:00',
    hourly: [{ hour: '2026-10-08T14:00:00+00:00', count: 8 }],
    complete: true,
    dropped: 0,
    ...over,
  }
}
