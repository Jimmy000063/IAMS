/**
 * Surfaces: the panels everything else sits on.
 *
 * A card here is a lit surface rather than a painted rectangle - a hairline
 * border, a one-pixel highlight along the top edge, and a shadow carrying a
 * trace of the brand hue. The difference is small on any single element and
 * considerable across a whole page.
 */

import type { HTMLAttributes, ReactNode, TdHTMLAttributes, ThHTMLAttributes } from 'react';

import { cn } from '@/lib/utils';

export function Card({
  className,
  children,
  interactive = false,
  ...props
}: HTMLAttributes<HTMLDivElement> & { interactive?: boolean }) {
  return (
    <div
      className={cn(
        'border-line bg-raised shadow-card edge-light rounded-[var(--radius-card)] border',
        interactive && 'hover-lift hover:border-line-strong cursor-pointer',
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}

/**
 * A panel that floats over the ambient wash rather than sitting on it.
 * For the few places where translucency earns its cost: the shell, overlays,
 * and anything that should feel attached to the background.
 */
export function GlassCard({ className, children, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        'border-line glass shadow-raised edge-light rounded-[var(--radius-panel)] border',
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}

export function CardHeader({
  title,
  description,
  action,
  icon: Icon,
}: {
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  icon?: React.ComponentType<{ className?: string }>;
}) {
  return (
    <div className="border-line flex flex-wrap items-start justify-between gap-3 border-b px-5 py-4">
      <div className="flex min-w-0 items-start gap-3">
        {Icon && (
          <span className="bg-brand-50 text-brand-600 dark:bg-brand-950/60 dark:text-brand-300 mt-0.5 grid size-9 shrink-0 place-items-center rounded-lg">
            <Icon className="size-4.5" />
          </span>
        )}
        <div className="min-w-0">
          <h2 className="text-ink text-base font-semibold">{title}</h2>
          {description && <p className="text-ink-soft mt-0.5 text-sm">{description}</p>}
        </div>
      </div>
      {action}
    </div>
  );
}

export function CardBody({ className, children, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn('p-5', className)} {...props}>
      {children}
    </div>
  );
}

export type BadgeTone = 'neutral' | 'brand' | 'success' | 'warning' | 'danger' | 'accent';

const BADGE_TONES: Record<BadgeTone, string> = {
  neutral: 'bg-sunken text-ink-soft ring-line-strong',
  brand:
    'bg-brand-50 text-brand-700 ring-brand-200 dark:bg-brand-950/50 dark:text-brand-200 dark:ring-brand-800/60',
  success:
    'bg-success-50 text-success-700 ring-success-600/30 dark:bg-success-900/30 dark:text-success-400 dark:ring-success-600/40',
  warning:
    'bg-warning-50 text-warning-700 ring-warning-600/30 dark:bg-warning-900/30 dark:text-warning-400 dark:ring-warning-600/40',
  danger:
    'bg-danger-50 text-danger-700 ring-danger-600/30 dark:bg-danger-900/30 dark:text-danger-400 dark:ring-danger-600/40',
  accent:
    'bg-accent-50 text-accent-700 ring-accent-300 dark:bg-accent-900/30 dark:text-accent-300 dark:ring-accent-700/50',
};

export function Badge({
  tone = 'neutral',
  children,
  className,
  dot = false,
}: {
  tone?: BadgeTone;
  children: ReactNode;
  className?: string;
  /** A small filled circle before the label, for status at a glance. */
  dot?: boolean;
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset',
        BADGE_TONES[tone],
        className,
      )}
    >
      {dot && (
        <span className="size-1.5 rounded-full bg-current opacity-70" aria-hidden="true" />
      )}
      {children}
    </span>
  );
}

export function PageHeader({
  title,
  description,
  action,
  eyebrow,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  /** A short label above the title: the section this page belongs to. */
  eyebrow?: string;
}) {
  return (
    <header className="animate-in mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        {eyebrow && (
          <p className="text-brand-600 dark:text-brand-300 mb-1 text-xs font-semibold tracking-[0.12em] uppercase">
            {eyebrow}
          </p>
        )}
        <h1 className="text-ink text-2xl font-semibold tracking-tight sm:text-[1.75rem]">
          {title}
        </h1>
        {description && <p className="text-ink-soft mt-1.5 max-w-2xl text-sm">{description}</p>}
      </div>
      {action}
    </header>
  );
}

/**
 * Horizontally scrollable table shell.
 *
 * Attendance and payroll tables are inherently wide. Rather than hide columns
 * on a small screen, the table scrolls inside a focusable region so a keyboard
 * user can reach the off-screen columns.
 */
export function TableShell({ children, caption }: { children: ReactNode; caption?: string }) {
  return (
    <div
      className="border-line -mx-5 overflow-x-auto border-y sm:mx-0 sm:rounded-[var(--radius-card)] sm:border"
      tabIndex={0}
      role="region"
      aria-label={caption}
    >
      <table className="w-full border-collapse text-left text-sm">
        {caption && <caption className="sr-only">{caption}</caption>}
        {children}
      </table>
    </div>
  );
}

export function Th({ className, children, ...props }: ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      scope="col"
      className={cn(
        'bg-sunken/80 text-ink-soft border-line sticky top-0 z-10 border-b px-4 py-2.5',
        'text-xs font-semibold tracking-wide whitespace-nowrap uppercase backdrop-blur',
        className,
      )}
      {...props}
    >
      {children}
    </th>
  );
}

export function Td({ className, children, ...props }: TdHTMLAttributes<HTMLTableCellElement>) {
  return (
    <td
      className={cn(
        'border-line text-ink border-b px-4 py-2.5 align-middle',
        'group-hover/row:bg-sunken/50 transition-colors',
        className,
      )}
      {...props}
    >
      {children}
    </td>
  );
}

/** A table row that highlights as a whole on hover. */
export function Tr({ className, children, ...props }: HTMLAttributes<HTMLTableRowElement>) {
  return (
    <tr className={cn('group/row', className)} {...props}>
      {children}
    </tr>
  );
}

/**
 * Initials in a tinted circle.
 *
 * Deliberately generated from the name rather than uploaded: an HRMS showing
 * photographs of staff is a privacy decision nobody has made, and initials
 * identify a row just as well in a directory.
 */
const AVATAR_TONES = [
  'bg-brand-100 text-brand-700 dark:bg-brand-900/60 dark:text-brand-200',
  'bg-accent-100 text-accent-700 dark:bg-accent-900/50 dark:text-accent-200',
  'bg-success-100 text-success-700 dark:bg-success-900/50 dark:text-success-400',
  'bg-cool-300/30 text-cool-600 dark:bg-cool-600/25 dark:text-cool-300',
  'bg-warning-100 text-warning-700 dark:bg-warning-900/50 dark:text-warning-400',
];

export function initialsOf(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  if (parts.length === 1) return parts[0]!.slice(0, 2).toUpperCase();
  return (parts[0]![0]! + parts[parts.length - 1]![0]!).toUpperCase();
}

export function Avatar({
  name,
  size = 'md',
  className,
}: {
  name: string;
  size?: 'xs' | 'sm' | 'md' | 'lg';
  className?: string;
}) {
  // A stable hash of the name, so the same person keeps the same colour
  // everywhere in the application rather than changing between pages.
  let hash = 0;
  for (let index = 0; index < name.length; index += 1) {
    hash = (hash * 31 + name.charCodeAt(index)) % 997;
  }
  const tone = AVATAR_TONES[hash % AVATAR_TONES.length];

  const sizes = {
    xs: 'size-6 text-[10px]',
    sm: 'size-8 text-xs',
    md: 'size-10 text-sm',
    lg: 'size-14 text-lg',
  } as const;

  return (
    <span
      aria-hidden="true"
      className={cn(
        'grid shrink-0 place-items-center rounded-full font-semibold select-none',
        'ring-1 ring-black/5 ring-inset dark:ring-white/10',
        sizes[size],
        tone,
        className,
      )}
    >
      {initialsOf(name)}
    </span>
  );
}
