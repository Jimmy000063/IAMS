/**
 * Loading, empty, error and status presentation.
 *
 * Every data view in the application is expected to handle four states
 * explicitly - loading, empty, failed, loaded - and these are the shared
 * components for the first three.
 *
 * One judgement is encoded here: **a refused rule is not a failure.** When the
 * server answers "your account is not linked to an employee record" or "this
 * setting has not been confirmed", nothing has gone wrong - the system is
 * telling the user how it works. Rendering that in alarm red, next to a Try
 * again button that cannot help, teaches people to distrust the interface.
 * Those responses are shown as a calm notice; genuine faults stay red.
 */

import { AlertCircle, Info, Inbox, Loader2, RefreshCw, TriangleAlert } from 'lucide-react';
import type { ReactNode } from 'react';

import { Button } from '@/components/ui/Button';
import { ApiError } from '@/lib/api';
import { cn } from '@/lib/utils';

export function Spinner({
  className,
  label = 'Loading',
}: {
  className?: string;
  label?: string;
}) {
  return (
    <span role="status" className="inline-flex items-center gap-2">
      <Loader2
        className={cn('text-brand-600 size-4 animate-spin', className)}
        aria-hidden="true"
      />
      <span className="sr-only">{label}</span>
    </span>
  );
}

export function LoadingPanel({ label = 'Loading...' }: { label?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-16" role="status">
      <span className="relative grid size-10 place-items-center">
        <span className="border-brand-500/25 absolute inset-0 rounded-full border-2" />
        <Loader2 className="text-brand-500 size-10 animate-spin" aria-hidden="true" />
      </span>
      <p className="text-ink-soft text-sm">{label}</p>
    </div>
  );
}

/**
 * Skeleton rows, used where the shape of the incoming data is known.
 *
 * A travelling highlight rather than a pulse: movement reads as "working",
 * while a block fading in and out reads as something that has stalled.
 */
export function SkeletonRows({ rows = 5 }: { rows?: number }) {
  return (
    <div className="space-y-2.5" aria-hidden="true">
      {Array.from({ length: rows }, (_, index) => (
        <div
          key={index}
          className="shimmer h-12 rounded-[var(--radius-card)]"
          style={{ animationDelay: `${index * 80}ms` }}
        />
      ))}
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
  icon: Icon = Inbox,
  tone = 'neutral',
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  icon?: typeof Inbox;
  /** `brand` for a state the user is meant to act on, `neutral` for "nothing here". */
  tone?: 'neutral' | 'brand';
}) {
  return (
    <div className="border-line flex flex-col items-center justify-center gap-4 rounded-[var(--radius-card)] border border-dashed px-6 py-14 text-center">
      <span
        className={cn(
          'grid size-14 place-items-center rounded-2xl',
          tone === 'brand'
            ? 'bg-brand-50 text-brand-500 dark:bg-brand-950/60 dark:text-brand-300'
            : 'bg-sunken text-ink-muted',
        )}
      >
        <Icon className="size-6" aria-hidden="true" />
      </span>
      <div>
        <p className="text-ink font-medium">{title}</p>
        {description && (
          <p className="text-ink-soft mx-auto mt-1.5 max-w-sm text-sm">{description}</p>
        )}
      </div>
      {action}
    </div>
  );
}

/**
 * Error codes that mean "the system is working and this is the rule", as
 * opposed to "something broke". They get the calm treatment.
 */
const POLICY_CODES = new Set([
  'policy_violation',
  'configuration_missing',
  'validation_error',
  'not_found',
  'conflict',
  'permission_denied',
]);

/**
 * Failure panel. Shows the server's message plus the request id, which is the
 * reference a user can quote and an administrator can find in the logs.
 */
export function ErrorPanel({
  error,
  onRetry,
  title,
}: {
  error: unknown;
  onRetry?: () => void;
  title?: string;
}) {
  const apiError = error instanceof ApiError ? error : null;
  const message =
    apiError?.message ??
    (error instanceof Error ? error.message : 'An unexpected error occurred.');

  const isPolicy = apiError !== null && POLICY_CODES.has(apiError.code);
  const heading = title ?? (isPolicy ? 'Nothing to show here yet' : 'Could not load this view');
  const Icon = isPolicy ? Info : AlertCircle;

  return (
    <div
      role="alert"
      className={cn(
        'rounded-[var(--radius-card)] border p-5',
        isPolicy
          ? 'border-line bg-sunken/60 text-ink'
          : 'border-danger-600/30 bg-danger-50 text-danger-900 dark:bg-danger-900/25 dark:text-danger-100',
      )}
    >
      <div className="flex items-start gap-3">
        <span
          className={cn(
            'mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg',
            isPolicy
              ? 'bg-brand-50 text-brand-600 dark:bg-brand-950/60 dark:text-brand-300'
              : 'bg-danger-100 text-danger-700 dark:bg-danger-900/50 dark:text-danger-300',
          )}
        >
          <Icon className="size-4.5" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="font-medium">{heading}</p>
          <p className={cn('mt-1 text-sm', isPolicy && 'text-ink-soft')}>{message}</p>
          {apiError?.details.length ? (
            <ul
              className={cn('mt-2 list-inside list-disc text-sm', isPolicy && 'text-ink-soft')}
            >
              {apiError.details.map((detail, index) => (
                <li key={index}>
                  {detail.field ? `${detail.field}: ` : ''}
                  {detail.message}
                </li>
              ))}
            </ul>
          ) : null}
          {apiError?.requestId && (
            <p className="text-ink-muted mt-2 font-mono text-xs">
              Reference: {apiError.requestId}
            </p>
          )}
          {onRetry && (
            <Button
              variant="secondary"
              size="sm"
              onClick={onRetry}
              leadingIcon={<RefreshCw className="size-4" aria-hidden="true" />}
              className="mt-3"
            >
              Try again
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

export type AlertVariant = 'info' | 'warning' | 'danger' | 'success';

const ALERT_STYLES: Record<AlertVariant, string> = {
  info: 'border-brand-600/25 bg-brand-50 text-brand-900 dark:bg-brand-950/40 dark:text-brand-100',
  warning:
    'border-warning-600/35 bg-warning-50 text-warning-900 dark:bg-warning-900/25 dark:text-warning-100',
  danger:
    'border-danger-600/30 bg-danger-50 text-danger-900 dark:bg-danger-900/25 dark:text-danger-100',
  success:
    'border-success-600/30 bg-success-50 text-success-900 dark:bg-success-900/25 dark:text-success-100',
};

const ALERT_ICON_STYLES: Record<AlertVariant, string> = {
  info: 'bg-brand-100 text-brand-700 dark:bg-brand-900/60 dark:text-brand-200',
  warning: 'bg-warning-100 text-warning-700 dark:bg-warning-900/50 dark:text-warning-200',
  danger: 'bg-danger-100 text-danger-700 dark:bg-danger-900/50 dark:text-danger-200',
  success: 'bg-success-100 text-success-700 dark:bg-success-900/50 dark:text-success-200',
};

export function Alert({
  variant = 'info',
  title,
  children,
  icon: Icon = TriangleAlert,
}: {
  variant?: AlertVariant;
  title?: string;
  children: ReactNode;
  icon?: typeof TriangleAlert;
}) {
  return (
    <div className={cn('rounded-[var(--radius-card)] border p-4', ALERT_STYLES[variant])}>
      <div className="flex items-start gap-3">
        <span
          className={cn(
            'mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg',
            ALERT_ICON_STYLES[variant],
          )}
        >
          <Icon className="size-4.5" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1 text-sm">
          {title && <p className="font-medium">{title}</p>}
          <div className={cn(title && 'mt-1 opacity-90')}>{children}</div>
        </div>
      </div>
    </div>
  );
}
