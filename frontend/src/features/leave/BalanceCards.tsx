/**
 * Leave balances, with each figure's meaning shown rather than collapsed.
 *
 * Four numbers, not one, because they answer different questions: the ledger
 * balance, days awaiting a decision, days already approved for later, and what
 * is actually left to book. Showing only the last would leave an employee
 * unable to work out why it is what it is.
 */

import { CalendarClock, CircleAlert, Hourglass, Scale } from 'lucide-react';

import { Badge, Card, CardBody } from '@/components/ui/Surface';
import { formatDays, isPositive, type LeaveBalance } from '@/features/leave/api';
import { formatIsoLong } from '@/lib/dates';
import { cn } from '@/lib/utils';

export function BalanceCards({
  balances,
  onApply,
}: {
  balances: LeaveBalance[];
  onApply?: (leaveTypeId: number) => void;
}) {
  if (balances.length === 0) {
    return (
      <Card>
        <CardBody>
          <p className="text-ink-soft text-sm">
            No leave types are configured yet, so there is nothing to show. HR sets these up
            under Leave administration.
          </p>
        </CardBody>
      </Card>
    );
  }

  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
      {balances.map((balance) => (
        <BalanceCard key={balance.leave_type_id} balance={balance} onApply={onApply} />
      ))}
    </div>
  );
}

function BalanceCard({
  balance,
  onApply,
}: {
  balance: LeaveBalance;
  onApply?: ((leaveTypeId: number) => void) | undefined;
}) {
  const noEntitlement = balance.deducts_balance && !balance.has_policy;

  return (
    <Card>
      <CardBody className="space-y-3">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <h3 className="text-ink flex items-center gap-2 font-semibold">
              <span
                className="size-2.5 shrink-0 rounded-full"
                style={{ backgroundColor: balance.colour ?? 'var(--color-brand-500)' }}
                aria-hidden="true"
              />
              <span className="truncate">{balance.leave_type_name}</span>
            </h3>
            <p className="text-ink-muted mt-0.5 text-xs">
              {balance.leave_type_code} &middot; leave year {balance.leave_year}&ndash;
              {String(balance.leave_year + 1).slice(-2)}
            </p>
          </div>
          {!balance.is_paid && <Badge tone="neutral">Unpaid</Badge>}
        </div>

        {noEntitlement ? (
          <p className="text-warning-700 text-sm">
            No entitlement is configured for this type, so there is nothing to draw on. Ask HR
            to set a policy.
          </p>
        ) : (
          <>
            <div>
              <p className="text-ink text-3xl font-semibold tabular-nums">
                {formatDays(balance.available_days)}
                <span className="text-ink-muted ml-1.5 text-sm font-normal">
                  day{formatDays(balance.available_days) === '1' ? '' : 's'} to book
                </span>
              </p>
            </div>

            <dl className="text-ink-soft space-y-1 text-sm">
              <Row
                icon={Scale}
                label="Balance"
                value={formatDays(balance.balance_days)}
                hint="The ledger total today."
              />
              {isPositive(balance.pending_days) && (
                <Row
                  icon={Hourglass}
                  label="Awaiting a decision"
                  value={formatDays(balance.pending_days)}
                  hint="Held by requests nobody has decided yet."
                  tone="warning"
                />
              )}
              {isPositive(balance.committed_days) && (
                <Row
                  icon={CalendarClock}
                  label="Booked for later"
                  value={formatDays(balance.committed_days)}
                  hint="Approved leave dated after today."
                  tone="brand"
                />
              )}
              {balance.annual_quota_days && (
                <Row
                  icon={Scale}
                  label="Annual entitlement"
                  value={formatDays(balance.annual_quota_days)}
                  hint={
                    balance.accrual_frequency && balance.accrual_frequency !== 'none'
                      ? `Accrues ${balance.accrual_frequency.replace('_', ' ')}.`
                      : undefined
                  }
                />
              )}
            </dl>

            {isPositive(balance.expiring_days) && balance.expiring_on && (
              <p className="text-warning-700 flex items-start gap-1.5 text-sm">
                <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                <span>
                  {formatDays(balance.expiring_days)} day(s) expire on{' '}
                  {formatIsoLong(balance.expiring_on)}.
                </span>
              </p>
            )}

            {balance.requires_legal_review && (
              <p className="text-ink-muted text-xs">
                This entitlement comes from statute and has not been verified against a legal
                reference in this system.
              </p>
            )}
          </>
        )}

        {onApply && !noEntitlement && (
          <button
            type="button"
            onClick={() => onApply(balance.leave_type_id)}
            className="text-brand-700 text-sm font-medium underline underline-offset-2"
          >
            Apply for {balance.leave_type_name}
          </button>
        )}
      </CardBody>
    </Card>
  );
}

function Row({
  icon: Icon,
  label,
  value,
  hint,
  tone = 'neutral',
}: {
  icon: typeof Scale;
  label: string;
  value: string;
  hint?: string | undefined;
  tone?: 'neutral' | 'warning' | 'brand';
}) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="flex items-center gap-1.5">
        <Icon
          className={cn(
            'size-3.5 shrink-0',
            tone === 'warning' && 'text-warning-600',
            tone === 'brand' && 'text-brand-600',
          )}
          aria-hidden="true"
        />
        <span>{label}</span>
        {hint && <span className="text-ink-muted hidden text-xs sm:inline">{hint}</span>}
      </dt>
      <dd className="text-ink shrink-0 font-medium tabular-nums">{value}</dd>
    </div>
  );
}
