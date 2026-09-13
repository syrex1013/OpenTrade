import { useEffect, useMemo, useState } from 'react'
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
import { api } from '@/lib/api'
import type { ExchangeInfo, SettingsField, StrategySettings } from '@/lib/types'

type Props = {
  settings: StrategySettings
  onChange: (next: StrategySettings) => void
  onApplyPreset: (next: StrategySettings) => void
  onSave: () => void
  saving: boolean
  saveError?: string
}

export function SettingsPanel({ settings, onChange, onApplyPreset, onSave, saving, saveError }: Props) {
  const [fields, setFields] = useState<Record<string, SettingsField>>({})
  const [groups, setGroups] = useState<string[]>([])
  const [presets, setPresets] = useState<{ name: string; label: string }[]>([])
  const [exchanges, setExchanges] = useState<ExchangeInfo[]>([])
  const [preset, setPreset] = useState('balanced')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let alive = true
    api
      .settingsSchema()
      .then((s) => {
        if (!alive) return
        setFields(s.schema)
        setGroups(s.groups)
        setPresets(s.presets)
      })
      .catch((e) => {
        if (alive) setError(e instanceof Error ? e.message : 'Could not load settings schema')
      })
    api
      .exchanges()
      .then((list) => {
        if (alive) setExchanges(list)
      })
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [])

  const visible = useMemo(
    () =>
      (Object.values(fields) as SettingsField[]).filter(
        (f) => f.key !== 'gates' && f.group !== 'Advanced' && groups.includes(f.group),
      ),
    [fields, groups],
  )
  const grouped = useMemo(() => {
    const out: Record<string, SettingsField[]> = {}
    for (const f of visible) {
      out[f.group] = out[f.group] || []
      out[f.group].push(f)
    }
    return groups.map((g) => [g, out[g] ?? []] as const).filter(([, list]) => list.length > 0)
  }, [visible, groups])

  const patch = (key: string, value: unknown) =>
    onChange({ ...settings, [key]: value } as StrategySettings)

  const changeExchange = (id: string) => {
    const info = exchanges.find((e) => e.id === id)
    onChange({
      ...settings,
      exchange: id,
      fee_rate: info?.fee_rate ?? settings.fee_rate,
      slippage_rate: info?.slippage_rate ?? settings.slippage_rate,
    })
  }

  const applyPreset = async () => {
    setBusy(true)
    setError('')
    try {
      const res = await api.applyPreset(preset)
      onApplyPreset(res.settings)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not apply preset')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-line bg-paper/60 p-3">
        <div className="mb-2 text-xs font-bold">Preset</div>
        <div className="flex gap-2">
          <Select value={preset} onValueChange={setPreset}>
            <SelectTrigger className="flex-1">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {presets.map((p) => (
                <SelectItem key={p.name} value={p.name}>
                  {p.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button variant="secondary" disabled={busy} onClick={applyPreset}>
            {busy ? 'Applying…' : 'Apply'}
          </Button>
        </div>
        <p className="mt-2 text-[11px] leading-snug text-steel">
          Gate toggles live in the Gates section. Switching exchange resets fee and slippage to that
          schedule — edit them after if you negotiated better rates.
        </p>
      </div>

      {grouped.map(([group, list]) => (
        <div key={group} className="rounded-lg border border-line bg-paper/40 p-3">
          <div className="mb-2 text-xs font-bold">{group}</div>
          <div className="grid gap-3 sm:grid-cols-2">
            {list.map((f) => (
              <FieldControl
                key={f.key}
                field={f}
                settings={settings}
                onPatch={patch}
                onExchange={changeExchange}
              />
            ))}
          </div>
        </div>
      ))}

      {(error || saveError) && (
        <p className="rounded-md border border-coral/30 bg-coral/5 px-3 py-2 font-mono text-[11px] text-coral">
          {error || saveError}
        </p>
      )}
      <Button className="w-full" onClick={onSave} disabled={saving}>
        {saving ? 'Saving…' : 'Save all settings'}
      </Button>
    </div>
  )
}

function FieldControl({
  field,
  settings,
  onPatch,
  onExchange,
}: {
  field: SettingsField
  settings: StrategySettings
  onPatch: (key: string, value: unknown) => void
  onExchange: (id: string) => void
}) {
  const raw = (settings as Record<string, unknown>)[field.key]
  const id = `setting-${field.key}`
  if (field.type === 'bool') {
    return (
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <Label htmlFor={id} className="text-[11px] font-semibold">
            {field.label}
          </Label>
          <Hint text={field.help} />
        </div>
        <Switch id={id} checked={Boolean(raw)} onCheckedChange={(v) => onPatch(field.key, v)} />
      </div>
    )
  }
  if (field.type === 'select') {
    return (
      <div>
        <Label htmlFor={id} className="text-[11px] font-semibold">
          {field.label}
        </Label>
        <Select
          value={String(raw ?? '')}
          onValueChange={(v) => (field.key === 'exchange' ? onExchange(v) : onPatch(field.key, v))}
        >
          <SelectTrigger id={id} className="mt-1 w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {(field.options ?? []).map((o) => (
              <SelectItem key={o} value={o}>
                {o}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Hint text={field.help} />
      </div>
    )
  }
  if (field.type === 'symbols') {
    const list = Array.isArray(raw) ? (raw as string[]) : []
    return (
      <div>
        <Label htmlFor={id} className="text-[11px] font-semibold">
          {field.label}
        </Label>
        <Input
          id={id}
          className="mt-1"
          value={list.join(', ')}
          placeholder="Empty = top volume"
          onChange={(e) =>
            onPatch(
              field.key,
              e.target.value
                .split(',')
                .map((s) => s.trim().toUpperCase())
                .filter(Boolean),
            )
          }
        />
        <Hint text={field.help} />
      </div>
    )
  }
  if (field.type === 'symbol') {
    return (
      <div>
        <Label htmlFor={id} className="text-[11px] font-semibold">
          {field.label}
        </Label>
        <Input
          id={id}
          className="mt-1 font-mono"
          value={String(raw ?? '')}
          onChange={(e) => onPatch(field.key, e.target.value.trim().toUpperCase())}
        />
        <Hint text={field.help} />
      </div>
    )
  }
  return (
    <div>
      <Label htmlFor={id} className="text-[11px] font-semibold">
        {field.label}
      </Label>
      <Input
        id={id}
        className="mt-1 font-mono"
        type="number"
        min={field.min}
        max={field.max}
        step={field.step}
        value={typeof raw === 'number' ? raw : 0}
        onChange={(e) => {
          const n = Number(e.target.value)
          onPatch(field.key, Number.isFinite(n) ? n : 0)
        }}
      />
      <Hint text={field.help} />
    </div>
  )
}

function Hint({ text }: { text: string }) {
  if (!text) return null
  return <p className="mt-0.5 text-[10px] leading-snug text-steel">{text}</p>
}
