import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  CrosshairMode,
  type IChartApi,
  type ISeriesApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import { useEffect, useMemo, useRef } from 'react'
import { fillMarkers } from '@/lib/markers'
import type { BacktestResult } from '@/lib/types'

type Props = {
  result: BacktestResult | null
}


export function BacktestChart({ result }: Props) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const markersRef = useRef<{ setMarkers: (m: SeriesMarker<Time>[]) => void } | null>(null)
  const markers = useMemo(() => fillMarkers(result), [result])

  useEffect(() => {
    if (!ref.current) return
    const chart = createChart(ref.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: '#ffffff' },
        textColor: '#5b6b7c',
        fontFamily: "'IBM Plex Mono', monospace",
        fontSize: 11,
      },
      grid: { vertLines: { color: '#eef1f5' }, horzLines: { color: '#eef1f5' } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: '#d5dbe3' },
      timeScale: { borderColor: '#d5dbe3', timeVisible: true, secondsVisible: false },
    })
    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#0d9b6c',
      downColor: '#e03e52',
      borderUpColor: '#0d9b6c',
      borderDownColor: '#e03e52',
      wickUpColor: '#0d9b6c',
      wickDownColor: '#e03e52',
    })
    chartRef.current = chart
    seriesRef.current = series
    markersRef.current = createSeriesMarkers(series, [])
    return () => {
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
      markersRef.current = null
    }
  }, [])

  useEffect(() => {
    const series = seriesRef.current
    const chart = chartRef.current
    if (!series || !chart) return
    const bars = result?.candles ?? []
    if (!bars.length) {
      series.setData([])
      markersRef.current?.setMarkers([])
      return
    }
    series.setData(
      bars.map((b) => ({
        time: Math.floor(b.t / 1000) as UTCTimestamp,
        open: b.o,
        high: b.h,
        low: b.l,
        close: b.c,
      })),
    )
    markersRef.current?.setMarkers(markers)
    chart.timeScale().fitContent()
  }, [result, markers])

  if (!result) {
    return (
      <div className="flex h-[260px] items-center justify-center rounded-lg border border-dashed border-line bg-paper/50 font-mono text-xs text-steel">
        Run a backtest to plot fills on candles
      </div>
    )
  }

  const bars = result.candles ?? []
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between font-mono text-[10px] text-steel">
        <span>{result.symbol ?? ''} · {bars.length} candles</span>
        <span className="inline-flex items-center gap-3">
          <span className="text-mint">▲ BUY</span>
          <span className="text-coral">▼ SELL {result.fills?.length ?? 0} fills</span>
        </span>
      </div>
      <div ref={ref} className="h-[320px] w-full" />
      {!bars.length && (
        <p className="font-mono text-[10px] text-steel">
          No candle payload returned — rerun the backtest.
        </p>
      )}
    </div>
  )
}
