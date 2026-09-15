import type { DecisionCheck, DecisionSnapshot } from '@/lib/types'
import { cash } from '@/lib/utils'

type Props = {
  decision: DecisionSnapshot | null
}

function toneFor(action?: string) {
  if (action === 'BUY') return 'bg-positive/15 text-positive border-positive/30'
  if (action === 'SELL') return 'bg-negative/15 text-negative border-negative/30'
  return 'bg-line/80 text-steel border-line'
}

function CheckRow({ check }: { check: DecisionCheck }) {
  const disabled = check.enabled === false
  return (
    <div className={`flex items-start gap-2 border-b border-line/60 py-1.5 last:border-0 ${disabled ? 'opacity-45' : ''}`}>
      <span
        className={`mt-0.5 inline-flex size-4 shrink-0 items-center justify-center rounded-sm font-mono text-[10px] font-bold ${
          disabled ? 'bg-line text-steel' : check.ok ? 'bg-positive/15 text-positive' : 'bg-negative/15 text-negative'
        }`}
        title={disabled ? 'disabled' : check.ok ? 'pass' : 'fail'}
      >
        {disabled ? '–' : check.ok ? '✓' : '×'}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-[11px] font-semibold text-ink">{check.label}</span>
          {check.value != null && typeof check.value === 'number' ? (
            <span className="font-mono text-[10px] text-steel">{Number.isInteger(check.value) ? check.value : check.value.toFixed(3)}</span>
          ) : null}
        </div>
        <div className="truncate font-mono text-[10px] text-steel">{check.detail}</div>
      </div>
    </div>
  )
}

export function DecisionPanel({ decision }: Props) {
  if (!decision) {
    return (
      <div className="rounded-none border border-dashed border-line bg-paper/50 px-3 py-6 text-center text-xs text-steel">
        Waiting for live decision stream…
      </div>
    )
  }

  const groups: { title: string; ids: string[] }[] = [
    { title: 'Market gates', ids: ['atr', 'trend', 'volume', 'spread', 'imbalance'] },
    { title: 'Signal factors', ids: ['rsi', 'rsi2', 'vwap', 'bb', 'setup', 'score'] },
    { title: 'Risk / account', ids: ['position', 'min_gain', 'tp', 'sl', 'leverage', 'halt', 'warmup'] },
  ]

  const byId = Object.fromEntries(decision.checks.map((c) => [c.id, c]))

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded-full border px-2 py-0.5 font-mono text-xs font-bold ${toneFor(decision.action)}`}>
          {decision.action}
        </span>
        <span className="font-mono text-[11px] text-steel">{decision.reason.replaceAll('_', ' ')}</span>
        <span className="ml-auto font-mono text-[10px] text-steel">
          {decision.ts ? new Date(decision.ts * 1000).toLocaleTimeString() : ''}
        </span>
      </div>

      <div className="grid grid-cols-3 gap-2 rounded-none border border-line bg-paper/70 p-2 font-mono text-[10px]">
        <div>
          <div className="text-steel">Price</div>
          <div className="font-semibold">{decision.price != null ? cash(decision.price) : '—'}</div>
        </div>
        <div>
          <div className="text-steel">Score</div>
          <div className="font-semibold">{decision.score != null ? decision.score.toFixed(2) : '—'}</div>
        </div>
        <div>
          <div className="text-steel">Est. PnL</div>
          <div className={`font-semibold ${(decision.est_pnl ?? 0) >= 0 ? 'text-positive' : 'text-negative'}`}>
            {decision.est_pnl != null ? cash(decision.est_pnl) : '—'}
          </div>
        </div>
      </div>

      <p className="text-[11px] leading-relaxed text-steel">
        Each row is a live input to the decision. Green = gate currently passes for the path in play;
        red = blocking or not met.
      </p>

      <div className="max-h-[420px] space-y-3 overflow-y-auto pr-1">
        {groups.map((g) => {
          const rows = g.ids.map((id) => byId[id]).filter(Boolean)
          if (!rows.length) return null
          return (
            <div key={g.title}>
              <div className="mb-1 font-mono text-[10px] uppercase tracking-[0.14em] text-steel">{g.title}</div>
              <div className="rounded-none border border-line bg-panel/40 px-2">
                {rows.map((c) => (
                  <CheckRow key={c.id} check={c} />
                ))}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
