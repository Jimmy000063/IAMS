/**
 * Comp-off for the employee: the balance, the individual credits, and requests.
 *
 * Credits are shown as a list rather than a single number because each one
 * **expires on its own date**. A total would hide the fact that one of them
 * lapses next week, which is the thing an employee most needs to know.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CircleAlert, Clock, Hourglass, Plus } from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/Button';
import { DatePicker } from '@/components/ui/DatePicker';
import { SelectField, TextAreaField } from '@/components/ui/Field';
import { Alert, EmptyState, ErrorPanel, SkeletonRows } from '@/components/ui/Feedback';
import { Modal } from '@/components/ui/Modal';
import {
  Badge,
  Card,
  CardBody,
  CardHeader,
  TableShell,
  Td,
  Th,
  type BadgeTone,
} from '@/components/ui/Surface';
import {
  applyForCompoff,
  cancelCompoff,
  compoffKeys,
  fetchMyCompoffApplications,
  fetchMyCompoffBalance,
  fetchMyCredits,
  withdrawCompoff,
  type CompoffApplicationSummary,
  type CreditStatus,
} from '@/features/compoff/api';
import { formatDays, isPositive } from '@/features/leave/api';
import { formatIsoLong, todayIso, type IsoDate } from '@/lib/dates';
import { useToast } from '@/lib/toast';

const CREDIT_TONES: Record<CreditStatus, BadgeTone> = {
  pending_verification: 'warning',
  available: 'success',
  rejected: 'danger',
  expired: 'neutral',
  reversed: 'neutral',
};

const CREDIT_LABELS: Record<CreditStatus, string> = {
  pending_verification: 'Awaiting verification',
  available: 'Available',
  rejected: 'Rejected',
  expired: 'Expired',
  reversed: 'Reversed',
};

const STATUS_TONES: Record<string, BadgeTone> = {
  pending_manager: 'warning',
  pending_hr: 'warning',
  approved: 'success',
  rejected: 'danger',
  withdrawn: 'neutral',
  cancelled: 'neutral',
};

export function CompoffPanel() {
  const toast = useToast();
  const queryClient = useQueryClient();

  const [applying, setApplying] = useState(false);
  const [prompt, setPrompt] = useState<{
    application: CompoffApplicationSummary;
    kind: 'withdraw' | 'cancel';
  } | null>(null);
  const [reason, setReason] = useState('');

  const balance = useQuery({
    queryKey: compoffKeys.myBalance(),
    queryFn: () => fetchMyCompoffBalance(),
  });
  const credits = useQuery({
    queryKey: compoffKeys.myCredits(),
    queryFn: fetchMyCredits,
  });
  const applications = useQuery({
    queryKey: compoffKeys.myApplications(),
    queryFn: fetchMyCompoffApplications,
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: compoffKeys.all });
  };

  const act = useMutation({
    mutationFn: () => {
      if (!prompt) throw new Error('Nothing to act on');
      return prompt.kind === 'withdraw'
        ? withdrawCompoff(prompt.application.id, reason || null)
        : cancelCompoff(prompt.application.id, reason);
    },
    onSuccess: () => {
      setPrompt(null);
      setReason('');
      invalidate();
      toast.success('Done. The credits are back in your balance.');
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  if (balance.isError) {
    return <ErrorPanel error={balance.error} onRetry={() => void balance.refetch()} />;
  }

  const data = balance.data;
  const liveCredits = (credits.data ?? []).filter(
    (item) => item.status === 'available' || item.status === 'pending_verification',
  );

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader
          title="Comp-off balance"
          description="Earned by working on a day off. Each credit expires on its own date."
          action={
            <Button
              onClick={() => setApplying(true)}
              disabled={!data || !isPositive(data.available_days)}
              title={
                data && !isPositive(data.available_days)
                  ? 'You have no verified comp-off credit to take.'
                  : undefined
              }
            >
              <Plus className="size-4" aria-hidden="true" />
              Take comp-off
            </Button>
          }
        />
        <CardBody>
          {balance.isPending ? (
            <SkeletonRows rows={2} />
          ) : data ? (
            <>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <Figure
                  label="Available to take"
                  value={formatDays(data.available_days)}
                  hint="Verified and unexpired."
                  tone="success"
                />
                <Figure
                  label="Awaiting verification"
                  value={formatDays(data.pending_verification_days)}
                  hint="Earned, but not usable until HR confirms it."
                  icon={Hourglass}
                  tone={isPositive(data.pending_verification_days) ? 'warning' : 'neutral'}
                />
                <Figure
                  label="Held by a request"
                  value={formatDays(data.committed_days)}
                  hint="Reserved for a request awaiting a decision."
                />
                <Figure
                  label="Expired"
                  value={formatDays(data.expired_days)}
                  hint="Past its date and no longer usable."
                  tone={isPositive(data.expired_days) ? 'danger' : 'neutral'}
                />
              </div>

              {isPositive(data.expiring_soon_days) && data.expiring_on && (
                <div className="mt-4">
                  <Alert variant="warning" title="Use it or lose it" icon={CircleAlert}>
                    {formatDays(data.expiring_soon_days)} day(s) of comp-off expire on{' '}
                    {formatIsoLong(data.expiring_on)}. Comp-off is spent earliest-expiring
                    first, so taking a day now uses that one.
                  </Alert>
                </div>
              )}
            </>
          ) : null}
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Your credits"
          description="Shown one by one, because each expires separately - a single total would hide the one lapsing next week."
        />
        <CardBody>
          {credits.isPending ? (
            <SkeletonRows rows={3} />
          ) : liveCredits.length === 0 ? (
            <EmptyState
              icon={Clock}
              title="No comp-off credits"
              description="Credits are created from attendance when you work on a weekly off or a holiday."
            />
          ) : (
            <TableShell caption="Comp-off credits">
              <thead>
                <tr>
                  <Th>Earned on</Th>
                  <Th>Worked</Th>
                  <Th>Worth</Th>
                  <Th>Expires</Th>
                  <Th>Status</Th>
                </tr>
              </thead>
              <tbody>
                {liveCredits.map((credit) => {
                  const remaining =
                    Number.parseFloat(credit.credit_days) -
                    Number.parseFloat(credit.allocated_days);
                  return (
                    <tr key={credit.id}>
                      <Td className="whitespace-nowrap">
                        {formatIsoLong(credit.earned_date)}
                        <p className="text-ink-muted text-xs">
                          {credit.day_type.replace('_', ' ')}
                          {credit.source === 'manual' && ' · granted by HR'}
                        </p>
                      </Td>
                      <Td>
                        {credit.worked_minutes > 0
                          ? `${Math.floor(credit.worked_minutes / 60)}h ${credit.worked_minutes % 60}m`
                          : '—'}
                      </Td>
                      <Td className="tabular-nums">
                        {formatDays(credit.credit_days)}
                        {remaining !== Number.parseFloat(credit.credit_days) && (
                          <p className="text-ink-muted text-xs">{remaining.toFixed(2)} left</p>
                        )}
                      </Td>
                      <Td className="whitespace-nowrap">
                        {credit.expiry_date ? (
                          formatIsoLong(credit.expiry_date)
                        ) : (
                          <span className="text-ink-muted">never</span>
                        )}
                      </Td>
                      <Td>
                        <Badge tone={CREDIT_TONES[credit.status]}>
                          {CREDIT_LABELS[credit.status]}
                        </Badge>
                      </Td>
                    </tr>
                  );
                })}
              </tbody>
            </TableShell>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Your comp-off requests" />
        <CardBody>
          {applications.isPending ? (
            <SkeletonRows rows={3} />
          ) : (applications.data ?? []).length === 0 ? (
            <EmptyState title="No comp-off requests yet" />
          ) : (
            <TableShell caption="Comp-off requests">
              <thead>
                <tr>
                  <Th>Dates</Th>
                  <Th>Days</Th>
                  <Th>Status</Th>
                  <Th>Reason</Th>
                  <Th className="text-right">Actions</Th>
                </tr>
              </thead>
              <tbody>
                {(applications.data ?? []).map((application) => (
                  <tr key={application.id}>
                    <Td className="whitespace-nowrap">
                      {application.start_date === application.end_date
                        ? formatIsoLong(application.start_date)
                        : `${formatIsoLong(application.start_date)} to ${formatIsoLong(application.end_date)}`}
                    </Td>
                    <Td className="tabular-nums">{formatDays(application.total_days)}</Td>
                    <Td>
                      <Badge tone={STATUS_TONES[application.status] ?? 'neutral'}>
                        {application.status.replace('_', ' ')}
                      </Badge>
                    </Td>
                    <Td className="text-ink-soft max-w-xs text-sm">
                      {application.reason ?? '—'}
                    </Td>
                    <Td className="text-right">
                      {(application.status === 'pending_manager' ||
                        application.status === 'pending_hr') && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => {
                            setReason('');
                            setPrompt({ application, kind: 'withdraw' });
                          }}
                        >
                          Withdraw
                        </Button>
                      )}
                      {application.status === 'approved' && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => {
                            setReason('');
                            setPrompt({ application, kind: 'cancel' });
                          }}
                        >
                          Cancel
                        </Button>
                      )}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </TableShell>
          )}
        </CardBody>
      </Card>

      <ApplyCompoffModal
        open={applying}
        availableDays={data?.available_days ?? '0.00'}
        onClose={() => setApplying(false)}
        onApplied={() => {
          setApplying(false);
          invalidate();
        }}
      />

      <Modal
        open={prompt !== null}
        onClose={() => setPrompt(null)}
        title={prompt?.kind === 'withdraw' ? 'Withdraw this request' : 'Cancel this comp-off'}
        description={
          prompt?.kind === 'withdraw'
            ? 'The credits it was holding go back to your balance.'
            : 'The credits return with their original expiry dates - cancelling does not extend them.'
        }
        footer={
          <>
            <Button variant="secondary" onClick={() => setPrompt(null)}>
              Keep it
            </Button>
            <Button
              variant="danger"
              onClick={() => act.mutate()}
              disabled={act.isPending || (prompt?.kind === 'cancel' && reason.trim() === '')}
            >
              {act.isPending ? 'Working…' : 'Confirm'}
            </Button>
          </>
        }
      >
        <TextAreaField
          label="Reason"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          rows={3}
          required={prompt?.kind === 'cancel'}
          hint={prompt?.kind === 'cancel' ? 'Required.' : 'Optional.'}
        />
      </Modal>
    </div>
  );
}

function Figure({
  label,
  value,
  hint,
  icon: Icon,
  tone = 'neutral',
}: {
  label: string;
  value: string;
  hint: string;
  icon?: typeof Clock;
  tone?: 'neutral' | 'success' | 'warning' | 'danger';
}) {
  const toneClass = {
    neutral: 'text-ink',
    success: 'text-success-700',
    warning: 'text-warning-700',
    danger: 'text-danger-700',
  }[tone];

  return (
    <div className="border-line rounded-lg border p-3">
      <p className="text-ink-soft flex items-center gap-1.5 text-xs font-medium tracking-wide uppercase">
        {Icon && <Icon className="size-3.5" aria-hidden="true" />}
        {label}
      </p>
      <p className={`mt-1 text-xl font-semibold tabular-nums ${toneClass}`}>{value}</p>
      <p className="text-ink-muted mt-0.5 text-xs">{hint}</p>
    </div>
  );
}

function ApplyCompoffModal({
  open,
  availableDays,
  onClose,
  onApplied,
}: {
  open: boolean;
  availableDays: string;
  onClose: () => void;
  onApplied: () => void;
}) {
  const toast = useToast();
  const [start, setStart] = useState<IsoDate>(todayIso());
  const [end, setEnd] = useState<IsoDate>(todayIso());
  const [portion, setPortion] = useState('full');
  const [reason, setReason] = useState('');

  const submit = useMutation({
    mutationFn: () =>
      applyForCompoff({
        start_date: start,
        end_date: end,
        start_portion: portion,
        reason: reason.trim() || null,
      }),
    onSuccess: (application) => {
      setReason('');
      toast.success(
        `Submitted: ${formatDays(application.total_days)} day(s) against ${application.allocations.length} credit(s).`,
      );
      onApplied();
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Take comp-off"
      description={`You have ${formatDays(availableDays)} day(s) of verified comp-off available.`}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => submit.mutate()} disabled={submit.isPending}>
            {submit.isPending ? 'Submitting…' : 'Submit request'}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Alert variant="info" title="How credits are spent">
          Earliest-expiring first, so you never lose a credit you could have used. The credits
          are held as soon as you submit, and returned if the request is rejected or withdrawn.
        </Alert>

        <div className="grid gap-4 sm:grid-cols-2">
          <DatePicker
            label="From"
            value={start}
            onChange={(value) => {
              setStart(value);
              if (value > end) setEnd(value);
            }}
          />
          <DatePicker label="To" value={end} onChange={setEnd} min={start} />
        </div>

        {start === end && (
          <SelectField
            label="Portion of the day"
            value={portion}
            onChange={(event) => setPortion(event.target.value)}
            options={[
              { value: 'full', label: 'Full day' },
              { value: 'first_half', label: 'First half' },
              { value: 'second_half', label: 'Second half' },
            ]}
          />
        )}

        <TextAreaField
          label="Reason"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          rows={2}
          hint="Optional, but it helps whoever reviews the request."
        />
      </div>
    </Modal>
  );
}
