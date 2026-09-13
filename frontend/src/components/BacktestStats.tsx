import {
  AreaSeries,
  ColorType,
  createChart,
  CrosshairMode,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from 'lightweight-charts'
import { useEffect, useMemo, useRef } from 'react'
import { Card, CardBody, CardHeader, CardTitle } from '@/components/ui/card'
import type { BacktestResult } from '@/lib/types'
import { cash, fmt } from '@/lib/utils'

type Props = {
  result: BacktestResult | null
}

function drawdownSeries(result: BacktestResult) {
  const curve = result.equity_curve ?? []
  let peak = -Infinity
  return curve.map((p) => {
    peak = Math.max(peak, p.equity)
    return { time: Math.floor(p.t / 1000) as UTCTimestamp, value: peak > 0 ? (p.equity / peak - 1) * 100 : 0 }
  })
}

export function BacktestStats({ result }: Props) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const equityRef = useRef<ISeriesApi<'Line'> | null>(null)
  const ddRef = useRef<ISeriesApi<'Area'> | null>(null)
  const dd = useMemo(() => (result ? drawdownSeries(result) : []), [result])

  useEffect(() => {
    if (!ref.current) return
    const chart = createChart(ref.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: '#ffffff' },
        textColor: '#5b6b7c',
        fontFamily: "'IBM Plex Mono', monospace",
        fontSize: 11,
        panes: { separatorColor: '#d5dbe3', separatorHoverColor: '#2557ff' },
      },
      grid: { vertLines: { color: '#eef1f5' }, horzLines: { color: '#eef1f5' } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: '#d5dbe3' },
      timeScale: { borderColor: '#d5dbe3', timeVisible: true, secondsVisible: false },
    })
    const equity = chart.addSeries(LineSeries, { color: '#2557ff', lineWidth: 2, priceLineVisible: false })
    chart.addPane(true).setStretchFactor(0.3)
    const ddSeries = chart.addSeries(
      AreaSeries,
      {
        lineColor: '#e03e52',
        topColor: 'rgba(224,62,82,0.06)',
        bottomColor: 'rgba(224,62,82,0.30)',
        lineWidth: 1,
        priceLineVisible: false,
      },
      1,
    )
    chart.panes()[0]?.setStretchFactor(1)
    chartRef.current = chart
    equityRef.current = equity
    ddRef.current = ddSeries
    return () => {
      chart.remove()
      chartRef.current = null
      equityRef.current = null
      ddRef.current = null
    }
  }, [])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart || !equityRef.current || !ddRef.current) return
    const curve = result?.equity_curve ?? []
    if (!curve.length) {
      equityRef.current.setData([])
      ddRef.current.setData([])
      return
    }
    equityRef.current.setData(
      curve.map((p) => ({ time: Math.floor(p.t / 1000) as UTCTimestamp, value: p.equity })),
    )
    ddRef.current.setData(dd)
    chart.timeScale().fitContent()
  }, [result, dd])

  if (!result) {
    return (
      <Card>
        <CardBody className="font-mono text-xs text-steel">
          Stats, equity curve and drawdown appear here after a run.
        </CardBody>
      </Card>
    )
  }

  const expectancy = result.trades ? (result.net_pnl ?? 0) / result.trades : 0
  return (
    <Card>
      <CardHeader>
        <CardTitle>Backtest stats</CardTitle>
        <span className="font-mono text-[10px] text-steel">
          fees {(result.fee_rate ?? 0) * 100}% + slippage {(result.slippage_rate ?? 0) * 100}% per leg
        </span>
      </CardHeader>
      <CardBody className="space-y-3 pt-2">
        <div className="grid grid-cols-2 gap-2 rounded-lg border border-line bg-paper/70 p-3 font-mono text-[11px] sm:grid-cols-3 lg:grid-cols-4">
          <Stat label="Return" value={`${result.return_pct.toFixed(2)}%`} tone={result.return_pct >= 0 ? 'mint' : 'coral'} />
          <Stat label="Net PnL" value={cash(result.net_pnl ?? result.ending_equity - result.capital)} tone={(result.net_pnl ?? 0) >= 0 ? 'mint' : 'coral'} />
          <Stat label="Fees paid" value={cash(result.fees_total ?? 0)} tone="coral" />
          <Stat label="Trades" value={String(result.trades)} />
          <Stat label="Win rate" value={`${result.win_rate.toFixed(0)}%`} />
          <Stat label="Profit factor" value={result.profit_factor.toFixed(2)} />
          <Stat label="Max DD" value={`${result.max_drawdown_pct.toFixed(2)}%`} tone="coral" />
          <Stat label="Expectancy" value={cash(expectancy)} tone={expectancy >= 0 ? 'mint' : 'coral'} />
          <Stat label="Avg trade" value={cash(result.avg_trade ?? 0)} />
          <Stat label="Ending equity" value={cash(result.ending_equity)} />
          <Stat label="Leverage" value={`${result.leverage ?? 1}x`} />
          <Stat label="Bars" value={fmt(result.bars ?? 0, 0)} />
        </div>
        <div>
          <div className="mb-1 flex items-center justify-between font-mono text-[10px] text-steel">
            <span className="text-cobalt">Equity</span>
            <span className="text-coral">Drawdown %</span>
          </div>
          <div ref={ref} className="h-[220px] w-full" />
        </div>
      </CardBody>
    </Card>
  )
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: 'mint' | 'coral' }) {
  return (
    <div>
      <div className="text-steel">{label}</div>
      <div className={tone === 'mint' ? 'font-semibold text-mint' : tone === 'coral' ? 'font-semibold text-coral' : 'font-semibold'}>
        {value}
      </div>
    </div>
  )
}
