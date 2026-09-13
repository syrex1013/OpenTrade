import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import {
  createIndicator,
  type IndicatorConfig,
  type IndicatorId,
} from '@/lib/types'
import { Trash2 } from 'lucide-react'
import { useState } from 'react'

type Props = {
  indicators: IndicatorConfig[]
  onChange: (next: IndicatorConfig[]) => void
  showSignals: boolean
  onShowSignals: (v: boolean) => void
}

const ADDABLE: IndicatorId[] = ['ema', 'sma', 'vwap', 'bb', 'rsi', 'atr']

export function IndicatorPanel({ indicators, onChange, showSignals, onShowSignals }: Props) {
  const [addType, setAddType] = useState<IndicatorId>('ema')

  const patch = (uid: string, next: Partial<IndicatorConfig>) => {
    onChange(indicators.map((i) => (i.uid === uid ? { ...i, ...next } : i)))
  }

  const remove = (uid: string) => onChange(indicators.filter((i) => i.uid !== uid))

  const add = () => onChange([...indicators, createIndicator(addType, indicators)])

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <Label className="text-ink">Signal markers</Label>
          <p className="text-[11px] text-steel">Buy / sell arrows on candles</p>
        </div>
        <Switch checked={showSignals} onCheckedChange={onShowSignals} />
      </div>

      <div className="flex gap-2">
        <Select value={addType} onValueChange={(v) => setAddType(v as IndicatorId)}>
          <SelectTrigger className="flex-1">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {ADDABLE.map((id) => (
              <SelectItem key={id} value={id}>
                {id.toUpperCase()}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button type="button" variant="secondary" onClick={add}>
          Add
        </Button>
      </div>

      {indicators.length === 0 ? (
        <p className="rounded-lg border border-dashed border-line bg-paper/50 px-3 py-4 text-center text-xs text-steel">
          No indicators. Add EMA, RSI, etc. — duplicates allowed with different periods.
        </p>
      ) : (
        indicators.map((ind) => (
          <div key={ind.uid} className="rounded-lg border border-line bg-paper/60 p-3">
            <div className="flex items-center justify-between gap-3">
              <div className="flex min-w-0 items-center gap-2">
                <span className="size-2.5 shrink-0 rounded-full" style={{ background: ind.color }} />
                <span className="truncate text-sm font-semibold">
                  {ind.label} {ind.period}
                </span>
                <span className="font-mono text-[10px] uppercase tracking-wide text-steel">{ind.pane}</span>
              </div>
              <div className="flex items-center gap-2">
                <Switch checked={ind.visible} onCheckedChange={(v) => patch(ind.uid, { visible: v })} />
                <button
                  type="button"
                  aria-label={`Remove ${ind.label}`}
                  className="rounded p-1 text-steel hover:bg-coral/10 hover:text-coral"
                  onClick={() => remove(ind.uid)}
                >
                  <Trash2 className="size-3.5" />
                </button>
              </div>
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2">
              <div>
                <Label htmlFor={`${ind.uid}-period`}>Period</Label>
                <Input
                  id={`${ind.uid}-period`}
                  type="number"
                  min={2}
                  max={200}
                  value={ind.period}
                  onChange={(e) =>
                    patch(ind.uid, { period: Math.max(2, Number(e.target.value) || ind.period) })
                  }
                />
              </div>
              {ind.id === 'bb' ? (
                <div>
                  <Label htmlFor={`${ind.uid}-std`}>Std dev</Label>
                  <Input
                    id={`${ind.uid}-std`}
                    type="number"
                    step={0.1}
                    min={0.5}
                    max={4}
                    value={ind.std ?? 2}
                    onChange={(e) => patch(ind.uid, { std: Number(e.target.value) || 2 })}
                  />
                </div>
              ) : (
                <div>
                  <Label htmlFor={`${ind.uid}-color`}>Color</Label>
                  <Input
                    id={`${ind.uid}-color`}
                    type="color"
                    className="h-9 cursor-pointer px-1"
                    value={ind.color}
                    onChange={(e) => patch(ind.uid, { color: e.target.value })}
                  />
                </div>
              )}
              {ind.id === 'bb' && (
                <div className="col-span-2">
                  <Label htmlFor={`${ind.uid}-color-bb`}>Color</Label>
                  <Input
                    id={`${ind.uid}-color-bb`}
                    type="color"
                    className="h-9 cursor-pointer px-1"
                    value={ind.color}
                    onChange={(e) => patch(ind.uid, { color: e.target.value })}
                  />
                </div>
              )}
            </div>
          </div>
        ))
      )}
    </div>
  )
}
