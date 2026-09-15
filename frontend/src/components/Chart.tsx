import {
  AreaSeries,
  BarSeries,
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  CrosshairMode,
  LineSeries,
  LineStyle,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import { useEffect, useRef } from 'react'
import { atr, bollinger, ema, heikinAshi, rsi, sma, vwap } from '@/lib/indicators'
import { tradeMarkers } from '@/lib/markers'
import type { Candle, ChartType, IndicatorConfig, TradeEvent } from '@/lib/types'

export type ChartLevels = {
  entry?: number | null
  takeProfit?: number | null
  stopLoss?: number | null
}

type Props = {
  candles: Candle[]
  indicators: IndicatorConfig[]
  livePrice?: number
  trades?: TradeEvent[]
  symbol?: string
  showSignals?: boolean
  rsiBuy?: number
  rsiSell?: number
  chartType?: ChartType
  levels?: ChartLevels
}

type MainSeries =
  | ISeriesApi<'Candlestick'>
  | ISeriesApi<'Bar'>
  | ISeriesApi<'Line'>
  | ISeriesApi<'Area'>

const PRICE_SERIES: Record<ChartType, string> = {
  candles: 'Candlestick',
  bars: 'Bar',
  line: 'Line',
  area: 'Area',
  heikin: 'Candlestick',
}

function toBars(candles: Candle[], livePrice?: number) {
  const bars = candles.map((c) => ({
    time: Math.floor(c[0] / 1000) as UTCTimestamp,
    open: c[1],
    high: c[2],
    low: c[3],
    close: c[4],
  }))
  if (livePrice && bars.length) {
    const last = bars[bars.length - 1]
    bars[bars.length - 1] = {
      ...last,
      close: livePrice,
      high: Math.max(last.high, livePrice),
      low: Math.min(last.low, livePrice),
    }
  }
  return bars
}

function toLine(candles: Candle[], values: number[]) {
  return candles.map((c, i) => ({
    time: Math.floor(c[0] / 1000) as UTCTimestamp,
    value: values[i],
  }))
}

function signalMarkers(candles: Candle[], indicators: IndicatorConfig[]): SeriesMarker<Time>[] {
  const closes = candles.map((c) => c[4])
  const emas = indicators.filter((i) => i.id === 'ema' && i.visible)
  const smas = indicators.filter((i) => i.id === 'sma' && i.visible)
  const rsis = indicators.filter((i) => i.id === 'rsi' && i.visible)
  const emaPeriod = emas[0]?.period ?? 9
  const smaPeriod = smas[0]?.period ?? 20
  const rsiPeriod = rsis[0]?.period ?? 14
  const fast = ema(closes, emaPeriod)
  const slow = sma(closes, smaPeriod)
  const rr = rsi(closes, rsiPeriod)
  const markers: SeriesMarker<Time>[] = []
  for (let i = 30; i < candles.length; i++) {
    const buy = fast[i] >= slow[i] && rr[i] < 48 && rr[i] > rr[i - 1]
    const sell = rr[i] > 62 || fast[i] < slow[i]
    if (buy) {
      markers.push({
        time: Math.floor(candles[i][0] / 1000) as UTCTimestamp,
        position: 'belowBar',
        color: '#198754',
        shape: 'arrowUp',
      })
    } else if (sell) {
      markers.push({
        time: Math.floor(candles[i][0] / 1000) as UTCTimestamp,
        position: 'aboveBar',
        color: '#dc3545',
        shape: 'arrowDown',
      })
    }
  }
  return markers.slice(-40)
}

export function Chart({
  candles,
  indicators,
  livePrice,
  trades = [],
  symbol,
  showSignals = false,
  rsiBuy = 48,
  rsiSell = 62,
  chartType = 'candles',
  levels,
}: Props) {
  const hostRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleRef = useRef<MainSeries | null>(null)
  const overlays = useRef<Map<string, ISeriesApi<'Line'>>>(new Map())
  const markersApi = useRef<{ setMarkers: (m: SeriesMarker<Time>[]) => void } | null>(null)
  const rsiGuideLines = useRef<Map<string, IPriceLine[]>>(new Map())
  const levelLines = useRef<IPriceLine[]>([])
  const followLive = useRef(true)
  const needRsi = indicators.some((i) => i.visible && i.pane === 'rsi')
  const needAtr = indicators.some((i) => i.visible && i.pane === 'atr')
  const paneKey = `${needRsi ? 'rsi' : ''}:${needAtr ? 'atr' : ''}`
  const seriesKind = PRICE_SERIES[chartType]

  useEffect(() => {
    if (!hostRef.current) return
    const chart = createChart(hostRef.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: '#ffffff' },
        textColor: '#495057',
        fontFamily: "'IBM Plex Mono', monospace",
        fontSize: 11,
        panes: { separatorColor: '#dee2e6', separatorHoverColor: '#ff0000' },
      },
      grid: {
        vertLines: { color: '#f8f9fa' },
        horzLines: { color: '#f8f9fa' },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: '#dee2e6' },
      timeScale: {
        borderColor: '#dee2e6',
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 4,
        barSpacing: 8,
      },
    })

    const main: MainSeries =
      seriesKind === 'Line'
        ? chart.addSeries(LineSeries, { color: '#ff0000', lineWidth: 2, priceLineVisible: false })
        : seriesKind === 'Area'
          ? chart.addSeries(AreaSeries, {
              lineColor: '#ff0000',
              topColor: 'rgba(255,0,0,0.25)',
              bottomColor: 'rgba(255,0,0,0.02)',
              lineWidth: 2,
              priceLineVisible: false,
            })
          : seriesKind === 'Bar'
            ? chart.addSeries(BarSeries, { upColor: '#198754', downColor: '#dc3545', thinBars: false })
            : chart.addSeries(CandlestickSeries, {
                upColor: '#198754',
                downColor: '#dc3545',
                borderUpColor: '#198754',
                borderDownColor: '#dc3545',
                wickUpColor: '#198754',
                wickDownColor: '#dc3545',
              })

    if (needRsi) chart.addPane(true).setStretchFactor(0.35)
    if (needAtr) chart.addPane(true).setStretchFactor(0.3)
    chart.panes()[0]?.setStretchFactor(1)

    chartRef.current = chart
    candleRef.current = main
    markersApi.current = createSeriesMarkers(main, [])
    overlays.current = new Map()
    rsiGuideLines.current = new Map()
    levelLines.current = []

    const onRange = () => {
      const logical = chart.timeScale().getVisibleLogicalRange()
      if (!logical || !candleRef.current) return
      followLive.current = logical.to >= candleRef.current.data().length - 3
    }
    chart.timeScale().subscribeVisibleLogicalRangeChange(onRange)

    return () => {
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(onRange)
      chart.remove()
      chartRef.current = null
      candleRef.current = null
      markersApi.current = null
      overlays.current.clear()
      rsiGuideLines.current.clear()
      levelLines.current = []
    }
  }, [paneKey, needRsi, needAtr, seriesKind])

  useEffect(() => {
    const chart = chartRef.current
    const candleSeries = candleRef.current
    if (!chart || !candleSeries || !candles.length) return

    const bars = chartType === 'heikin' ? heikinAshi(candles) : candles
    if (chartType === 'line' || chartType === 'area') {
      candleSeries.setData(
        bars.map((c) => ({ time: Math.floor(c[0] / 1000) as UTCTimestamp, value: c[4] })),
      )
    } else {
      candleSeries.setData(toBars(bars, livePrice))
    }
    const closes = candles.map((c) => c[4])
    const wanted = new Set<string>()
    const rsiPane = needRsi ? 1 : undefined
    const atrPane = needAtr ? (needRsi ? 2 : 1) : undefined
    let rsiGuidesDrawn = false

    const line = (key: string, color: string, paneIndex?: number, dotted = false) => {
      wanted.add(key)
      let series = overlays.current.get(key)
      if (!series) {
        series = chart.addSeries(
          LineSeries,
          {
            color,
            lineWidth: dotted ? 1 : 2,
            lineStyle: dotted ? LineStyle.Dotted : LineStyle.Solid,
            priceLineVisible: false,
            lastValueVisible: !dotted,
            crosshairMarkerVisible: false,
          },
          paneIndex,
        )
        overlays.current.set(key, series)
      } else {
        series.applyOptions({ color })
      }
      return series
    }

    for (const ind of indicators) {
      if (!ind.visible) continue
      const k = ind.uid
      if (ind.id === 'ema') line(k, ind.color).setData(toLine(candles, ema(closes, ind.period)))
      if (ind.id === 'sma') line(k, ind.color).setData(toLine(candles, sma(closes, ind.period)))
      if (ind.id === 'vwap') line(k, ind.color).setData(toLine(candles, vwap(candles, ind.period)))
      if (ind.id === 'bb') {
        const bb = bollinger(closes, ind.period, ind.std ?? 2)
        line(`${k}-mid`, ind.color).setData(toLine(candles, bb.mid))
        line(`${k}-up`, ind.color, undefined, true).setData(toLine(candles, bb.upper))
        line(`${k}-lo`, ind.color, undefined, true).setData(toLine(candles, bb.lower))
      }
      if (ind.id === 'rsi' && rsiPane != null) {
        const series = line(k, ind.color, rsiPane)
        series.setData(toLine(candles, rsi(closes, ind.period)))
        if (!rsiGuidesDrawn) {
          for (const lines of rsiGuideLines.current.values()) {
            for (const pl of lines) series.removePriceLine(pl)
          }
          rsiGuideLines.current.clear()
          rsiGuideLines.current.set(k, [
            series.createPriceLine({
              price: rsiBuy,
              color: '#198754',
              lineWidth: 1,
              lineStyle: LineStyle.Dashed,
              axisLabelVisible: true,
              title: 'buy',
            }),
            series.createPriceLine({
              price: rsiSell,
              color: '#dc3545',
              lineWidth: 1,
              lineStyle: LineStyle.Dashed,
              axisLabelVisible: true,
              title: 'sell',
            }),
            series.createPriceLine({
              price: 50,
              color: '#dee2e6',
              lineWidth: 1,
              lineStyle: LineStyle.SparseDotted,
              axisLabelVisible: false,
              title: '',
            }),
          ])
          rsiGuidesDrawn = true
        }
      }
      if (ind.id === 'atr' && atrPane != null) {
        line(k, ind.color, atrPane).setData(toLine(candles, atr(candles, ind.period)))
      }
    }

    for (const [key, series] of overlays.current) {
      if (!wanted.has(key)) {
        chart.removeSeries(series)
        overlays.current.delete(key)
        rsiGuideLines.current.delete(key)
      }
    }

    const marks = [
      ...tradeMarkers(candles, trades, symbol),
      ...(showSignals ? signalMarkers(candles, indicators) : []),
    ]
    if (markersApi.current) markersApi.current.setMarkers(marks)

    for (const pl of levelLines.current) candleSeries.removePriceLine(pl)
    levelLines.current = []
    const addLevel = (
      price: number | null | undefined,
      color: string,
      title: string,
      style: LineStyle = LineStyle.Dashed,
    ) => {
      if (price == null || !Number.isFinite(price) || price <= 0) return
      levelLines.current.push(
        candleSeries.createPriceLine({
          price,
          color,
          lineWidth: 1,
          lineStyle: style,
          axisLabelVisible: true,
          title,
        }),
      )
    }
    addLevel(levels?.entry, '#0d6efd', 'ENTRY', LineStyle.Solid)
    addLevel(levels?.takeProfit, '#198754', 'TP')
    addLevel(levels?.stopLoss, '#dc3545', 'SL')

    if (followLive.current) chart.timeScale().scrollToRealTime()
  }, [
    candles,
    indicators,
    livePrice,
    trades,
    symbol,
    showSignals,
    rsiBuy,
    rsiSell,
    paneKey,
    needRsi,
    needAtr,
    chartType,
    seriesKind,
    levels,
  ])

  return (
    <div className="relative min-h-[420px] w-full flex-1">
      <div ref={hostRef} className="absolute inset-0" />
      <div className="pointer-events-none absolute bottom-3 left-3 z-10 flex flex-wrap items-center gap-3 rounded-none border border-line bg-panel px-2.5 py-1.5 font-mono text-[10px] text-steel">
        <span className="inline-flex items-center gap-1 text-positive">▲ BUY</span>
        <span className="inline-flex items-center gap-1 text-negative">▼ SELL</span>
        <span className="inline-flex items-center gap-1 text-info">— ENTRY</span>
        <span className="inline-flex items-center gap-1 text-positive">- - TP</span>
        <span className="inline-flex items-center gap-1 text-negative">- - SL</span>
      </div>
      {!candles.length && (
        <div className="absolute inset-0 flex items-center justify-center font-mono text-xs text-steel">
          Waiting for market candles…
        </div>
      )}
    </div>
  )
}
