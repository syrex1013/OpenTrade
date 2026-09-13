export type Candle = [number, number, number, number, number, number]

export type BotState = {
  running: boolean
  mode: string
  exchange: string
  symbol: string
  price: number
  action: string
  cash: number
  coin: number
  entry: number
  pnl: number
  realized_pnl?: number
  equity?: number
  trades: number
  wins: number
  spread_bps: number
  imbalance: number
  error: string
  halted?: boolean
  expectancy?: number
}

export type GateId =
  | 'atr'
  | 'trend'
  | 'volume'
  | 'rsi'
  | 'rsi2'
  | 'vwap'
  | 'bb'
  | 'setup'
  | 'score'
  | 'spread'
  | 'imbalance'
  | 'min_gain'
  | 'halt'
  | 'fee_edge'
  | 'pattern'
  | 'ema_cross'
  | 'breakout'
  | 'ai'

export type GateMap = Partial<Record<GateId, boolean>>

export const DEFAULT_GATES: Record<GateId, boolean> = {
  atr: true,
  trend: true,
  volume: true,
  rsi: true,
  rsi2: true,
  vwap: true,
  bb: true,
  setup: true,
  score: true,
  spread: true,
  imbalance: true,
  min_gain: true,
  halt: true,
  fee_edge: true,
  pattern: true,
  ema_cross: true,
  breakout: true,
  ai: true,
}

export const GATE_META: Record<GateId, { label: string; hint: string }> = {
  atr: { label: 'ATR volatility', hint: 'Skip quiet tape below min ATR %' },
  trend: { label: 'Trend / regime SMA', hint: 'Require price near/above regime MA' },
  volume: { label: 'Volume ratio', hint: 'Require relative volume' },
  rsi: { label: 'RSI', hint: 'Primary RSI buy/sell thresholds' },
  rsi2: { label: 'RSI(2)', hint: 'Connors-style extreme RSI(2)' },
  vwap: { label: 'VWAP distance', hint: 'Mean-reversion vs session VWAP' },
  bb: { label: 'Bollinger', hint: 'Lower-band / mid-band location' },
  setup: { label: 'Entry setup pattern', hint: 'deep+turn / micro rebound / Connors / patterns' },
  score: { label: 'Buy score confluence', hint: 'Role-based indicator confluence floor' },
  spread: { label: 'Spread gate', hint: 'Max order-book spread (bps)' },
  imbalance: { label: 'Book imbalance', hint: 'Reject toxic imbalance' },
  min_gain: { label: 'Min gain $', hint: 'Block tiny profitable exits under floor' },
  halt: { label: 'Expectancy halt', hint: 'Pause when rolling expectancy < 0' },
  fee_edge: { label: 'Fee edge', hint: 'Require expected ATR move ≥ fee_edge_mult × fees' },
  pattern: { label: 'Chart patterns', hint: 'VWAP reclaim / squeeze / pullback / breakout' },
  ema_cross: { label: 'EMA cross', hint: 'Fast EMA vs slow regime filter' },
  breakout: { label: 'Breakout', hint: 'Donchian high clear with volume' },
  ai: { label: 'AI filter', hint: 'Optional local chart-AI confirm' },
}

export type ConfigListItem = {
  name: string
  label: string
  updated?: number
  builtin?: boolean
}

export type SavedConfig = {
  name: string
  label?: string
  builtin?: boolean
  updated?: number
  data: {
    settings?: Partial<StrategySettings> & { gates?: GateMap }
    indicators?: IndicatorConfig[]
  }
}


export type StrategySettings = {
  exchange: string
  symbol: string
  interval: string
  poll_seconds: number
  position_pct: number
  leverage?: number
  min_gain_usd?: number
  fee_rate: number
  slippage_rate: number
  take_profit_pct: number
  stop_loss_pct: number
  min_profit_pct: number
  max_hold_bars: number
  max_spread_bps: number
  min_book_imbalance: number
  rsi_buy: number
  rsi_sell: number
  rsi_period: number
  sma_fast: number
  sma_slow: number
  vwap_period: number
  bb_period: number
  bb_std: number
  volume_period: number
  min_volume_ratio: number
  vwap_distance_pct: number
  buy_score: number
  breakout_period: number
  breakout_bps: number
  breakout_volume_ratio: number
  min_atr_pct: number
  min_trend_slope_pct: number
  atr_period: number
  trend_tolerance_pct: number
  trail_pct?: number
  atr_tp_mult?: number
  atr_sl_mult?: number
  vwap_z_entry?: number
  cooldown_bars?: number
  fee_edge_mult?: number
  atr_fee_lambda?: number
  auto_scan?: boolean
  scan_symbols?: number
  scan_seconds?: number
  ai_scan?: boolean
  max_positions?: number
  fee_style?: "maker" | "taker"
  live_post_only?: boolean
  min_entry_score?: number
  expectancy_window?: number
  expectancy_halt?: boolean
  scan_whitelist?: string[]
  gates?: GateMap
}

export type IndicatorId = 'ema' | 'sma' | 'vwap' | 'bb' | 'rsi' | 'atr'

export type ChartType = 'candles' | 'line' | 'area' | 'bars' | 'heikin'

export type IndicatorConfig = {
  /** Unique instance id — multiple of same type allowed. */
  uid: string
  id: IndicatorId
  label: string
  visible: boolean
  period: number
  color: string
  pane: 'price' | 'rsi' | 'atr'
  std?: number
}

export type TradeFill = {
  side: string
  symbol?: string
  price: number
  quantity?: number
  proceeds?: number
  win?: boolean
  pnl?: number | null
  reason?: string
  realized_pnl?: number
  cash?: number
  coin?: number
  trades?: number
  wins?: number
  equity?: number
}

export type TradeEvent = {
  kind?: string
  ts?: number
  data?: TradeFill
} & Partial<TradeFill>

export type EquityPoint = { t: number; equity: number }

export type BacktestFill = {
  side: string
  ts: number
  price: number
  pnl?: number | null
  equity?: number
  /** Position size in base units. */
  quantity?: number
  margin?: number
  notional?: number
  /** Total fees for this fill (entry leg on BUY, both legs on SELL). */
  fee_paid?: number
  fee_entry?: number
  fee_exit?: number
  gross_pnl?: number
  entry?: number
  exit?: number
  reason?: string
  hold_bars?: number
  entry_time?: number
  exit_time?: number
  return_margin_pct?: number
}

export type BacktestCandle = { t: number; o: number; h: number; l: number; c: number }

export type BacktestResult = {
  capital: number
  ending_equity: number
  return_pct: number
  trades: number
  win_rate: number
  profit_factor: number
  max_drawdown_pct: number
  equity_curve?: EquityPoint[]
  fills?: BacktestFill[]
  /** Downsampled OHLC used to plot fills on real candles. */
  candles?: BacktestCandle[]
  net_pnl?: number
  gross_pnl?: number
  fees_total?: number
  avg_trade?: number
  leverage?: number
  min_gain_usd?: number
  start_index?: number
  fee_rate?: number
  slippage_rate?: number
  symbol?: string
  bars?: number
}

export type JobStatus = {
  id: string
  kind: string
  status: 'queued' | 'running' | 'done' | 'error' | string
  pct: number
  phase?: string
  symbol?: string | null
  bars?: number
  pages?: number
  done?: number
  total?: number
  error?: string | null
  result?: BacktestResult | OptimizeResult | null
}

export type SettingsField = {
  key: string
  label: string
  type: 'float' | 'int' | 'bool' | 'select' | 'symbol' | 'symbols' | 'gates'
  group: string
  help: string
  min?: number
  max?: number
  step?: number
  options?: string[]
}

export type ExchangeInfo = {
  id: string
  label: string
  fee_rate: number
  slippage_rate: number
}

export type ModeStatus = {
  mode: 'paper' | 'live' | string
  exchange: string
  keys: { present: boolean; key_hint: string; env: string[] }
  exchanges: string[]
}

export type ScanCandidate = {
  symbol: string
  signal: string
  score: number
  price: number | null
  atr_pct: number
}

export type TradeRecord = TradeFill & { ts?: number; mode?: string }


export type BacktestJob = {
  status: 'idle' | 'running' | 'done' | 'error' | string
  pct: number
  phase?: string
  symbol?: string | null
  error?: string | null
  done?: number
  total?: number
  result?: BacktestResult | null
}

export type OptimizeResult = {
  settings: Partial<StrategySettings> | null
  qualified: boolean
  message?: string
  holdout: BacktestResult
  train: BacktestResult
  bars: number
  split: number
}

export type DecisionCheck = {
  id: string
  label: string
  ok: boolean
  enabled?: boolean
  detail: string
  value?: number | null
}

export type DecisionSnapshot = {
  action: 'BUY' | 'SELL' | 'HOLD' | string
  reason: string
  raw_signal?: string
  price?: number
  score?: number | null
  tp_pct?: number | null
  sl_pct?: number | null
  est_pnl?: number | null
  min_gain_usd?: number
  leverage?: number
  checks: DecisionCheck[]
  ts?: number
  error?: string
}

export type TradeAnalysis = {
  model: string
  action: 'BUY' | 'SELL' | 'HOLD'
  confidence: number
  entry?: number | null
  stop?: number | null
  take_profit?: number | null
  rationale: string
  risks: string
  raw: string
}

export const DEFAULT_SETTINGS: StrategySettings = {
  exchange: 'binance',
  symbol: 'INJUSDT',
  interval: '1m',
  poll_seconds: 2,
  position_pct: 100,
  leverage: 5,
  min_gain_usd: 0.1,
  fee_rate: 0.0002,
  slippage_rate: 0.0001,
  take_profit_pct: 0.9,
  stop_loss_pct: 0.25,
  min_profit_pct: 0.22,
  max_hold_bars: 8,
  max_spread_bps: 12,
  min_book_imbalance: 0.02,
  rsi_buy: 45,
  rsi_sell: 50,
  rsi_period: 4,
  sma_fast: 8,
  sma_slow: 21,
  vwap_period: 20,
  bb_period: 20,
  bb_std: 2,
  volume_period: 20,
  min_volume_ratio: 0.25,
  vwap_distance_pct: 0.05,
  buy_score: 2,
  breakout_period: 5,
  breakout_bps: 1,
  breakout_volume_ratio: 1.1,
  min_atr_pct: 0.05,
  min_trend_slope_pct: -0.05,
  atr_period: 14,
  trend_tolerance_pct: 1.1,
  trail_pct: 0.35,
  atr_tp_mult: 1.0,
  atr_sl_mult: 0.7,
  vwap_z_entry: 1.8,
  cooldown_bars: 1,
  fee_edge_mult: 3,
  atr_fee_lambda: 1,
  auto_scan: true,
  scan_symbols: 12,
  scan_seconds: 5,
  ai_scan: false,
  min_entry_score: 0,
  expectancy_window: 40,
  expectancy_halt: true,
  scan_whitelist: ["INJUSDT","PYTHUSDT","AAVEUSDT","SUIUSDT","ADAUSDT","SOLUSDT","AVAXUSDT","ATOMUSDT","ETHUSDT","WIFUSDT","XRPUSDT","DOGEUSDT"],
}


export const INDICATOR_PRESETS: Record<
  IndicatorId,
  { label: string; period: number; pane: IndicatorConfig['pane']; std?: number; color: string }
> = {
  ema: { label: 'EMA', period: 9, pane: 'price', color: '#2557ff' },
  sma: { label: 'SMA', period: 20, pane: 'price', color: '#6b4fd8' },
  vwap: { label: 'VWAP', period: 20, pane: 'price', color: '#c47a12' },
  bb: { label: 'Bollinger', period: 20, pane: 'price', std: 2, color: '#5b6b7c' },
  rsi: { label: 'RSI', period: 14, pane: 'rsi', color: '#5b6b7c' },
  atr: { label: 'ATR', period: 14, pane: 'atr', color: '#e03e52' },
}

const COLOR_CYCLE = ['#2557ff', '#6b4fd8', '#c47a12', '#0d9b6c', '#e03e52', '#0891b2', '#db2777', '#65a30d']

export function newIndicatorUid() {
  return `ind_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`
}

export function createIndicator(id: IndicatorId, existing: IndicatorConfig[] = []): IndicatorConfig {
  const preset = INDICATOR_PRESETS[id]
  const sameCount = existing.filter((i) => i.id === id).length
  // Stagger duplicate periods so EMA+EMA become 9 then 21, etc.
  const staggered: Record<IndicatorId, number[]> = {
    ema: [9, 21, 50, 100],
    sma: [10, 20, 50, 200],
    rsi: [7, 14, 21],
    atr: [7, 14, 21],
    vwap: [20, 40, 60],
    bb: [20, 20, 20],
  }
  const periods = staggered[id]
  const period = periods[Math.min(sameCount, periods.length - 1)] ?? preset.period
  return {
    uid: newIndicatorUid(),
    id,
    label: preset.label,
    visible: true,
    period,
    color: COLOR_CYCLE[(existing.length + sameCount) % COLOR_CYCLE.length] ?? preset.color,
    pane: preset.pane,
    std: preset.std,
  }
}

/** Default: empty — user adds indicators. */
export const DEFAULT_INDICATORS: IndicatorConfig[] = []


// Attach default gates after GATE constants exist
DEFAULT_SETTINGS.gates = { ...DEFAULT_GATES }
DEFAULT_SETTINGS.min_gain_usd = 0.1
