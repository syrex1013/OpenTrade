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
        textColor: '#495057',
        fontFamily: "'IBM Plex Mono', monospace",
        fontSize: 11,
      },
      grid: { vertLines: { color: '#f8f9fa' }, horzLines: { color: '#f8f9fa' } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: '#dee2e6' },
      timeScale: { borderColor: '#dee2e6', timeVisible: true, secondsVisible: false },
    })
    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#198754',
      downColor: '#dc3545',
      borderUpColor: '#198754',
      borderDownColor: '#dc3545',
      wickUpColor: '#198754',
      wickDownColor: '#dc3545',
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
      <div className="flex h-[260px] items-center justify-center rounded-none border border-dashed border-line bg-paper/50 font-mono text-xs text-steel">
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
          <span className="text-positive">▲ BUY</span>
          <span className="text-negative">▼ SELL {result.fills?.length ?? 0} fills</span>
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
