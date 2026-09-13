import type {
  BacktestJob,
  BacktestResult,
  BotState,
  Candle,
  ConfigListItem,
  DecisionSnapshot,
  ExchangeInfo,
  IndicatorConfig,
  JobStatus,
  ModeStatus,
  OptimizeResult,
  SavedConfig,
  ScanCandidate,
  SettingsField,
  StrategySettings,
  TradeAnalysis,
  TradeEvent,
} from './types'

async function parse<T>(res: Response): Promise<T> {
  const raw = await res.text()
  if (!raw.trim()) {
    throw new Error(
      res.ok
        ? `Empty response from ${res.url || 'the API'}`
        : `API not reachable (HTTP ${res.status}) — start the Python server with: bun run dev:api`,
    )
  }
  let data: unknown
  try {
    data = JSON.parse(raw)
  } catch {
    throw new Error(`Invalid JSON from ${res.url} (HTTP ${res.status}): ${raw.slice(0, 120)}`)
  }
  if (!res.ok) {
    throw new Error((data as { error?: string })?.error || String(res.status))
  }
  // BotState also has an `error` string for loop faults — only treat pure error envelopes as failures.
  if (
    data &&
    typeof data === 'object' &&
    'error' in data &&
    (data as { error?: unknown }).error &&
    !('running' in data) &&
    !('cash' in data) &&
    !('checks' in data)
  ) {
    throw new Error(String((data as { error: unknown }).error))
  }
  return data as T
}

export const api = {
  state: () => fetch('/api/state').then((r) => parse<BotState>(r)),
  decision: () => fetch('/api/decision').then((r) => parse<DecisionSnapshot>(r)),
  settings: () => fetch('/api/settings').then((r) => parse<StrategySettings>(r)),
  settingsSchema: () =>
    fetch('/api/settings/schema').then((r) =>
      parse<{
        schema: Record<string, SettingsField>
        groups: string[]
        gates: Record<string, boolean>
        presets: { name: string; label: string }[]
        intervals: string[]
      }>(r),
    ),
  exchanges: () => fetch('/api/exchanges').then((r) => parse<ExchangeInfo[]>(r)),
  help: () => fetch('/api/help').then((r) => parse<Record<string, string>>(r)),
  mode: () => fetch('/api/mode').then((r) => parse<ModeStatus>(r)),
  setMode: (mode: 'paper' | 'live', confirm = false) =>
    fetch('/api/mode', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode, confirm }),
    }).then((r) => parse<{ mode: string; exchange: string; keys: ModeStatus['keys'] }>(r)),
  job: (id: string) => fetch(`/api/jobs/${encodeURIComponent(id)}`).then((r) => parse<JobStatus>(r)),
  runOptimize: (symbol: string) =>
    fetch('/api/optimize/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol }),
    }).then((r) => parse<JobStatus>(r)),
  applyPreset: (preset: string) =>
    fetch('/api/settings/preset', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ preset }),
    }).then((r) => parse<{ preset: string; settings: StrategySettings }>(r)),
  scan: (limit = 12) => fetch(`/api/scan?limit=${limit}`).then((r) => parse<ScanCandidate[]>(r)),
  candles: (symbol: string, interval: string, limit: number) =>
    fetch(`/api/candles?${new URLSearchParams({ symbol, interval, limit: String(limit) })}`).then((r) =>
      parse<Candle[]>(r),
    ),
  markets: () => fetch('/api/markets').then((r) => parse<string[]>(r)),
  ticker: (symbol: string) =>
    fetch(`/api/ticker?symbol=${encodeURIComponent(symbol)}`).then((r) =>
      parse<{ symbol: string; price: number; ts: number }>(r),
    ),
  trades: (limit = 200, symbol = '') =>
    fetch(`/api/trades?${new URLSearchParams({ limit: String(limit), symbol })}`).then((r) =>
      parse<TradeEvent[]>(r),
    ),
  orderbook: (symbol: string) =>
    fetch(`/api/orderbook?symbol=${encodeURIComponent(symbol)}`).then((r) =>
      parse<{ bid: number; ask: number; spread_bps: number; imbalance: number }>(r),
    ),
  backtest: (symbol: string, history = false) =>
    fetch(`/api/backtest?${new URLSearchParams({ symbol, history: history ? '1' : '0' })}`).then((r) =>
      parse<BacktestResult>(r),
    ),
  runBacktest: (body: { symbol?: string; history?: boolean; holdout?: boolean }) =>
    fetch('/api/backtest/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then((r) => parse<BacktestJob>(r)),
  backtestStatus: () => fetch('/api/backtest/status').then((r) => parse<BacktestJob>(r)),
  configs: () => fetch('/api/configs').then((r) => parse<ConfigListItem[]>(r)),
  loadConfig: (name: string) =>
    fetch(`/api/configs?${new URLSearchParams({ name })}`).then((r) => parse<SavedConfig>(r)),
  saveConfig: (body: {
    name: string
    label?: string
    data?: SavedConfig['data']
    settings?: Partial<StrategySettings>
    indicators?: IndicatorConfig[]
  }) =>
    fetch('/api/configs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then((r) => parse<{ name: string; label: string; updated: number }>(r)),
  deleteConfig: (name: string) =>
    fetch('/api/configs/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    }).then((r) => parse<{ deleted: string }>(r)),
  applyConfig: (name: string) =>
    fetch('/api/configs/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    }).then((r) =>
      parse<{
        ok: boolean
        config: SavedConfig
        state: BotState
        settings: StrategySettings
      }>(r),
    ),
  optimize: (symbol: string) =>
    fetch(`/api/optimize?symbol=${encodeURIComponent(symbol)}`).then((r) => parse<OptimizeResult>(r)),
  analyze: (body: {
    symbol: string
    interval: string
    limit: number
    indicators: IndicatorConfig[]
    settings: Partial<StrategySettings>
    live_price?: number
  }) =>
    fetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then((r) => parse<TradeAnalysis>(r)),
  analyzeStream: async (body: {
    symbol: string; interval: string; limit: number; indicators: IndicatorConfig[]
    settings: Partial<StrategySettings>; live_price?: number
  }, onDelta: (text: string) => void) => {
    const res = await fetch('/api/analyze/stream', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    })
    if (!res.ok || !res.body) throw new Error(`NIM stream unavailable (HTTP ${res.status})`)
    const reader = res.body.getReader(); const decoder = new TextDecoder(); let buffer = ''
    while (true) {
      const { value, done } = await reader.read(); if (done) break
      buffer += decoder.decode(value, { stream: true })
      const chunks = buffer.split('\n\n'); buffer = chunks.pop() || ''
      for (const line of chunks) {
        const raw = line.split('\n').find((x) => x.startsWith('data: '))?.slice(6); if (!raw) continue
        const msg = JSON.parse(raw) as { kind: string; text?: string; data?: TradeAnalysis; error?: string }
        if (msg.kind === 'delta' && msg.text) onDelta(msg.text)
        if (msg.kind === 'error') throw new Error(msg.error || 'NIM analysis failed')
        if (msg.kind === 'done' && msg.data) return msg.data
      }
    }
    throw new Error('NIM stream ended without analysis')
  },
  start: () => fetch('/api/start', { method: 'POST' }).then((r) => parse<BotState>(r)),
  clearStats: () => fetch('/api/clear', { method: 'POST' }).then((r) => parse<BotState>(r)),
  stop: () => fetch('/api/stop', { method: 'POST' }).then((r) => parse<BotState>(r)),
  saveSettings: (body: Partial<StrategySettings>) =>
    fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then((r) => parse<BotState>(r)),
}
