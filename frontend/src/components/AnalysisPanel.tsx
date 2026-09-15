import { Button } from '@/components/ui/button'
import type { TradeAnalysis } from '@/lib/types'
import { cash } from '@/lib/utils'

type Props = {
  analysis: TradeAnalysis | null
  streamText?: string
  error?: string
  loading: boolean
  onRun: () => void
}

export function AnalysisPanel({ analysis, streamText, error, loading, onRun }: Props) {
  return (
    <div className="space-y-3">
      <p className="text-xs text-steel">
        NVIDIA NIM reads candles, active indicators, order book, strategy, and paper state, then proposes one trade.
      </p>
      <Button className="w-full" disabled={loading} onClick={onRun}>
        {loading ? 'Analyzing…' : 'Run AI analysis'}
      </Button>
      {error ? (
        <p className="rounded-none border border-negative/30 bg-negative/5 px-2 py-1.5 font-mono text-[11px] text-negative">
          {error}
        </p>
      ) : null}
      {loading && streamText ? (
        <p className="max-h-40 overflow-auto rounded-none border border-line bg-paper/70 p-2 font-mono text-[11px] text-steel whitespace-pre-wrap">
          {streamText}
        </p>
      ) : null}
      {analysis ? (
        <div className="space-y-2 rounded-none border border-line bg-paper/70 p-3">
          <div className="flex items-center justify-between gap-2">
            <span
              className={`rounded-full px-2 py-0.5 font-mono text-xs font-bold ${
                analysis.action === 'BUY'
                  ? 'bg-positive/15 text-positive'
                  : analysis.action === 'SELL'
                    ? 'bg-negative/15 text-negative'
                    : 'bg-line text-steel'
              }`}
            >
              {analysis.action}
            </span>
            <span className="font-mono text-[11px] text-steel">
              conf {(analysis.confidence * 100).toFixed(0)}% · {analysis.model.split('/').pop()}
            </span>
          </div>
          <div className="grid grid-cols-3 gap-2 font-mono text-[11px]">
            <div>
              <div className="text-steel">Entry</div>
              <div className="font-semibold">{analysis.entry != null ? cash(analysis.entry) : '—'}</div>
            </div>
            <div>
              <div className="text-steel">Stop</div>
              <div className="font-semibold">{analysis.stop != null ? cash(analysis.stop) : '—'}</div>
            </div>
            <div>
              <div className="text-steel">Target</div>
              <div className="font-semibold">
                {analysis.take_profit != null ? cash(analysis.take_profit) : '—'}
              </div>
            </div>
          </div>
          <div>
            <div className="text-xs font-bold">Why</div>
            <p className="mt-1 text-xs leading-relaxed text-ink/90 whitespace-pre-wrap">{analysis.rationale}</p>
          </div>
          <div>
            <div className="text-xs font-bold">Risks</div>
            <p className="mt-1 text-xs leading-relaxed text-steel whitespace-pre-wrap">{analysis.risks}</p>
          </div>
        </div>
      ) : null}
    </div>
  )
}
