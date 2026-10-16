import { Loader2 } from 'lucide-react';
import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react';

import { cn } from '@/lib/utils';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'link';
export type ButtonSize = 'sm' | 'md' | 'lg';

/**
 * The primary button is a gradient rather than a flat fill, and lifts slightly
 * on hover. It is the only gradient in the interface, which is what keeps it
 * reading as "the action on this screen" without a second colour or a larger
 * size.
 */
const VARIANTS: Record<ButtonVariant, string> = {
  primary: cn(
    'bg-linear-to-b from-brand-500 to-brand-600 text-white shadow-sm',
    'hover:from-brand-400 hover:to-brand-600 hover:shadow-[0_6px_16px_-6px_var(--color-brand-600)]',
    'active:translate-y-px disabled:from-brand-300 disabled:to-brand-300 disabled:shadow-none',
  ),
  secondary: cn(
    'border-line-strong bg-raised text-ink border shadow-xs',
    'hover:bg-sunken hover:border-ink-muted/40 active:translate-y-px',
    'disabled:text-ink-muted',
  ),
  ghost: 'text-ink-soft hover:bg-sunken hover:text-ink active:translate-y-px',
  danger: cn(
    'bg-linear-to-b from-danger-600 to-danger-700 text-white shadow-sm',
    'hover:from-danger-600 hover:to-danger-900 active:translate-y-px',
    'disabled:from-danger-100 disabled:to-danger-100',
  ),
  link: 'text-brand-600 dark:text-brand-300 underline-offset-4 hover:underline',
};

const SIZES: Record<ButtonSize, string> = {
  sm: 'h-8 gap-1.5 px-3 text-sm',
  md: 'h-9.5 gap-2 px-4 text-sm',
  lg: 'h-11 gap-2 px-5 text-base',
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  isLoading?: boolean;
  leadingIcon?: ReactNode;
  fullWidth?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = 'primary',
    size = 'md',
    isLoading = false,
    leadingIcon,
    fullWidth = false,
    className,
    children,
    disabled,
    type = 'button',
    ...props
  },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      // `aria-busy` tells assistive technology the control is working; the
      // spinner alone conveys nothing to a screen reader.
      aria-busy={isLoading || undefined}
      disabled={disabled || isLoading}
      className={cn(
        'inline-flex items-center justify-center rounded-lg font-medium whitespace-nowrap',
        'transition-[background,box-shadow,transform,border-color,color] duration-150',
        'disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-70',
        VARIANTS[variant],
        SIZES[size],
        fullWidth && 'w-full',
        className,
      )}
      {...props}
    >
      {isLoading ? <Loader2 className="size-4 animate-spin" aria-hidden="true" /> : leadingIcon}
      {children}
    </button>
  );
});
