import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function fmt(n: number, digits = 2) {
  if (!Number.isFinite(n)) return '—'
  return n.toLocaleString(undefined, {
    maximumFractionDigits: Math.abs(n) < 1 ? 6 : digits,
  })
}

export function cash(n: number) {
  return `$${fmt(n)}`
}

export function pairLabel(symbol: string) {
  return symbol.endsWith('USDT') ? symbol.replace(/USDT$/, ' / USDT') : symbol
}
