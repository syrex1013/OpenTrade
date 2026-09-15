import { useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Card, CardBody, CardHeader, CardTitle } from '@/components/ui/card'
import { Modal } from '@/components/ui/modal'
import type { TradeEvent } from '@/lib/types'
import { cash, fmt } from '@/lib/utils'

type Fill = Record<string, number | string | boolean | null | undefined>

function fillOf(t: TradeEvent): Fill {
  return { ...(t.data ?? {}), ...(t as unknown as Record<string, unknown>) } as Fill
}

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

/** Pair each BUY with the next SELL on the same market so both rows can show P/L. */
function pairTradePnls(trades: TradeEvent[]): Array<number | null> {
  const fills = trades.map(fillOf)
  const openIdx: Record<string, number[]> = {}
  const paired: Array<number | null> = trades.map(() => null)
  for (let i = fills.length - 1; i >= 0; i--) {
    const f = fills[i]
    const sym = String(f.symbol ?? '')
    if (f.side === 'BUY') {
      openIdx[sym] = openIdx[sym] || []
      openIdx[sym].push(i)
    } else if (f.side === 'SELL' && num(f.pnl) != null) {
      const stack = openIdx[sym]
      if (stack?.length) paired[stack.pop()!] = num(f.pnl)
      paired[i] = num(f.pnl)
    }
  }
  return paired
}

function tradeCsv(trades: TradeEvent[]): string {
  const cols = [
    'time',
    'side',
    'symbol',
    'price',
    'quantity',
    'margin',
    'leverage',
    'fee_paid',
    'pnl',
    'reason',
    'mode',
  ]
  const rows = trades.map((t) => {
    const f = fillOf(t)
    const cells: Record<string, string> = {
      time: t.ts ? new Date(t.ts * 1000).toISOString() : '',
      side: String(f.side ?? ''),
      symbol: String(f.symbol ?? ''),
      price: String(num(f.price) ?? ''),
      quantity: String(num(f.quantity) ?? ''),
      margin: String(num(f.margin) ?? ''),
      leverage: String(num(f.leverage) ?? ''),
      fee_paid: String(num(f.fee_paid) ?? ''),
      pnl: String(num(f.pnl) ?? ''),
      reason: String(f.reason ?? ''),
      mode: String(f.mode ?? 'paper'),
    }
    return cols.map((c) => cells[c]).join(',')
  })
  return [cols.join(','), ...rows].join('\n')
}

type Props = {
  trades: TradeEvent[]
  onClear?: () => void
}

export function TradesPanel({ trades, onClear }: Props) {
  const [selected, setSelected] = useState<number | null>(null)
  const paired = useMemo(() => pairTradePnls(trades), [trades])
  const shown = trades.slice(0, 100)

  const download = () => {
    const blob = new Blob([tradeCsv(trades)], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'fasttrade-trades.csv'
    a.click()
    URL.revokeObjectURL(url)
  }

  const detail = selected != null ? shown[selected] : null
  const d = detail ? fillOf(detail) : null
  const feeEntry = num(d?.fee_entry)
  const feeExit = num(d?.fee_exit)
  const feePaid = num(d?.fee_paid)

  return (
    <Card>
      <CardHeader>
        <CardTitle>Executed trades</CardTitle>
        <div className="flex gap-2">
          {onClear && (
            <Button variant="secondary" size="sm" disabled={!trades.length} onClick={onClear}>
              Clear stats
            </Button>
          )}
          <Button variant="secondary" size="sm" disabled={!trades.length} onClick={download}>
            Export CSV
          </Button>
        </div>
      </CardHeader>
      <CardBody className="pt-2">
        <div className="overflow-x-auto">
          <table className="w-full border-collapse font-mono text-[11px]">
            <thead>
              <tr className="text-left text-steel">
                <th className="pb-2 font-normal">TIME</th>
                <th className="pb-2 font-normal">SIDE</th>
                <th className="pb-2 font-normal">MARKET</th>
                <th className="pb-2 font-normal">PRICE</th>
                <th className="pb-2 font-normal">SIZE</th>
                <th className="pb-2 font-normal text-right">FEE</th>
                <th className="pb-2 font-normal text-right">P/L</th>
                <th className="pb-2 font-normal text-right">BOOK</th>
              </tr>
            </thead>
            <tbody>
              {shown.length === 0 ? (
                <tr>
                  <td colSpan={8} className="py-6 text-center text-steel">
                    No trades yet
                  </td>
                </tr>
              ) : (
                shown.map((t, i) => {
                  const f = fillOf(t)
                  const pnl = num(f.pnl) ?? paired[i]
                  const fee = num(f.fee_paid)
                  return (
                    <tr
                      key={`${t.ts}-${String(f.side)}-${String(f.price)}-${i}`}
                      className="cursor-pointer border-t border-line/80 hover:bg-paper/60"
                      onClick={() => setSelected(i)}
                    >
                      <td className="py-2">{t.ts ? new Date(t.ts * 1000).toLocaleTimeString() : '—'}</td>
                      <td className={`py-2 ${f.side === 'BUY' ? 'text-positive' : 'text-negative'}`}>
                        {String(f.side ?? '—')}
                      </td>
                      <td className="py-2">{String(f.symbol ?? '—')}</td>
                      <td className="py-2">{num(f.price) != null ? cash(num(f.price)!) : '—'}</td>
                      <td className="py-2">{fmt(num(f.quantity) ?? num(f.proceeds) ?? 0)}</td>
                      <td className="py-2 text-right text-steel">{fee != null ? cash(fee) : '—'}</td>
                      <td
                        className={`py-2 text-right font-semibold ${pnl == null ? 'text-steel' : pnl >= 0 ? 'text-positive' : 'text-negative'}`}
                      >
                        {pnl == null ? (f.side === 'BUY' ? 'open' : '—') : `${pnl >= 0 ? '+' : ''}${cash(pnl)}`}
                        {f.reason ? (
                          <div className="text-[10px] font-normal text-steel">{String(f.reason)}</div>
                        ) : null}
                      </td>
                      <td className="py-2 text-right text-steel">
                        {num(f.realized_pnl) != null ? cash(num(f.realized_pnl)!) : '—'}
                      </td>
                    </tr>
                  )
                })
              )}
            </tbody>
          </table>
        </div>
      </CardBody>
      <Modal open={detail != null} title="Trade details" onClose={() => setSelected(null)}>
        {d && (
          <div className="space-y-3 font-mono text-[11px]">
            <div className="grid grid-cols-2 gap-2 rounded-none border border-line bg-paper/70 p-3 sm:grid-cols-3">
              <Detail label="Side" value={String(d.side ?? '—')} />
              <Detail label="Market" value={String(d.symbol ?? '—')} />
              <Detail label="Mode" value={String(d.mode ?? 'paper')} />
              <Detail label="Entry" value={num(d.entry) != null ? cash(num(d.entry)!) : '—'} />
              <Detail label="Exit" value={num(d.exit ?? d.price) != null ? cash(num(d.exit ?? d.price)!) : '—'} />
              <Detail label="Qty" value={fmt(num(d.quantity) ?? 0, 6)} />
              <Detail label="Margin" value={cash(num(d.margin) ?? 0)} />
              <Detail label="Leverage" value={`${num(d.leverage) ?? 1}x`} />
              <Detail label="Hold" value={d.hold_bars != null ? `${d.hold_bars} bars` : '—'} />
            </div>
            <div className="rounded-none border border-line bg-paper/70 p-3">
              <div className="mb-2 text-xs font-bold">Fee breakdown</div>
              <div className="space-y-1">
                <MoneyRow label="Gross" value={num(d.gross_pnl)} />
                <MoneyRow label="Entry fee" value={feeEntry != null ? -feeEntry : null} />
                <MoneyRow label="Exit fee" value={feeExit != null ? -feeExit : null} />
                <MoneyRow
                  label="Total fees"
                  value={feePaid != null ? -feePaid : null}
                  check={
                    feeEntry != null && feeExit != null && feePaid != null
                      ? Math.abs(feeEntry + feeExit - feePaid) < 1e-9
                        ? 'sums ✓'
                        : 'MISMATCH'
                      : undefined
                  }
                />
                <MoneyRow label="Net P/L" value={num(d.pnl)} bold />
                <MoneyRow label="Return on margin" value={num(d.return_margin_pct)} suffix="%" />
              </div>
            </div>
            {d.reason ? (
              <p>
                <span className="text-steel">Exit reason:</span> {String(d.reason)}
              </p>
            ) : null}
          </div>
        )}
      </Modal>
    </Card>
  )
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-steel">{label}</div>
      <div className="font-semibold">{value}</div>
    </div>
  )
}

function MoneyRow({
  label,
  value,
  bold,
  suffix,
  check,
}: {
  label: string
  value: number | null
  bold?: boolean
  suffix?: string
  check?: string
}) {
  if (value == null) return null
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-steel">{label}</span>
      <span className={bold ? 'font-bold' : value >= 0 ? 'text-positive' : 'text-negative'}>
        {value >= 0 ? '+' : ''}
        {cash(value)}
        {suffix ?? ''} {check ? <span className="text-steel">· {check}</span> : null}
      </span>
    </div>
  )
}
