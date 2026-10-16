/**
 * One application in full, with every stage decision in order.
 *
 * The history is append-only in the database and shown in full here: a request
 * a manager approved and HR refused must stay legible afterwards, including the
 * comments each left.
 */

import { useQuery } from '@tanstack/react-query';
import { Check, SkipForward, X } from 'lucide-react';

import { ErrorPanel, SkeletonRows } from '@/components/ui/Feedback';
import { Modal } from '@/components/ui/Modal';
import { Badge } from '@/components/ui/Surface';
import { fetchApplication, formatDays, leaveKeys } from '@/features/leave/api';
import { formatIsoLong } from '@/lib/dates';
import { formatDateTime } from '@/lib/utils';

const STAGE_LABELS: Record<string, string> = {
  reporting_manager: 'Reporting manager',
  hr_admin: 'HR / Admin',
};

export function DecisionHistory({
  applicationId,
  onClose,
}: {
  applicationId: number | null;
  onClose: () => void;
}) {
  const application = useQuery({
    queryKey: leaveKeys.application(applicationId ?? 0),
    queryFn: () => fetchApplication(applicationId as number),
    enabled: applicationId !== null,
  });

  return (
    <Modal
      open={applicationId !== null}
      onClose={onClose}
      title="Leave application"
      className="max-w-2xl"
    >
      {application.isPending ? (
        <SkeletonRows rows={4} />
      ) : application.isError ? (
        <ErrorPanel error={application.error} onRetry={() => void application.refetch()} />
      ) : application.data ? (
        <div className="space-y-5">
          <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
            <Detail label="Dates">
              {application.data.start_date === application.data.end_date
                ? formatIsoLong(application.data.start_date)
                : `${formatIsoLong(application.data.start_date)} to ${formatIsoLong(application.data.end_date)}`}
            </Detail>
            <Detail label="Days deducted">
              {formatDays(application.data.total_days)}
              {application.data.unpaid_days !== '0.00' && (
                <span className="text-warning-700">
                  {' '}
                  ({formatDays(application.data.paid_days)} paid,{' '}
                  {formatDays(application.data.unpaid_days)} unpaid)
                </span>
              )}
            </Detail>
            <Detail label="Status">{application.data.status.replace('_', ' ')}</Detail>
            <Detail label="Submitted">{formatDateTime(application.data.created_at)}</Detail>
            {application.data.reason && (
              <Detail label="Reason" wide>
                {application.data.reason}
              </Detail>
            )}
            {application.data.cancellation_reason && (
              <Detail label="Cancellation reason" wide>
                {application.data.cancellation_reason}
              </Detail>
            )}
          </dl>

          <section>
            <h3 className="text-ink text-sm font-semibold">Days</h3>
            <p className="text-ink-soft mt-0.5 text-sm">
              Non-working days inside the range are shown but not deducted, unless the leave
              type is counted in calendar days.
            </p>
            <ul className="mt-2 space-y-1 text-sm">
              {application.data.days.map((day) => (
                <li key={day.leave_date} className="flex items-center justify-between gap-3">
                  <span>
                    {formatIsoLong(day.leave_date)}
                    {day.portion !== 'full' && (
                      <span className="text-ink-muted"> · {day.portion.replace('_', ' ')}</span>
                    )}
                  </span>
                  {day.counts_against_balance ? (
                    <Badge tone={day.is_paid ? 'success' : 'warning'}>
                      {formatDays(day.day_fraction)} day{day.is_paid ? '' : ' unpaid'}
                    </Badge>
                  ) : (
                    <Badge tone="neutral">
                      {day.day_type.replace('_', ' ')} — not deducted
                    </Badge>
                  )}
                </li>
              ))}
            </ul>
          </section>

          <section>
            <h3 className="text-ink text-sm font-semibold">Decision history</h3>
            <p className="text-ink-soft mt-0.5 text-sm">
              Appended, never rewritten — the database refuses an edit to a recorded decision.
            </p>
            {application.data.approvals.length === 0 ? (
              <p className="text-ink-muted mt-2 text-sm">No decisions recorded yet.</p>
            ) : (
              <ol className="mt-2 space-y-3">
                {application.data.approvals.map((approval) => {
                  const Icon =
                    approval.decision === 'approved'
                      ? Check
                      : approval.decision === 'rejected'
                        ? X
                        : SkipForward;
                  return (
                    <li key={approval.sequence} className="flex gap-3">
                      <span
                        className={
                          approval.decision === 'approved'
                            ? 'text-success-700'
                            : approval.decision === 'rejected'
                              ? 'text-danger-700'
                              : 'text-ink-muted'
                        }
                      >
                        <Icon className="mt-0.5 size-4" aria-hidden="true" />
                      </span>
                      <div className="min-w-0">
                        <p className="text-ink text-sm font-medium">
                          {STAGE_LABELS[approval.stage] ?? approval.stage} — {approval.decision}
                        </p>
                        <p className="text-ink-muted text-xs">
                          {formatDateTime(approval.decided_at)}
                          {approval.was_auto_skipped && ' · skipped automatically'}
                        </p>
                        {approval.comment && (
                          <p className="text-ink-soft mt-1 text-sm">{approval.comment}</p>
                        )}
                      </div>
                    </li>
                  );
                })}
              </ol>
            )}
          </section>

          {application.data.policy_snapshot && (
            <details>
              <summary className="text-brand-700 cursor-pointer text-sm font-medium">
                The rules that governed this request
              </summary>
              <p className="text-ink-soft mt-2 text-sm">
                Captured at submission, so this decision stays explicable even after the policy
                and settings have changed.
              </p>
              <pre className="bg-sunken text-ink-soft mt-2 overflow-x-auto rounded p-3 font-mono text-xs">
                {JSON.stringify(application.data.policy_snapshot, null, 2)}
              </pre>
            </details>
          )}
        </div>
      ) : null}
    </Modal>
  );
}

function Detail({
  label,
  children,
  wide = false,
}: {
  label: string;
  children: React.ReactNode;
  wide?: boolean;
}) {
  return (
    <div className={wide ? 'sm:col-span-2' : undefined}>
      <dt className="text-ink-soft text-xs font-medium tracking-wide uppercase">{label}</dt>
      <dd className="text-ink mt-0.5 text-sm">{children}</dd>
    </div>
  );
}
