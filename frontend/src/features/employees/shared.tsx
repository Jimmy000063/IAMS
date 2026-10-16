/** Small presentational pieces shared across the employee screens. */

import { Badge, type BadgeTone } from '@/components/ui/Surface';
import { humanise } from '@/lib/utils';

/**
 * Status colour mapping.
 *
 * `exited` is neutral rather than red: leaving a company is not an error state,
 * and colouring it as one misreads the directory at a glance.
 */
const STATUS_TONES: Record<string, BadgeTone> = {
  active: 'success',
  probation: 'accent',
  notice_period: 'warning',
  suspended: 'danger',
  exited: 'neutral',
};

export function EmploymentStatusBadge({ status }: { status: string }) {
  return <Badge tone={STATUS_TONES[status] ?? 'neutral'}>{humanise(status)}</Badge>;
}

export function EmploymentTypeBadge({ type }: { type: string }) {
  return <Badge tone="brand">{humanise(type)}</Badge>;
}

export function DefinitionList({
  items,
  columns = 2,
}: {
  items: { label: string; value: React.ReactNode }[];
  columns?: 1 | 2 | 3;
}) {
  const gridClass =
    columns === 1 ? 'sm:grid-cols-1' : columns === 3 ? 'sm:grid-cols-3' : 'sm:grid-cols-2';
  return (
    <dl className={`grid gap-4 ${gridClass}`}>
      {items.map((item) => (
        <div key={item.label}>
          <dt className="text-ink-muted text-xs font-medium tracking-wide uppercase">
            {item.label}
          </dt>
          <dd className="text-ink mt-1 text-sm break-words">{item.value ?? '—'}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Renders a value or an em dash, so empty fields look deliberate. */
export function Value({ children }: { children: React.ReactNode }) {
  if (children === null || children === undefined || children === '') {
    return <span className="text-ink-muted">—</span>;
  }
  return <>{children}</>;
}
