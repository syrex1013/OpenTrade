import type { InputHTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        'flex h-9 w-full rounded-lg border border-line bg-paper px-3 font-mono text-sm text-ink outline-none transition-colors placeholder:text-steel/70 focus:border-brand',
        className,
      )}
      {...props}
    />
  )
}
