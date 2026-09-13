import { Loader2 } from 'lucide-react'
import type { ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import { Modal } from '@/components/ui/modal'
import { useState } from 'react'
import type { JobStatus } from '@/lib/types'

type Props = {
  title: string
  description: string
  runLabel: string
  runningLabel?: string
  busy: boolean
  job: JobStatus | null
  error?: string
  onRun: () => void
  summary?: string
  children?: ReactNode
}

export function JobRunner({
  title,
  description,
  runLabel,
  runningLabel = 'Running…',
  busy,
  job,
  error,
  onRun,
  summary,
  children,
}: Props) {
  const [details, setDetails] = useState(false)
  const active = busy || job?.status === 'running' || job?.status === 'queued'
  const pct = Math.max(2, Math.min(100, Math.round(job?.pct ?? 0)))
  const showProgress = active || (job && job.status !== 'idle' && (job.pct ?? 0) > 0)

  return (
    <div className="space-y-3">
      <p className="text-xs text-steel">{description}</p>
      <div className="flex gap-2">
        <Button className="flex-1" disabled={active} onClick={onRun}>
          {active && <Loader2 className="size-4 animate-spin" />}
          {active ? runningLabel : runLabel}
        </Button>
        {job?.status === 'done' && children ? (
          <Button variant="secondary" onClick={() => setDetails(true)}>
            Details
          </Button>
        ) : null}
      </div>
      {showProgress && (
        <div className="space-y-1" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
          <div className="flex justify-between font-mono text-[10px] text-steel">
            <span>
              {job?.phase || 'working'}
              {job?.symbol ? ` · ${job.symbol}` : ''}
              {job?.done != null && job?.total != null ? ` · ${job.done}/${job.total}` : ''}
              {job?.pages != null && job?.pages > 0 ? ` · page ${job.pages}` : ''}
            </span>
            <span>{pct}%</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-line">
            <div
              className="h-full rounded-full bg-cobalt transition-all duration-300"
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
      )}
      {summary && job?.status === 'done' ? (
        <p className="font-mono text-[11px] text-steel">{summary}</p>
      ) : null}
      {error || job?.status === 'error' ? (
        <p className="rounded-md border border-coral/30 bg-coral/5 px-3 py-2 font-mono text-[11px] text-coral">
          {error || job?.error || 'Job failed'}
        </p>
      ) : null}
      {job?.status === 'done' && children && (
        <Modal open={details} title={title} onClose={() => setDetails(false)}>
          {children}
        </Modal>
      )}
    </div>
  )
}
