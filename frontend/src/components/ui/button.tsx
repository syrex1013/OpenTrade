import { cva, type VariantProps } from 'class-variance-authority'
import type { ButtonHTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-1.5 rounded-md text-sm font-semibold transition-colors disabled:pointer-events-none disabled:opacity-50',
  {
    variants: {
      variant: {
        default: 'bg-cobalt text-white hover:bg-cobalt/90',
        secondary: 'border border-line bg-panel text-ink hover:bg-paper',
        danger: 'border border-coral/30 bg-white text-coral hover:bg-coral/5',
        ghost: 'text-steel hover:bg-paper hover:text-ink',
      },
      size: {
        default: 'h-9 px-3',
        sm: 'h-8 px-2.5 text-xs',
        lg: 'h-10 px-4',
      },
    },
    defaultVariants: { variant: 'default', size: 'default' },
  },
)

type Props = ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof buttonVariants>

export function Button({ className, variant, size, ...props }: Props) {
  return <button className={cn(buttonVariants({ variant, size }), className)} {...props} />
}
