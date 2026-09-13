import type { Candle } from './types'

/** Match backend ema() — seed with first close. */
export function ema(values: number[], period: number): number[] {
  if (!values.length) return []
  const k = 2 / (period + 1)
  const out = [values[0]]
  for (let i = 1; i < values.length; i++) out.push(values[i] * k + out[i - 1] * (1 - k))
  return out
}

export function sma(values: number[], period: number): number[] {
  return values.map((_, i) => {
    const slice = values.slice(Math.max(0, i - period + 1), i + 1)
    return slice.reduce((a, b) => a + b, 0) / slice.length
  })
}

export function rollingStd(values: number[], period: number): number[] {
  return values.map((_, i) => {
    const window = values.slice(Math.max(0, i - period + 1), i + 1)
    const mean = window.reduce((a, b) => a + b, 0) / window.length
    return Math.sqrt(window.reduce((a, b) => a + (b - mean) ** 2, 0) / window.length)
  })
}

/** Typical-price VWAP over rolling window — matches backend. */
export function vwap(candles: Candle[], period = 20): number[] {
  return candles.map((_, i) => {
    const rows = candles.slice(Math.max(0, i - period + 1), i + 1)
    const volume = rows.reduce((a, c) => a + c[5], 0)
    if (!volume) return rows[rows.length - 1][4]
    return rows.reduce((a, c) => a + ((c[2] + c[3] + c[4]) / 3) * c[5], 0) / volume
  })
}

/** Rolling SMA RSI — matches backend rsi(). */
export function rsi(values: number[], period = 14): number[] {
  if (values.length < 2) return values.map(() => 50)
  const gains: number[] = []
  const losses: number[] = []
  const out = [50]
  for (let i = 1; i < values.length; i++) {
    const diff = values[i] - values[i - 1]
    gains.push(Math.max(diff, 0))
    losses.push(Math.max(-diff, 0))
    const g = gains.slice(-period).reduce((a, b) => a + b, 0) / period
    const l = losses.slice(-period).reduce((a, b) => a + b, 0) / period
    out.push(l === 0 ? 100 : 100 - 100 / (1 + g / l))
  }
  return out
}

/** High-low ATR approximation — matches backend atr(). */
export function atr(candles: Candle[], period = 14): number[] {
  const tr = candles.map((c) => c[2] - c[3])
  return tr.map((_, i) => {
    const slice = tr.slice(Math.max(0, i - period + 1), i + 1)
    return slice.reduce((a, b) => a + b, 0) / slice.length
  })
}

export function bollinger(values: number[], period: number, stdMult: number) {
  const mid = sma(values, period)
  const std = rollingStd(values, period)
  return {
    mid,
    upper: mid.map((m, i) => m + stdMult * std[i]),
    lower: mid.map((m, i) => m - stdMult * std[i]),
  }
}

/** Heikin-Ashi candles derived from raw OHLC — no extra dependency. */
export function heikinAshi(candles: Candle[]): Candle[] {
  const out: Candle[] = []
  for (let i = 0; i < candles.length; i++) {
    const [t, o, h, l, c] = candles[i]
    const close = (o + h + l + c) / 4
    const prev = out[i - 1]
    const open = prev ? (prev[1] + prev[4]) / 2 : (o + c) / 2
    out.push([t, open, Math.max(h, open, close), Math.min(l, open, close), close, candles[i][5]])
  }
  return out
}
