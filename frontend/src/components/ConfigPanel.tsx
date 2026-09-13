import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { api } from '@/lib/api'
import type { ConfigListItem, GateMap, IndicatorConfig, StrategySettings } from '@/lib/types'

type Props = {
  settings: StrategySettings
  gates: GateMap
  indicators: IndicatorConfig[]
  onApply: (payload: {
    settings: StrategySettings
    gates: GateMap
    indicators?: IndicatorConfig[]
  }) => void
}

export function ConfigPanel({ settings, gates, indicators, onApply }: Props) {
  const [list, setList] = useState<ConfigListItem[]>([])
  const [name, setName] = useState('my_scalp')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [ok, setOk] = useState('')

  const refresh = async () => {
    try {
      setList(await api.configs())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not list configs')
    }
  }

  useEffect(() => {
    refresh()
  }, [])

  const save = async () => {
    setBusy('save')
    setError('')
    setOk('')
    try {
      await api.saveConfig({
        name,
        label: name,
        data: {
          settings: { ...settings, gates },
          indicators,
        },
      })
      setOk(`Saved “${name}”`)
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setBusy('')
    }
  }

  const load = async (cfgName: string) => {
    setBusy(`load:${cfgName}`)
    setError('')
    setOk('')
    try {
      const applied = await api.applyConfig(cfgName)
      const s = (applied.settings || {}) as StrategySettings
      const g = (s.gates || gates) as GateMap
      const ind = (applied.config?.data?.indicators as IndicatorConfig[] | undefined) || undefined
      onApply({ settings: { ...settings, ...s, gates: g }, gates: g, indicators: ind })
      setName(cfgName)
      setOk(`Loaded “${cfgName}”`)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Load failed')
    } finally {
      setBusy('')
    }
  }

  const remove = async (cfgName: string) => {
    setBusy(`del:${cfgName}`)
    setError('')
    try {
      await api.deleteConfig(cfgName)
      await refresh()
      setOk(`Deleted “${cfgName}”`)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed')
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-steel">
        Save strategy + gates (+ indicators) as named configs. Built-ins: Balanced, HF Scalp.
      </p>
      <div className="space-y-1">
        <Label htmlFor="cfg-name">Config name</Label>
        <div className="flex gap-2">
          <Input
            id="cfg-name"
            value={name}
            onChange={(e) => setName(e.target.value.replace(/\s+/g, '_'))}
            placeholder="my_scalp"
          />
          <Button onClick={save} disabled={!name || busy === 'save'}>
            {busy === 'save' ? 'Saving…' : 'Save'}
          </Button>
        </div>
      </div>
      {error ? (
        <p className="rounded-md border border-coral/30 bg-coral/5 px-2 py-1.5 font-mono text-[11px] text-coral">
          {error}
        </p>
      ) : null}
      {ok ? <p className="font-mono text-[11px] text-mint">{ok}</p> : null}
      <div className="max-h-[280px] space-y-1.5 overflow-y-auto">
        {list.map((c) => (
          <div
            key={c.name}
            className="flex items-center gap-2 rounded-lg border border-line bg-paper/60 px-2.5 py-2"
          >
            <div className="min-w-0 flex-1">
              <div className="truncate text-[11px] font-semibold">{c.label || c.name}</div>
              <div className="font-mono text-[10px] text-steel">
                {c.builtin ? 'builtin preset' : 'saved config'}
              </div>
            </div>
            <Button
              variant="secondary"
              className="h-7 px-2 text-[10px]"
              disabled={Boolean(busy)}
              onClick={() => load(c.name)}
            >
              {busy === `load:${c.name}` ? '…' : 'Load'}
            </Button>
            {!c.builtin ? (
              <Button
                variant="ghost"
                className="h-7 px-2 text-[10px] text-coral"
                disabled={Boolean(busy)}
                onClick={() => remove(c.name)}
              >
                Del
              </Button>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  )
}
