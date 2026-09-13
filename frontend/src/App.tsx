import { useCallback, useEffect, useMemo, useState } from 'react'
import { AnalysisPanel } from '@/components/AnalysisPanel'
import { BacktestChart } from '@/components/BacktestChart'
import { BacktestStats } from '@/components/BacktestStats'
import { Chart, type ChartLevels } from '@/components/Chart'
import { ConfigPanel } from '@/components/ConfigPanel'
import { DecisionPanel } from '@/components/DecisionPanel'
import { GatePanel } from '@/components/GatePanel'
import { IndicatorPanel } from '@/components/IndicatorPanel'
import { JobRunner } from '@/components/JobRunner'
import { SettingsPanel } from '@/components/SettingsPanel'
import { TradesPanel } from '@/components/TradesPanel'
import { Button } from '@/components/ui/button'
import { Card, CardBody, CardHeader, CardTitle } from '@/components/ui/card'
import { Modal } from '@/components/ui/modal'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import { Switch } from '@/components/ui/switch'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { api } from '@/lib/api'
import {
  DEFAULT_GATES,
  DEFAULT_INDICATORS,
  DEFAULT_SETTINGS,
  type BacktestFill,
  type BacktestResult,
  type BotState,
  type Candle,
  type ChartType,
  type DecisionSnapshot,
  type GateMap,
  type IndicatorConfig,
  type JobStatus,
  type ModeStatus,
  type BacktestJob,
  type OptimizeResult,
  type ScanCandidate,
  type StrategySettings,
  type TradeAnalysis,
  type TradeEvent,
  type TradeFill,
} from '@/lib/types'
import { cash, cn, fmt, pairLabel } from '@/lib/utils'

const RANGES = [
  { value: '120', label: '120 bars' },
  { value: '240', label: '240 bars' },
  { value: '600', label: '600 bars' },
  { value: '1200', label: '1200 bars' },
]

const CHART_TYPES: { value: ChartType; label: string }[] = [
  { value: 'candles', label: 'Candles' },
  { value: 'bars', label: 'Bars' },
  { value: 'line', label: 'Line' },
  { value: 'area', label: 'Area' },
  { value: 'heikin', label: 'Heikin-Ashi' },
]

const INTERVALS = ['1m', '2m', '3m', '5m', '10m', '15m', '30m', '1h', '2h', '4h', '1d']

const UI_KEY = 'FASTTRADE_UI_V1'

type UiPrefs = {
  chartType?: ChartType
  indicators?: IndicatorConfig[]
  showSignals?: boolean
  symbol?: string
  interval?: string
  range?: string
}

function loadUi(): UiPrefs {
  try {
    const raw = localStorage.getItem(UI_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw) as UiPrefs
    return typeof parsed === 'object' && parsed ? parsed : {}
  } catch {
    return {}
  }
}

type RunState<R, J = JobStatus> = { job: J | null; result: R | null; error: string }

const idleRun = <R, J = JobStatus>(): RunState<R, J> => ({ job: null, result: null, error: '' })


export default function App() {
  const [settings, setSettings] = useState<StrategySettings>(() => {
    const ui = loadUi()
    return {
      ...DEFAULT_SETTINGS,
      symbol: ui.symbol ?? DEFAULT_SETTINGS.symbol,
      interval: ui.interval ?? DEFAULT_SETTINGS.interval,
    }
  })
  const [indicators, setIndicators] = useState<IndicatorConfig[]>(() => loadUi().indicators ?? DEFAULT_INDICATORS)
  const [showSignals, setShowSignals] = useState(() => loadUi().showSignals ?? true)
  const [chartType, setChartType] = useState<ChartType>(() => loadUi().chartType ?? 'candles')
  const [range, setRange] = useState<string>(() => loadUi().range ?? '240')

  const [state, setState] = useState<BotState | null>(null)
  const [candles, setCandles] = useState<Candle[]>([])
  const [livePrice, setLivePrice] = useState<number | undefined>(undefined)
  const [trades, setTrades] = useState<TradeEvent[]>([])
  const [markets, setMarkets] = useState<string[]>([])
  const [sse, setSse] = useState<'connecting' | 'live' | 'reconnecting'>('connecting')
  const [chartError, setChartError] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')

  const [backtest, setBacktest] = useState<RunState<BacktestResult, BacktestJob>>(idleRun)
  const [history, setHistory] = useState<RunState<BacktestResult, BacktestJob>>(idleRun)
  const [optimize, setOptimize] = useState<RunState<OptimizeResult>>(idleRun)
  const [busy, setBusy] = useState('')

  const [analysis, setAnalysis] = useState<TradeAnalysis | null>(null)
  const [analysisText, setAnalysisText] = useState('')
  const [analysisError, setAnalysisError] = useState('')
  const [decision, setDecision] = useState<DecisionSnapshot | null>(null)
  const [mode, setMode] = useState<ModeStatus | null>(null)
  const [confirmLive, setConfirmLive] = useState(false)
  const [actionError, setActionError] = useState('')
  const [starting, setStarting] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [help, setHelp] = useState<Record<string, string>>({})
  const [scan, setScan] = useState<ScanCandidate[]>([])
  const [scanError, setScanError] = useState('')
  const [tab, setTab] = useState('chart')

  const gates: GateMap = useMemo(() => ({ ...DEFAULT_GATES, ...settings.gates }), [settings.gates])
  const setGates = (next: GateMap) => setSettings((s) => ({ ...s, gates: next }))

  const live = mode?.mode === 'live'
  const running = !!state?.running
  const activeIndicators = indicators.filter((i) => i.visible)
  const symbolOptions = markets.length
    ? markets.includes(settings.symbol)
      ? markets
      : [settings.symbol, ...markets]
    : [settings.symbol]

  const equity = useMemo(() => {
    if (typeof state?.equity === 'number') return state.equity
    if (state) return state.cash + state.coin * (livePrice ?? state.price)
    return null
  }, [state, livePrice])

  const winRate = useMemo(() => {
    if (!state || state.trades <= 0) return null
    return (state.wins / state.trades) * 100
  }, [state])

  /* ------------------------------------------------------------------ */
  /* Boot: restore UI prefs, seed server state, streams                 */
  /* ------------------------------------------------------------------ */

  useEffect(() => {
    const ui = loadUi()
    if (ui.chartType) setChartType(ui.chartType)
    if (ui.range) setRange(ui.range)
    api
      .state()
      .then(setState)
      .catch(() => undefined)
    api
      .mode()
      .then(setMode)
      .catch(() => undefined)
    api
      .markets()
      .then(setMarkets)
      .catch(() => undefined)
    api
      .help()
      .then(setHelp)
      .catch(() => undefined)
    api
      .trades(200)
      .then((rows) => setTrades(rows.slice(0, 200)))
      .catch(() => undefined)
  }, [])

  useEffect(() => {
    localStorage.setItem(
      UI_KEY,
      JSON.stringify({ chartType, indicators, showSignals, symbol: settings.symbol, interval: settings.interval, range }),
    )
  }, [chartType, indicators, showSignals, settings.symbol, settings.interval, range])

  const loadCandles = useCallback(async () => {
    try {
      const rows = await api.candles(settings.symbol, settings.interval, Number(range))
      setCandles(rows)
      setChartError('')
    } catch (e) {
      setChartError(e instanceof Error ? e.message : 'Market data unavailable')
    }
  }, [settings.symbol, settings.interval, range])

  /* Candles: 3s while the bot runs, 10s while idle */
  useEffect(() => {
    void loadCandles()
    const period = running ? 3000 : 10000
    const id = setInterval(() => void loadCandles(), period)
    return () => clearInterval(id)
  }, [loadCandles, running])

  /* Ticker: 1s */
  useEffect(() => {
    let alive = true
    const tick = async () => {
      try {
        const t = await api.ticker(settings.symbol)
        if (alive) setLivePrice(t.price)
      } catch {
        /* ticker best-effort */
      }
    }
    void tick()
    const id = setInterval(tick, 1000)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [settings.symbol])

  /* SSE stream: decision / tick / status / mode / backtest / optimize / trade */
  useEffect(() => {
    const es = new EventSource('/events')
    es.onopen = () => setSse('live')
    es.onerror = () => setSse('reconnecting')
    es.onmessage = (ev) => {
      let msg: { kind?: string; data?: unknown; ts?: number }
      try {
        msg = JSON.parse(ev.data) as typeof msg
      } catch {
        return
      }
      if (msg.kind === 'decision') {
        setDecision(msg.data as DecisionSnapshot)
        return
      }
      if (msg.kind === 'tick' || msg.kind === 'status') {
        setState(msg.data as BotState)
        return
      }
      if (msg.kind === 'mode') {
        setMode(msg.data as ModeStatus)
        return
      }
      if (msg.kind === 'backtest') {
        const job = msg.data as BacktestJob
        setBacktest((prev) => ({ ...prev, job }))
        if (job.status === 'done' && job.result) {
          setBacktest((prev) => ({ ...prev, result: job.result as BacktestResult, error: '' }))
        }
        return
      }
      if (msg.kind === 'optimize') {
        const job = msg.data as JobStatus
        setOptimize((prev) => ({ ...prev, job }))
        if (job.status === 'done' && job.result) {
          setOptimize((prev) => ({ ...prev, result: job.result as OptimizeResult, error: '' }))
        }
        return
      }
      if (msg.kind === 'trade') {
        const fill = tradeFill(msg as TradeEvent)
        const event: TradeEvent = { kind: 'trade', ts: msg.ts, data: fill }
        setTrades((prev) => [event, ...prev].slice(0, 200))
        setState((prev) => applyTradeToState(prev, fill))
      }
    }
    return () => es.close()
  }, [])

  /* Poll state / trades / decision while the bot runs */
  useEffect(() => {
    if (!running) return
    const id = setInterval(() => {
      api
        .state()
        .then(setState)
        .catch(() => undefined)
      api
        .trades(200)
        .then((rows) => setTrades(rows.slice(0, 200)))
        .catch(() => undefined)
      api
        .decision()
        .then(setDecision)
        .catch(() => undefined)
    }, 2000)
    return () => clearInterval(id)
  }, [running])

  /* ------------------------------------------------------------------ */
  /* Actions                                                            */
  /* ------------------------------------------------------------------ */

  const save = async () => {
    setSaving(true)
    setSaveError('')
    try {
      await api.saveSettings({ ...settings, gates })
      await loadCandles()
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : 'Could not save settings')
    } finally {
      setSaving(false)
    }
  }

  const start = async () => {
    if (running || starting) return
    setStarting(true)
    setActionError('')
    try {
      await api.saveSettings({ ...settings, gates })
      const next = await api.start()
      setState(next)
      await loadCandles()
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Could not start paper bot')
    } finally {
      setStarting(false)
    }
  }

  const stop = async () => {
    if (!running || stopping) return
    setStopping(true)
    setActionError('')
    try {
      const next = await api.stop()
      setState(next)
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Could not stop paper bot')
    } finally {
      setStopping(false)
    }
  }
  /** Clear stats/trades history; keeps cash+portfolio. */
  const clearStats = async () => {
    setActionError('')
    try {
      const next = await api.clearStats()
      setState(next)
      setTrades([])
      api.trades(200, '').then(setTrades).catch(() => undefined)
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Could not clear stats')
    }
  }

  /** Run a backtest; deep=true loads full exchange history first (slow). */
  const runBacktest = async (deep: boolean) => {
    if (busy) return
    const kind = deep ? 'history' : 'backtest'
    const patch = deep ? setHistory : setBacktest
    setBusy(kind)
    patch(idleRun())
    try {
      await api.runBacktest({ symbol: settings.symbol, history: deep })
      const done = await pollBacktest((j) => patch((prev) => ({ ...prev, job: j })))
      if (done.result) patch((prev) => ({ ...prev, result: done.result as BacktestResult, job: done }))
    } catch (e) {
      patch((prev) => ({ ...prev, error: e instanceof Error ? e.message : 'Backtest failed' }))
    } finally {
      setBusy('')
    }
  }

  const runOptimize = async () => {
    if (busy) return
    setBusy('optimize')
    setOptimize(idleRun())
    try {
      const job = await api.runOptimize(settings.symbol)
      setOptimize((prev) => ({ ...prev, job }))
      const done = await pollJob(job.id, (j) => setOptimize((prev) => ({ ...prev, job: j })))
      if (done.result) setOptimize((prev) => ({ ...prev, result: done.result as OptimizeResult, job: done }))
    } catch (e) {
      setOptimize((prev) => ({
        ...prev,
        error: e instanceof Error ? e.message : 'Optimizer failed',
      }))
    } finally {
      setBusy('')
    }
  }

  /** Guard: only apply optimizer output that qualified on the holdout split. */
  const applyOptimize = async () => {
    const result = optimize.result
    if (!result?.qualified || !result.settings) return
    setBusy('apply')
    try {
      const next: StrategySettings = {
        ...DEFAULT_SETTINGS,
        ...settings,
        ...result.settings,
        gates: { ...DEFAULT_GATES, ...(result.settings.gates ?? settings.gates) },
      }
      await api.saveSettings(next)
      setSettings(next)
      await loadCandles()
    } catch (e) {
      setOptimize((prev) => ({
        ...prev,
        error: e instanceof Error ? e.message : 'Could not apply optimized settings',
      }))
    } finally {
      setBusy('')
    }
  }

  const runAnalyze = async () => {
    if (busy === 'analyze') return
    setBusy('analyze')
    setAnalysisError('')
    setAnalysisText('')
    try {
      const result = await api.analyzeStream({
        symbol: settings.symbol,
        interval: settings.interval,
        limit: Number(range),
        indicators,
        settings: { ...settings, gates },
        live_price: livePrice,
      }, (text) => setAnalysisText((prev) => prev + text))
      setAnalysis(result)
    } catch (e) {
      setAnalysisError(e instanceof Error ? e.message : 'Analysis failed')
    } finally {
      setBusy('')
    }
  }

  const runScan = async () => {
    if (busy === 'scan') return
    setBusy('scan')
    setScanError('')
    try {
      setScan(await api.scan(settings.scan_symbols ?? 12))
    } catch (e) {
      setScanError(e instanceof Error ? e.message : 'Scan failed')
    } finally {
      setBusy('')
    }
  }

  const goPaper = async () => {
    setActionError('')
    try {
      await api.setMode('paper')
      setMode(await api.mode())
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Could not switch to paper mode')
    }
  }

  /** First press raises the confirmation wall; second press (modal) arms live. */
  const armLive = async () => {
    setActionError('')
    try {
      await api.setMode('live')
      setMode(await api.mode())
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Could not arm live mode'
      if (/confirm/i.test(msg)) setConfirmLive(true)
      else setActionError(msg)
    }
  }

  const confirmLiveMode = async () => {
    setActionError('')
    try {
      await api.setMode('live', true)
      setMode(await api.mode())
      setConfirmLive(false)
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Could not arm live mode')
      setConfirmLive(false)
    }
  }

  const applyPreset = async (next: StrategySettings) => {
    setSaving(true)
    setSaveError('')
    try {
      await api.saveSettings({ ...next, gates })
      setSettings(next)
      await loadCandles()
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : 'Could not apply preset')
    } finally {
      setSaving(false)
    }
  }

  const applyConfig = async (payload: {
    settings: StrategySettings
    gates: GateMap
    indicators?: IndicatorConfig[]
  }) => {
    setSettings(payload.settings)
    setGates(payload.gates)
    if (payload.indicators) setIndicators(payload.indicators)
    await api.saveSettings({ ...payload.settings, gates: payload.gates })
    await loadCandles()
  }

  /* ------------------------------------------------------------------ */
  /* Chart levels from open position + decision                          */
  /* ------------------------------------------------------------------ */

  const levels = useMemo<ChartLevels>(() => {
    if (!state || state.entry <= 0) return {}
    const entry = state.entry
    const tpPct = decision?.tp_pct ?? settings.take_profit_pct
    const slPct = decision?.sl_pct ?? settings.stop_loss_pct
    return {
      entry,
      takeProfit: entry * (1 + (tpPct ?? 0) / 100),
      stopLoss: entry * (1 - (slPct ?? 0) / 100),
    }
  }, [state, decision, settings.take_profit_pct, settings.stop_loss_pct])

  /* ------------------------------------------------------------------ */
  /* Render                                                             */
  /* ------------------------------------------------------------------ */

  const modeBadge = (
    <span
      className={cn(
        'rounded-md px-2 py-0.5 font-mono text-[10px] font-bold uppercase tracking-wide',
        running ? 'bg-mint/10 text-mint' : 'bg-line text-steel',
      )}
    >
      {running ? 'RUNNING' : 'STOPPED'}
      {live ? ' · LIVE' : ''}
    </span>
  )

  const chip = (label: string, value: string, toneVal?: 'mint' | 'coral', tip?: string) => (
    <div className="stat-chip" title={tip}>
      <span className="lbl">{label}</span>
      <span className="val" style={toneVal ? { color: toneVal === 'mint' ? 'var(--color-mint)' : 'var(--color-coral)' } : undefined}>
        {value}
      </span>
    </div>
  )

  return (
    <div className="min-h-screen">
      {/* ---------------- Header: white desk ---------------- */}
      <header className="sticky top-0 z-30 border-b border-line bg-panel/95 backdrop-blur">
        <div className="mx-auto flex w-full max-w-[1900px] flex-wrap items-center justify-between gap-4 px-4 py-3">
          <div className="flex items-center gap-3">
            <div className="brand-mark">F</div>
            <div>
              <div className="font-display text-lg font-extrabold leading-none tracking-tight">
                FASTTRADE<span className="ml-1.5 font-medium text-steel">Bot</span>
              </div>
              <div className="font-mono text-[10px] uppercase tracking-widest text-steel">
                {pairLabel(settings.symbol)} · {settings.interval}
              </div>
            </div>
            {modeBadge}
          </div>
          <div className="flex flex-wrap items-center gap-2" title={help['equity']}>
            {chip('Equity', equity != null ? cash(equity) : '—')}
            {chip('Realized PnL', money(state?.realized_pnl, true), toneFor(state?.realized_pnl), help['realized_pnl'])}
            {chip('Unrealized', money(state?.pnl, true), toneFor(state?.pnl))}
            {chip('Trades', state ? String(state.trades) : '—')}
            {chip('Win%', winRate != null ? `${winRate.toFixed(0)}%` : '—')}
            {chip('Price', livePrice != null ? fmt(livePrice) : state ? fmt(state.price) : '—', undefined, help['price'])}
          </div>
        </div>
        <div className="mx-auto flex w-full max-w-[1900px] flex-wrap items-center gap-2 px-4 pb-3">
          {/* symbol */}
          <Select value={settings.symbol} onValueChange={(v) => setSettings((s) => ({ ...s, symbol: v }))}>
            <SelectTrigger className="h-8 w-[150px] text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {symbolOptions.map((m) => (
                <SelectItem key={m} value={m}>
                  {m}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {/* interval */}
          <Select value={settings.interval} onValueChange={(v) => setSettings((s) => ({ ...s, interval: v }))}>
            <SelectTrigger className="h-8 w-[86px] text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {INTERVALS.map((iv) => (
                <SelectItem key={iv} value={iv}>
                  {iv}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {/* range */}
          <Select value={range} onValueChange={setRange}>
            <SelectTrigger className="h-8 w-[110px] text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {RANGES.map((r) => (
                <SelectItem key={r.value} value={r.value}>
                  {r.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {/* chart type */}
          <Select value={chartType} onValueChange={(v) => setChartType(v as ChartType)}>
            <SelectTrigger className="h-8 w-[110px] text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {CHART_TYPES.map((t) => (
                <SelectItem key={t.value} value={t.value}>
                  {t.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Separator orientation="vertical" className="mx-1 h-6" />

          {/* paper / live */}
          <div className="flex items-center gap-2 rounded-md border border-line bg-paper px-2 py-1">
            <span className={cn('font-mono text-[10px] uppercase', live ? 'text-steel' : 'font-bold text-ink')}>Paper</span>
            <Switch checked={live} onCheckedChange={(v) => (v ? void armLive() : void goPaper())} aria-label="Live mode" />
            <span className={cn('font-mono text-[10px] uppercase', live ? 'font-bold text-coral' : 'text-steel')}>Live</span>
          </div>

          <Button size="sm" onClick={start} disabled={running || starting}>
            {starting ? 'Starting…' : 'Start'}
          </Button>
          <Button size="sm" variant="danger" onClick={stop} disabled={!running || stopping}>
            {stopping ? 'Stopping…' : 'Stop'}
          </Button>

          <div className="ml-auto flex items-center gap-2">
            <span
              className={cn(
                'size-1.5 rounded-full',
                sse === 'live' ? 'live-dot bg-mint' : sse === 'reconnecting' ? 'bg-amber' : 'bg-steel',
              )}
            />
            <span className="font-mono text-[10px] uppercase text-steel">SSE {sse}</span>
          </div>
        </div>
      </header>
      <div className="desk-rail" data-live={sse === 'live'} />

      {actionError ? (
        <div className="mx-auto w-full max-w-[1900px] px-4 pt-3">
          <p className="rounded-md border border-coral/30 bg-coral/5 px-3 py-2 font-mono text-[11px] text-coral">
            {actionError}
          </p>
        </div>
      ) : null}
      <main className="mx-auto w-full max-w-[1900px] px-4 pb-10 pt-4">
        <Tabs value={tab} onValueChange={setTab}>
          <TabsList>
            <TabsTrigger value="chart">Chart</TabsTrigger>
            <TabsTrigger value="trades">Trades</TabsTrigger>
            <TabsTrigger value="backtest">Backtest</TabsTrigger>
            <TabsTrigger value="history">History</TabsTrigger>
            <TabsTrigger value="optimize">Optimize</TabsTrigger>
            <TabsTrigger value="settings">Settings</TabsTrigger>
            <TabsTrigger value="live">Live</TabsTrigger>
          </TabsList>

          {/* ---------------- Chart tab ---------------- */}
          <TabsContent value="chart" className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
            <div className="min-w-0 space-y-4">
              <Card>
                <CardHeader>
                  <CardTitle>{pairLabel(settings.symbol)}</CardTitle>
                  <span className="font-mono text-[10px] text-steel">
                    {activeIndicators.length ? `${activeIndicators.length} active indicators` : 'no indicators'}
                    {state?.action && state.action !== '-' ? ` · ${state.action}` : ''}
                  </span>
                </CardHeader>
                <CardBody className="h-[520px]">
                  {chartError ? (
                    <p className="rounded-md border border-coral/30 bg-coral/5 px-3 py-2 font-mono text-[11px] text-coral">
                      {chartError}
                    </p>
                  ) : (
                    <Chart
                      candles={candles}
                      indicators={indicators}
                      livePrice={livePrice}
                      trades={trades}
                      symbol={settings.symbol}
                      showSignals={showSignals}
                      rsiBuy={settings.rsi_buy}
                      rsiSell={settings.rsi_sell}
                      chartType={chartType}
                      levels={levels}
                    />
                  )}
                </CardBody>
                <div className="flex items-center justify-between border-t border-line px-4 py-2 font-mono text-[10px] text-steel">
                  <span>{followHint(candles, livePrice)}</span>
                  <span>{levels.entry ? `entry ${fmt(levels.entry)}` : 'flat'}</span>
                </div>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Trades</CardTitle>
                  <span className="font-mono text-[10px] text-steel">{trades.length} fills</span>
                </CardHeader>
                <CardBody>
                  <TradesPanel trades={trades} onClear={clearStats} />
                </CardBody>
              </Card>
            </div>

            <div className="space-y-4">
              <Card>
                <CardHeader>
                  <CardTitle>Account</CardTitle>
                  <span className="font-mono text-[10px] text-steel">{live ? 'LIVE' : 'PAPER'}</span>
                </CardHeader>
                <CardBody className="space-y-0">
                  <Stat label="Mode" value={live ? 'LIVE' : 'PAPER'} />
                  <Stat label="Exchange" value={state?.exchange ?? mode?.exchange ?? settings.exchange} />
                  <Stat label="Equity" value={equity != null ? cash(equity) : '—'} />
                  <Stat label="Cash" value={state ? cash(state.cash) : '—'} />
                  <Stat label="Coin" value={state ? `${fmt(state.coin, 4)}` : '—'} />
                  <Stat label="Open entry" value={state && state.entry > 0 ? fmt(state.entry) : '—'} />
                  <Stat label="Unrealized P/L" value={money(state?.pnl, true)} tone={toneFor(state?.pnl)} />
                  <Stat label="Realized P/L" value={money(state?.realized_pnl, true)} tone={toneFor(state?.realized_pnl)} />
                  <Stat label="Completed trades" value={state ? String(state.trades) : '—'} />
                  <Stat label="Win rate" value={winRate != null ? `${winRate.toFixed(0)}%` : '—'} />
                  <Stat label="Spread" value={state ? `${fmt(state.spread_bps, 1)} bp` : '—'} />
                  <Stat label="Book imbalance" value={state ? fmt(state.imbalance, 3) : '—'} />
                  <Stat label="Min gain" value={`$${fmt(settings.min_gain_usd ?? 0.1)}`} />
                  <Stat label="Leverage" value={`${settings.leverage ?? 1}x`} />
                  {state?.error ? (
                    <p className="mt-2 rounded-md border border-coral/30 bg-coral/5 px-2 py-1.5 font-mono text-[11px] text-coral">
                      {state.error}
                    </p>
                  ) : null}
                </CardBody>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Indicators</CardTitle>
                  <span className="font-mono text-[10px] text-steel">{activeIndicators.length} active</span>
                </CardHeader>
                <CardBody>
                  <IndicatorPanel
                    indicators={indicators}
                    onChange={setIndicators}
                    showSignals={showSignals}
                    onShowSignals={setShowSignals}
                  />
                </CardBody>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>AI</CardTitle>
                  <span className="font-mono text-[10px] text-steel">{analysis ? analysis.model.split('/').pop() : 'NIM'}</span>
                </CardHeader>
                <CardBody>
                  <AnalysisPanel
                    analysis={analysis}
                    streamText={analysisText}
                    error={analysisError}
                    loading={busy === 'analyze'}
                    onRun={runAnalyze}
                  />
                </CardBody>
              </Card>
            </div>
          </TabsContent>

          {/* ---------------- Trades tab ---------------- */}
          <TabsContent value="trades" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Executed {live ? 'live' : 'paper'} trades</CardTitle>
                <span className="font-mono text-[10px] text-steel">{trades.length} fills · newest first</span>
              </CardHeader>
              <CardBody>
                <TradesPanel trades={trades} onClear={clearStats} />
              </CardBody>
            </Card>
          </TabsContent>

          {/* ---------------- Backtest tab ---------------- */}
          <TabsContent value="backtest" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Backtest</CardTitle>
                <span className="font-mono text-[10px] text-steel">{pairLabel(settings.symbol)} · recent window</span>
              </CardHeader>
              <CardBody className="space-y-4">
                <JobRunner
                  title="Backtest result"
                  description="Replay the recent window against the saved strategy. Uses current settings server-side."
                  runLabel="Run backtest"
                  runningLabel="Running…"
                  busy={busy === 'backtest'}
                  job={backtest.job as JobStatus | null}
                  error={backtest.error}
                  onRun={() => void runBacktest(false)}
                  summary={
                    backtest.result
                      ? `${backtest.result.trades} trades · net ${money(backtest.result.net_pnl ?? backtest.result.ending_equity - backtest.result.capital, true)}`
                      : undefined
                  }
                />
                {backtest.result ? (
                  <>
                    <BacktestChart result={backtest.result} />
                    <BacktestStats result={backtest.result} />
                    {backtest.result.fills?.length ? (
                      <div className="space-y-2">
                        <h3 className="font-display text-xs font-bold">Executed fills</h3>
                        <FillsTable fills={backtest.result.fills} />
                      </div>
                    ) : null}
                  </>
                ) : (
                  <p className="rounded-lg border border-dashed border-line bg-paper/50 px-3 py-6 text-center text-xs text-steel">
                    No backtest yet — run one to see the equity curve and fill markers.
                  </p>
                )}
              </CardBody>
            </Card>
          </TabsContent>

          {/* ---------------- History tab ---------------- */}
          <TabsContent value="history" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>History backtest</CardTitle>
                <span className="font-mono text-[10px] text-steel">{pairLabel(settings.symbol)} · full exchange history</span>
              </CardHeader>
              <CardBody className="space-y-4">
                <JobRunner
                  title="History result"
                  description="Loads the full candle history from the exchange (HF scale, not a profit guarantee), then replays the whole set."
                  runLabel="Run history backtest"
                  runningLabel="Loading history…"
                  busy={busy === 'history'}
                  job={history.job as JobStatus | null}
                  error={history.error}
                  onRun={() => void runBacktest(true)}
                  summary={
                    history.result
                      ? `${history.result.trades} trades · return ${(history.result.return_pct ?? 0).toFixed(2)}%`
                      : undefined
                  }
                />
                {history.result ? (
                  <>
                    <BacktestChart result={history.result} />
                    <BacktestStats result={history.result} />
                    {history.result.fills?.length ? (
                      <div className="space-y-2">
                        <h3 className="font-display text-xs font-bold">Executed fills</h3>
                        <FillsTable fills={history.result.fills} />
                      </div>
                    ) : null}
                  </>
                ) : (
                  <p className="rounded-lg border border-dashed border-line bg-paper/50 px-3 py-6 text-center text-xs text-steel">
                    No history run yet — first run downloads every available candle for this pair.
                  </p>
                )}
              </CardBody>
            </Card>
          </TabsContent>

          {/* ---------------- Optimize tab ---------------- */}
          <TabsContent value="optimize" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Optimizer</CardTitle>
                <span className="font-mono text-[10px] text-steel">{pairLabel(settings.symbol)} · train/holdout split</span>
              </CardHeader>
              <CardBody className="space-y-4">
                <JobRunner
                  title="Optimizer result"
                  description="Grid-searches parameters on a train split and keeps only candidates that stay profitable on a held-out tail. Nothing is applied automatically."
                  runLabel="Run optimizer"
                  runningLabel="Searching…"
                  busy={busy === 'optimize'}
                  job={optimize.job}
                  error={optimize.error}
                  onRun={runOptimize}
                />
                {optimize.result ? (
                  <div className="space-y-3">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <span
                        className={cn(
                          'rounded-md px-2 py-0.5 font-mono text-[11px] font-bold',
                          optimize.result.qualified ? 'bg-mint/15 text-mint' : 'bg-coral/15 text-coral',
                        )}
                      >
                        {optimize.result.qualified ? 'QUALIFIED' : 'NOT QUALIFIED'}
                      </span>
                      <Button onClick={applyOptimize} disabled={!(optimize.result.qualified && optimize.result.settings)}>
                        Apply optimized settings
                      </Button>
                    </div>
                    {optimize.result.message ? (
                      <p
                        className={cn(
                          'rounded-md border px-3 py-2 font-mono text-[11px]',
                          optimize.result.qualified
                            ? 'border-mint/30 bg-mint/5 text-mint'
                            : 'border-coral/30 bg-coral/5 text-coral',
                        )}
                      >
                        {optimize.result.message}
                      </p>
                    ) : null}
                    <div className="grid gap-4 lg:grid-cols-2">
                      <div className="rounded-lg border border-line p-3">
                        <div className="mb-2 font-display text-xs font-bold">Train split</div>
                        <Stat label="Net P/L" value={money(optimize.result.train.net_pnl, true)} tone={toneFor(optimize.result.train.net_pnl)} />
                        <Stat label="Trades" value={String(optimize.result.train.trades)} />
                        <Stat label="Win rate" value={`${(optimize.result.train.win_rate * 100).toFixed(0)}%`} />
                        <Stat label="Profit factor" value={fmt(optimize.result.train.profit_factor)} />
                      </div>
                      <div className="rounded-lg border border-line p-3">
                        <div className="mb-2 font-display text-xs font-bold">Holdout split</div>
                        <Stat label="Net P/L" value={money(optimize.result.holdout.net_pnl, true)} tone={toneFor(optimize.result.holdout.net_pnl)} />
                        <Stat label="Trades" value={String(optimize.result.holdout.trades)} />
                        <Stat label="Win rate" value={`${(optimize.result.holdout.win_rate * 100).toFixed(0)}%`} />
                        <Stat label="Profit factor" value={fmt(optimize.result.holdout.profit_factor)} />
                      </div>
                    </div>
                  </div>
                ) : null}
              </CardBody>
            </Card>
          </TabsContent>

          {/* ---------------- Settings tab ---------------- */}
          <TabsContent value="settings" className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Strategy</CardTitle>
                <span className="font-mono text-[10px] text-steel">{saveError ? 'save failed' : 'schema driven'}</span>
              </CardHeader>
              <CardBody>
                <SettingsPanel
                  settings={settings}
                  onChange={setSettings}
                  onApplyPreset={(next) => void applyPreset(next)}
                  onSave={() => void save()}
                  saving={saving}
                  saveError={saveError}
                />
              </CardBody>
            </Card>
            <div className="space-y-4">
              <Card>
                <CardHeader>
                  <CardTitle>Gates</CardTitle>
                  <span className="font-mono text-[10px] text-steel">
                    {Object.values({ ...DEFAULT_GATES, ...gates }).filter(Boolean).length} enabled
                  </span>
                </CardHeader>
                <CardBody>
                  <GatePanel gates={gates} onChange={setGates} />
                </CardBody>
              </Card>
              <Card>
                <CardHeader>
                  <CardTitle>Configs</CardTitle>
                  <span className="font-mono text-[10px] text-steel">save / load whole desk</span>
                </CardHeader>
                <CardBody>
                  <ConfigPanel
                    settings={settings}
                    gates={gates}
                    indicators={indicators}
                    onApply={(payload) => void applyConfig(payload)}
                  />
                </CardBody>
              </Card>
            </div>
          </TabsContent>

          {/* ---------------- Live tab ---------------- */}
          <TabsContent value="live" className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <Card>
              <CardHeader>
                <CardTitle>Live decision</CardTitle>
                <span className="font-mono text-[10px] text-steel">
                  {decision?.action ?? '—'}
                  {decision?.score != null ? ` · score ${fmt(decision.score)}` : ''}
                </span>
              </CardHeader>
              <CardBody>
                <DecisionPanel decision={decision} />
              </CardBody>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Market scan</CardTitle>
                <Button size="sm" onClick={runScan} disabled={busy === 'scan'}>
                  {busy === 'scan' ? 'Scanning…' : 'Scan markets'}
                </Button>
              </CardHeader>
              <CardBody className="space-y-3">
                {scanError ? (
                  <p className="rounded-md border border-coral/30 bg-coral/5 px-2 py-1.5 font-mono text-[11px] text-coral">
                    {scanError}
                  </p>
                ) : null}
                {scan.length ? (
                  <div className="max-h-[420px] overflow-auto rounded-lg border border-line">
                    <table className="w-full border-collapse font-mono text-[11px]">
                      <thead>
                        <tr className="border-b border-line bg-paper text-left text-steel">
                          <th className="px-2 py-1.5 font-normal">SYMBOL</th>
                          <th className="px-2 py-1.5 font-normal">SIGNAL</th>
                          <th className="px-2 py-1.5 text-right font-normal">SCORE</th>
                          <th className="px-2 py-1.5 text-right font-normal">PRICE</th>
                          <th className="px-2 py-1.5 text-right font-normal">ATR%</th>
                        </tr>
                      </thead>
                      <tbody>
                        {scan.map((c) => (
                          <tr key={c.symbol} className="border-b border-line/50 last:border-0">
                            <td className="px-2 py-1">
                              <button
                                type="button"
                                className={cn(
                                  'font-semibold hover:text-cobalt',
                                  c.symbol === settings.symbol ? 'text-cobalt' : 'text-ink',
                                )}
                                onClick={() => setSettings((s) => ({ ...s, symbol: c.symbol }))}
                              >
                                {c.symbol}
                              </button>
                            </td>
                            <td className="px-2 py-1">
                              <span
                                className={cn(
                                  'rounded px-1.5 py-0.5 text-[10px] font-bold',
                                  c.signal === 'BUY' ? 'bg-mint/15 text-mint' : c.signal === 'SELL' ? 'bg-coral/15 text-coral' : 'bg-line text-steel',
                                )}
                              >
                                {c.signal}
                              </span>
                            </td>
                            <td className="px-2 py-1 text-right">{fmt(c.score)}</td>
                            <td className="px-2 py-1 text-right">{c.price != null ? fmt(c.price) : '—'}</td>
                            <td className="px-2 py-1 text-right text-steel">{c.atr_pct.toFixed(2)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <p className="rounded-lg border border-dashed border-line bg-paper/50 px-3 py-6 text-center text-xs text-steel">
                    No scan yet — ranks {settings.scan_symbols ?? 12} hot markets by signal score.
                  </p>
                )}
                <Separator />
                <p className="font-mono text-[10px] text-steel">
                  Experimental paper bot. No profit guarantee; live orders need explicit keys + risk review.
                </p>
              </CardBody>
            </Card>
          </TabsContent>
        </Tabs>
      </main>

      {/* ---------------- Live arm confirmation ---------------- */}
      <Modal open={confirmLive} title="Arm live trading" onClose={() => setConfirmLive(false)}>
        <div className="space-y-3">
          <p className="text-sm">
            You are about to switch from paper to <b>live</b> on {mode?.exchange ?? settings.exchange}. Orders will use
            your real exchange keys and real funds.
          </p>
          <p className="rounded-md border border-coral/30 bg-coral/5 px-3 py-2 font-mono text-[11px] text-coral">
            {mode?.keys?.present
              ? `Keys detected: ${mode.keys.env.join(' + ')} (${mode.keys.key_hint})`
              : 'No API keys detected — arming will fail until keys are present.'}
          </p>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setConfirmLive(false)}>
              Cancel
            </Button>
            <Button variant="danger" onClick={() => void confirmLiveMode()}>
              Arm live
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/* Helpers (bottom of module)                                         */
/* ------------------------------------------------------------------ */

/** Backtest/history progress lives on /api/backtest/status (not the generic job bus). */
async function pollBacktest(onUpdate: (job: BacktestJob) => void): Promise<BacktestJob> {
  for (let i = 0; i < 1200; i++) {
    const job = await api.backtestStatus()
    onUpdate(job)
    if (job.status === 'done') return job
    if (job.status === 'error') throw new Error(job.error || 'Backtest failed')
    await delay(500)
  }
  throw new Error('Backtest is taking unusually long — it keeps running server-side; check back shortly.')
}

function delay(ms: number): Promise<void> {
  const { promise, resolve } = Promise.withResolvers<void>()
  setTimeout(resolve, ms)
  return promise
}
/** Merge an SSE trade event's nested data with its top-level fill fields. */
function tradeFill(t: TradeEvent): TradeFill {
  const merged = { ...(t.data ?? {}), ...(t as unknown as Partial<TradeFill>) }
  return merged as TradeFill
}

/** Fold a fill into the bot state snapshot (server also pushes authoritative state). */
function applyTradeToState(prev: BotState | null, fill: TradeFill | null): BotState | null {
  if (!prev || !fill) return prev
  const next = { ...prev }
  if (typeof fill.cash === 'number') next.cash = fill.cash
  if (typeof fill.coin === 'number') next.coin = fill.coin
  if (typeof fill.trades === 'number') next.trades = fill.trades
  if (typeof fill.wins === 'number') next.wins = fill.wins
  if (typeof fill.realized_pnl === 'number') next.realized_pnl = fill.realized_pnl
  if (typeof fill.equity === 'number') next.equity = fill.equity
  if (typeof fill.price === 'number') next.price = fill.price
  if (fill.symbol) next.symbol = fill.symbol
  if (fill.side === 'BUY') {
    next.action = 'BUY'
    next.entry = fill.price ?? next.entry
  } else if (fill.side === 'SELL') {
    next.action = 'SELL'
    next.entry = 0
    next.pnl = typeof fill.pnl === 'number' ? fill.pnl : 0
  }
  return next
}

/** Poll a job endpoint until done/error; throws on failure or timeout. */
async function pollJob(id: string, onUpdate: (job: JobStatus) => void): Promise<JobStatus> {
  for (let i = 0; i < 900; i++) {
    const job = await api.job(id)
    onUpdate(job)
    if (job.status === 'done') return job
    if (job.status === 'error') throw new Error(job.error || 'Job failed')
    await delay(400)
  }
  throw new Error('Job is taking unusually long — it keeps running server-side; check back shortly.')
}

/** Footer hint under the live chart: bar count, last candle time, live price. */
function followHint(candles: Candle[], livePrice?: number): string {
  if (!candles.length) return 'waiting for candles…'
  const last = candles[candles.length - 1]
  const stamp = new Date(last[0]).toLocaleTimeString()
  const tail = typeof livePrice === 'number' ? ` · live ${fmt(livePrice)}` : ''
  return `${candles.length} candles · last ${stamp}${tail}`
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: 'mint' | 'coral' | 'steel' }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-line/60 py-1.5 last:border-0">
      <span className="text-[11px] text-steel">{label}</span>
      <span
        className={cn(
          'font-mono text-[12px] font-semibold',
          tone === 'mint' && 'text-mint',
          tone === 'coral' && 'text-coral',
        )}
      >
        {value}
      </span>
    </div>
  )
}

function money(n: number | null | undefined, tone = false): string {
  if (n == null || !Number.isFinite(n)) return '—'
  if (tone) return `${n >= 0 ? '+' : '-'}$${fmt(Math.abs(n))}`
  return cash(n)
}

function toneFor(n: number | null | undefined): 'mint' | 'coral' | undefined {
  if (n == null || !Number.isFinite(n)) return undefined
  return n >= 0 ? 'mint' : 'coral'
}

/** Compact fills table for finished backtest/optimize runs. */
function FillsTable({ fills }: { fills: BacktestFill[] }) {
  const shown = fills.slice(0, 80)
  return (
    <div className="space-y-2">
      <div className="max-h-[360px] overflow-auto rounded-lg border border-line">
        <table className="w-full border-collapse font-mono text-[11px]">
          <thead>
            <tr className="border-b border-line bg-paper text-left text-steel">
              <th className="px-2 py-1.5 font-normal">TIME</th>
              <th className="px-2 py-1.5 font-normal">SIDE</th>
              <th className="px-2 py-1.5 text-right font-normal">PRICE</th>
              <th className="px-2 py-1.5 text-right font-normal">QTY</th>
              <th className="px-2 py-1.5 text-right font-normal">FEES</th>
              <th className="px-2 py-1.5 text-right font-normal">P/L</th>
              <th className="px-2 py-1.5 font-normal">REASON</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((f, i) => (
              <tr key={i} className="border-b border-line/50 last:border-0">
                <td className="whitespace-nowrap px-2 py-1 text-steel">
                  {new Date(f.ts).toLocaleTimeString()}
                </td>
                <td
                  className={cn(
                    'px-2 py-1 font-semibold',
                    f.side === 'BUY' ? 'text-cobalt' : 'text-coral',
                  )}
                >
                  {f.side}
                </td>
                <td className="px-2 py-1 text-right">{fmt(f.price)}</td>
                <td className="px-2 py-1 text-right">{f.quantity != null ? fmt(f.quantity, 4) : '—'}</td>
                <td className="px-2 py-1 text-right text-steel">{f.fee_paid != null ? fmt(f.fee_paid, 3) : '—'}</td>
                <td
                  className={cn(
                    'px-2 py-1 text-right',
                    f.pnl == null ? 'text-steel' : f.pnl >= 0 ? 'text-mint' : 'text-coral',
                  )}
                >
                  {f.pnl == null ? '—' : money(f.pnl, true)}
                </td>
                <td className="px-2 py-1 text-steel">{f.reason || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {fills.length > shown.length ? (
        <p className="font-mono text-[10px] text-steel">
          Showing {shown.length} of {fills.length} fills — export full set from the server log.
        </p>
      ) : null}
    </div>
  )
}
