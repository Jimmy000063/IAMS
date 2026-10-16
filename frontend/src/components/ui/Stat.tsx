/**
 * Data display: stat tiles, a progress ring, a sparkline and a bar meter.
 *
 * All drawn as inline SVG rather than pulled from a charting library. The
 * shapes needed here are simple, a library would add more to the bundle than
 * the whole feature set weighs, and hand-drawn SVG inherits the design tokens
 * directly instead of being themed through a second configuration system.
 *
 * Every one of these is decorative: the number is always present as text, so a
 * screen reader never depends on the drawing.
 */

import type { ReactNode } from 'react';

import { cn } from '@/lib/utils';

export function StatTile({
  label,
  value,
  hint,
  icon: Icon,
  tone = 'neutral',
  action,
  className,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  icon?: React.ComponentType<{ className?: string }>;
  tone?: 'neutral' | 'brand' | 'success' | 'warning' | 'danger';
  action?: ReactNode;
  className?: string;
}) {
  const tones = {
    neutral: 'bg-sunken text-ink-soft',
    brand: 'bg-brand-50 text-brand-600 dark:bg-brand-950/60 dark:text-brand-300',
    success: 'bg-success-50 text-success-700 dark:bg-success-900/40 dark:text-success-400',
    warning: 'bg-warning-50 text-warning-700 dark:bg-warning-900/40 dark:text-warning-400',
    danger: 'bg-danger-50 text-danger-700 dark:bg-danger-900/40 dark:text-danger-400',
  } as const;

  return (
    <div
      className={cn(
        'border-line bg-raised shadow-card edge-light hover-lift',
        'relative overflow-hidden rounded-[var(--radius-card)] border p-4',
        className,
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <p className="text-ink-soft text-xs font-medium tracking-wide uppercase">{label}</p>
        {Icon && (
          <span
            className={cn('grid size-8 shrink-0 place-items-center rounded-lg', tones[tone])}
          >
            <Icon className="size-4" />
          </span>
        )}
      </div>
      <p className="text-ink mt-2 text-2xl font-semibold tracking-tight tabular-nums">
        {value}
      </p>
      {hint && <div className="text-ink-muted mt-1 text-xs">{hint}</div>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}

/**
 * A progress ring.
 *
 * `value` and `total` rather than a percentage, because the caller always has
 * both and rounding in two places is how a ring ends up disagreeing with the
 * number printed inside it.
 */
export function Ring({
  value,
  total,
  size = 112,
  thickness = 10,
  label,
  sublabel,
  tone = 'brand',
}: {
  value: number;
  total: number;
  size?: number;
  thickness?: number;
  label?: ReactNode;
  sublabel?: ReactNode;
  tone?: 'brand' | 'success' | 'warning';
}) {
  const safeTotal = total > 0 ? total : 1;
  const fraction = Math.min(Math.max(value / safeTotal, 0), 1);
  const radius = (size - thickness) / 2;
  const circumference = 2 * Math.PI * radius;
  const strokes = {
    brand: 'var(--color-brand-500)',
    success: 'var(--color-success-600)',
    warning: 'var(--color-warning-600)',
  } as const;

  return (
    <div className="relative grid place-items-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90" aria-hidden="true">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="var(--surface-sunken)"
          strokeWidth={thickness}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={strokes[tone]}
          strokeWidth={thickness}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - fraction)}
          style={{
            transition: 'stroke-dashoffset var(--duration-slow) var(--ease-out-soft)',
          }}
        />
      </svg>
      <div className="absolute grid place-items-center text-center">
        {label && <span className="text-ink text-xl font-semibold tabular-nums">{label}</span>}
        {sublabel && <span className="text-ink-muted text-[11px]">{sublabel}</span>}
      </div>
    </div>
  );
}

/**
 * A sparkline over a short series. Flat series are drawn as a flat line rather
 * than scaled up into fake drama.
 */
export function Sparkline({
  points,
  width = 120,
  height = 36,
  tone = 'brand',
  className,
}: {
  points: number[];
  width?: number;
  height?: number;
  tone?: 'brand' | 'success' | 'warning' | 'danger';
  className?: string;
}) {
  if (points.length < 2) return null;

  const max = Math.max(...points);
  const min = Math.min(...points);
  const span = max - min || 1;
  const step = width / (points.length - 1);

  const coordinates = points.map((point, index) => {
    const x = index * step;
    // A 2px inset top and bottom, so a peak is not clipped by the viewBox.
    const y = height - 2 - ((point - min) / span) * (height - 4);
    return [x, y] as const;
  });

  const line = coordinates
    .map(([x, y], index) => `${index === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`)
    .join(' ');
  const area = `${line} L${width},${height} L0,${height} Z`;

  const strokes = {
    brand: 'var(--color-brand-500)',
    success: 'var(--color-success-600)',
    warning: 'var(--color-warning-600)',
    danger: 'var(--color-danger-600)',
  } as const;

  const gradientId = `spark-${tone}-${points.length}-${Math.round(max)}`;

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      className={cn('overflow-visible', className)}
      aria-hidden="true"
    >
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={strokes[tone]} stopOpacity="0.25" />
          <stop offset="100%" stopColor={strokes[tone]} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${gradientId})`} />
      <path
        d={line}
        fill="none"
        stroke={strokes[tone]}
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** A labelled proportion bar, for "11 of 14" style figures. */
export function Meter({
  segments,
  className,
}: {
  segments: {
    label: string;
    value: number;
    tone: 'brand' | 'success' | 'warning' | 'danger' | 'neutral';
  }[];
  className?: string;
}) {
  const total = segments.reduce((sum, segment) => sum + segment.value, 0);
  if (total === 0) return null;

  const fills = {
    brand: 'bg-brand-500',
    success: 'bg-success-600',
    warning: 'bg-warning-600',
    danger: 'bg-danger-600',
    neutral: 'bg-line-strong',
  } as const;

  return (
    <div className={className}>
      <div className="bg-sunken flex h-2 overflow-hidden rounded-full" aria-hidden="true">
        {segments
          .filter((segment) => segment.value > 0)
          .map((segment) => (
            <div
              key={segment.label}
              className={cn(fills[segment.tone], 'transition-[width] duration-500')}
              style={{ width: `${(segment.value / total) * 100}%` }}
            />
          ))}
      </div>
      <div className="mt-2.5 flex flex-wrap gap-x-4 gap-y-1">
        {segments
          .filter((segment) => segment.value > 0)
          .map((segment) => (
            <span
              key={segment.label}
              className="text-ink-soft flex items-center gap-1.5 text-xs"
            >
              <span
                className={cn('size-2 rounded-full', fills[segment.tone])}
                aria-hidden="true"
              />
              {segment.label}
              <span className="text-ink font-medium tabular-nums">{segment.value}</span>
            </span>
          ))}
      </div>
    </div>
  );
}
