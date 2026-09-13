import type { SeriesMarker, Time, UTCTimestamp } from 'lightweight-charts'
import type { BacktestFill, BacktestResult, Candle, TradeEvent } from './types'

/** Snap a timestamp onto the newest candle at or before it. */
export function snapTime(candles: Candle[], tsSec: number): UTCTimestamp {
  const times = candles.map((c) => Math.floor(c[0] / 1000))
  let best = times[0] ?? Math.floor(tsSec)
  for (const t of times) {
    if (t <= tsSec) best = t
    else break
  }
  return best as UTCTimestamp
}

/**
 * Live fill markers.
 *
 * - Fills older than the loaded window are dropped instead of being stacked onto the
 *   first bar (that pile-up is what rendered as a tall column of arrows).
 * - A BUY and a SELL inside the same candle both survive: the library draws them
 *   below-bar and above-bar respectively, so neither disappears.
 */
export function tradeMarkers(
  candles: Candle[],
  trades: TradeEvent[],
  symbol?: string,
): SeriesMarker<Time>[] {
  if (!candles.length || !trades.length) return []
  const firstBar = Math.floor(candles[0][0] / 1000)
  const out: SeriesMarker<Time>[] = []
  for (const t of trades) {
    const q = t.data ?? t
    const side = String(q.side ?? '').toUpperCase()
    if (side !== 'BUY' && side !== 'SELL') continue
    if (symbol && q.symbol && q.symbol !== symbol) continue
    const secs = Math.floor(Number(t.ts ?? 0))
    if (!secs || secs < firstBar) continue
    out.push({
      time: snapTime(candles, secs),
      position: side === 'BUY' ? 'belowBar' : 'aboveBar',
      color: side === 'BUY' ? '#0d9b6c' : '#e03e52',
      shape: side === 'BUY' ? 'arrowUp' : 'arrowDown',
      text: side,
    })
  }
  // lightweight-charts requires ascending marker times.
  return out.sort((a, b) => Number(a.time) - Number(b.time))
}

/** Backtest fill markers plotted on the backtest candle payload. */
export function fillMarkers(result: BacktestResult | null): SeriesMarker<Time>[] {
  const bars = result?.candles ?? []
  const fills = result?.fills ?? []
  if (!bars.length || !fills.length) return []
  const times = bars.map((b) => Math.floor(b.t / 1000))
  const first = times[0]
  const out: SeriesMarker<Time>[] = []
  for (const f of fills as BacktestFill[]) {
    const side = String(f.side ?? '').toUpperCase()
    if (side !== 'BUY' && side !== 'SELL') continue
    const secs = Math.floor(Number(f.ts ?? 0) / 1000)
    if (!secs || secs < first) continue
    let snapped = times[0]
    for (const t of times) {
      if (t <= secs) snapped = t
      else break
    }
    out.push({
      time: snapped as UTCTimestamp,
      position: side === 'BUY' ? 'belowBar' : 'aboveBar',
      color: side === 'BUY' ? '#0d9b6c' : '#e03e52',
      shape: side === 'BUY' ? 'arrowUp' : 'arrowDown',
      text: f.reason && side === 'SELL' ? `SELL ${f.reason}` : side,
    })
  }
  return out.sort((a, b) => Number(a.time) - Number(b.time))
}
