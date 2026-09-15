import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import type { GateId, GateMap } from '@/lib/types'
import { DEFAULT_GATES, GATE_META } from '@/lib/types'

type Props = {
  gates: GateMap
  onChange: (next: GateMap) => void
}

export function GatePanel({ gates, onChange }: Props) {
  const merged: GateMap = { ...DEFAULT_GATES, ...gates }

  const toggle = (id: GateId, enabled: boolean) => {
    onChange({ ...merged, [id]: enabled })
  }

  const setAll = (enabled: boolean) => {
    const next = { ...merged }
    ;(Object.keys(DEFAULT_GATES) as GateId[]).forEach((id) => {
      next[id] = enabled
    })
    onChange(next)
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-steel">
        Toggle which live decision factors block entries/exits. Disabled gates are treated as pass.
      </p>
      <div className="flex gap-2">
        <button
          type="button"
          className="rounded-none border border-line px-2 py-1 font-mono text-[10px] hover:bg-paper"
          onClick={() => setAll(true)}
        >
          Enable all
        </button>
        <button
          type="button"
          className="rounded-none border border-line px-2 py-1 font-mono text-[10px] hover:bg-paper"
          onClick={() => setAll(false)}
        >
          Disable all
        </button>
      </div>
      <div className="max-h-[360px] space-y-2 overflow-y-auto pr-1">
        {(Object.keys(GATE_META) as GateId[]).map((id) => (
          <div
            key={id}
            className="flex items-start justify-between gap-3 rounded-none border border-line bg-paper/60 px-2.5 py-2"
          >
            <div className="min-w-0">
              <Label htmlFor={`gate-${id}`} className="text-[11px] font-semibold">
                {GATE_META[id].label}
              </Label>
              <p className="mt-0.5 text-[10px] leading-snug text-steel">{GATE_META[id].hint}</p>
            </div>
            <Switch
              id={`gate-${id}`}
              checked={Boolean(merged[id])}
              onCheckedChange={(v) => toggle(id, v)}
            />
          </div>
        ))}
      </div>
    </div>
  )
}
