/**
 * Comp-off administration: verifying earned credits, and the approval queue.
 *
 * Verification is the step the brief requires - comp-off must come from
 * *verified* qualifying work - so it is the first thing on the page.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CircleCheck, RefreshCw } from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/Button';
import { DatePicker } from '@/components/ui/DatePicker';
import { TextAreaField } from '@/components/ui/Field';
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
  compoffKeys,
  decideCompoff,
  decideCredit,
  fetchCreditsPendingVerification,
  fetchPendingCompoffApprovals,
  reverseCredit,
  runCreditExpiry,
  runCreditGrant,
  type CompoffApplicationSummary,
  type CompoffCredit,
} from '@/features/compoff/api';
import { formatDays } from '@/features/leave/api';
import { useAuth } from '@/lib/auth';
import { addDays, formatIsoLong, todayIso, type IsoDate } from '@/lib/dates';
import { useToast } from '@/lib/toast';

const STATUS_TONES: Record<string, BadgeTone> = {
  pending_manager: 'warning',
  pending_hr: 'warning',
  approved: 'success',
  rejected: 'danger',
};

export function CompoffAdminPanel() {
  const { can } = useAuth();
  const toast = useToast();
  const queryClient = useQueryClient();

  const [from, setFrom] = useState<IsoDate>(addDays(todayIso(), -14));
  const [to, setTo] = useState<IsoDate>(todayIso());
  const [reversing, setReversing] = useState<CompoffCredit | null>(null);
  const [reason, setReason] = useState('');
  const [deciding, setDeciding] = useState<{
    application: CompoffApplicationSummary;
    approve: boolean;
  } | null>(null);
  const [comment, setComment] = useState('');

  const pendingCredits = useQuery({
    queryKey: compoffKeys.pendingVerification(),
    queryFn: fetchCreditsPendingVerification,
    enabled: can('compoff.verify'),
  });

  const pendingApprovals = useQuery({
    queryKey: compoffKeys.pending(),
    queryFn: fetchPendingCompoffApprovals,
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: compoffKeys.all });
  };

  const verify = useMutation({
    mutationFn: ({ creditId, approve }: { creditId: number; approve: boolean }) =>
      decideCredit(creditId, approve),
    onSuccess: (credit) => {
      invalidate();
      toast.success(
        credit.status === 'available'
          ? 'Verified. The credit is now usable.'
          : 'Rejected. Nothing was credited.',
      );
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  const reverse = useMutation({
    mutationFn: () => reverseCredit(reversing!.id, reason),
    onSuccess: () => {
      setReversing(null);
      setReason('');
      invalidate();
      toast.success('Reversed. The credit is marked, not deleted.');
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  const grant = useMutation({
    mutationFn: () => runCreditGrant({ start: from, end: to }),
    onSuccess: (result) => {
      invalidate();
      if (result.credits_created === 0 && result.already_credited > 0) {
        toast.show({
          variant: 'info',
          title: 'Nothing new to credit.',
          description: `${result.already_credited} day(s) were already credited, so nothing was credited twice.`,
        });
      } else if (result.credits_created === 0) {
        toast.show({
          variant: 'warning',
          title: 'No qualifying work found.',
          description: `${result.days_examined} day(s) examined; ${result.not_qualifying} fell below the minute thresholds.`,
        });
      } else {
        toast.success(
          `${result.credits_created} credit(s) created, worth ${formatDays(result.days_credited)} day(s).`,
        );
      }
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  const expire = useMutation({
    mutationFn: () => runCreditExpiry(),
    onSuccess: (result) => {
      invalidate();
      toast.success(
        result.credits_expired === 0
          ? 'Nothing was due to expire.'
          : `${result.credits_expired} credit(s) expired, ${formatDays(result.days_lost)} day(s) lost.`,
      );
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  const decide = useMutation({
    mutationFn: () =>
      decideCompoff(deciding!.application.id, deciding!.approve, comment || null),
    onSuccess: (application) => {
      const approved = deciding?.approve;
      setDeciding(null);
      setComment('');
      invalidate();
      toast.success(
        approved
          ? application.status === 'approved'
            ? 'Approved. Attendance for those days has been recalculated.'
            : 'Approved at your stage; it now awaits HR.'
          : 'Rejected. The held credits have gone back to the employee.',
      );
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  return (
    <div className="space-y-5">
      <Alert variant="info" title="Why credits wait for verification">
        The brief requires comp-off to come from <em>verified</em> qualifying work. A credit
        created from attendance is not usable until someone confirms it, which is why an
        employee's balance shows "awaiting verification" separately from what they can take.
      </Alert>

      {can('compoff.verify') && (
        <Card>
          <CardHeader
            title="Create credits from attendance"
            description="Reads attendance for the range and credits qualifying work. Safe to run repeatedly: a day earns credit once."
          />
          <CardBody className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <DatePicker label="From" value={from} onChange={setFrom} />
              <DatePicker label="To" value={to} onChange={setTo} min={from} />
            </div>
            <div className="flex flex-wrap gap-2">
              <Button onClick={() => grant.mutate()} disabled={grant.isPending}>
                <RefreshCw className="size-4" aria-hidden="true" />
                {grant.isPending ? 'Running…' : 'Create credits'}
              </Button>
              <Button
                variant="secondary"
                onClick={() => expire.mutate()}
                disabled={expire.isPending}
              >
                {expire.isPending ? 'Running…' : 'Expire overdue credits'}
              </Button>
            </div>
            <p className="text-ink-soft text-sm">
              Both are also available from the command line as{' '}
              <code className="bg-sunken rounded px-1 py-0.5 text-xs">iams grant-compoff</code>{' '}
              and{' '}
              <code className="bg-sunken rounded px-1 py-0.5 text-xs">iams expire-compoff</code>
              , for a scheduled job.
            </p>
          </CardBody>
        </Card>
      )}

      {can('compoff.verify') && (
        <Card>
          <CardHeader
            title={`Credits awaiting verification (${pendingCredits.data?.length ?? 0})`}
            description="Each cites the attendance day that proves the work."
          />
          <CardBody>
            {pendingCredits.isPending ? (
              <SkeletonRows rows={3} />
            ) : pendingCredits.isError ? (
              <ErrorPanel
                error={pendingCredits.error}
                onRetry={() => void pendingCredits.refetch()}
              />
            ) : (pendingCredits.data ?? []).length === 0 ? (
              <EmptyState
                icon={CircleCheck}
                title="Nothing awaiting verification"
                description="Credits appear here when the grant job finds qualifying work."
              />
            ) : (
              <TableShell caption="Comp-off credits awaiting verification">
                <thead>
                  <tr>
                    <Th>Employee</Th>
                    <Th>Earned on</Th>
                    <Th>Worked</Th>
                    <Th>Worth</Th>
                    <Th>Expires</Th>
                    <Th className="text-right">Decision</Th>
                  </tr>
                </thead>
                <tbody>
                  {(pendingCredits.data ?? []).map((credit) => (
                    <tr key={credit.id}>
                      <Td>#{credit.employee_id}</Td>
                      <Td className="whitespace-nowrap">
                        {formatIsoLong(credit.earned_date)}
                        <p className="text-ink-muted text-xs">
                          {credit.day_type.replace('_', ' ')}
                        </p>
                      </Td>
                      <Td>
                        {Math.floor(credit.worked_minutes / 60)}h {credit.worked_minutes % 60}m
                      </Td>
                      <Td className="tabular-nums">{formatDays(credit.credit_days)}</Td>
                      <Td className="whitespace-nowrap">
                        {credit.expiry_date ? formatIsoLong(credit.expiry_date) : 'never'}
                      </Td>
                      <Td className="text-right">
                        <div className="flex justify-end gap-2">
                          <Button
                            size="sm"
                            onClick={() =>
                              verify.mutate({ creditId: credit.id, approve: true })
                            }
                            disabled={verify.isPending}
                          >
                            Verify
                          </Button>
                          <Button
                            variant="danger"
                            size="sm"
                            onClick={() =>
                              verify.mutate({ creditId: credit.id, approve: false })
                            }
                            disabled={verify.isPending}
                          >
                            Reject
                          </Button>
                          {can('compoff.reverse') && (
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => {
                                setReason('');
                                setReversing(credit);
                              }}
                            >
                              Reverse
                            </Button>
                          )}
                        </div>
                      </Td>
                    </tr>
                  ))}
                </tbody>
              </TableShell>
            )}
          </CardBody>
        </Card>
      )}

      <Card>
        <CardHeader
          title={`Comp-off requests awaiting a decision (${pendingApprovals.data?.length ?? 0})`}
          description="Rejecting a request returns the credits it was holding."
        />
        <CardBody>
          {pendingApprovals.isPending ? (
            <SkeletonRows rows={3} />
          ) : pendingApprovals.isError ? (
            <ErrorPanel
              error={pendingApprovals.error}
              onRetry={() => void pendingApprovals.refetch()}
            />
          ) : (pendingApprovals.data ?? []).length === 0 ? (
            <EmptyState icon={CircleCheck} title="Nothing waiting" />
          ) : (
            <TableShell caption="Comp-off requests awaiting a decision">
              <thead>
                <tr>
                  <Th>Employee</Th>
                  <Th>Dates</Th>
                  <Th>Days</Th>
                  <Th>Status</Th>
                  <Th className="text-right">Decision</Th>
                </tr>
              </thead>
              <tbody>
                {(pendingApprovals.data ?? []).map((application) => (
                  <tr key={application.id}>
                    <Td>
                      <p className="font-medium">{application.employee_code}</p>
                      <p className="text-ink-soft text-xs">{application.employee_name}</p>
                    </Td>
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
                    <Td className="text-right">
                      <div className="flex justify-end gap-2">
                        <Button
                          size="sm"
                          onClick={() => {
                            setComment('');
                            setDeciding({ application, approve: true });
                          }}
                        >
                          Approve
                        </Button>
                        <Button
                          variant="danger"
                          size="sm"
                          onClick={() => {
                            setComment('');
                            setDeciding({ application, approve: false });
                          }}
                        >
                          Reject
                        </Button>
                      </div>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </TableShell>
          )}
        </CardBody>
      </Card>

      <Modal
        open={reversing !== null}
        onClose={() => setReversing(null)}
        title="Reverse this credit"
        description="The credit is marked reversed, never deleted - the evidence that it was granted has to stay. A credit already spent cannot be reversed."
        footer={
          <>
            <Button variant="secondary" onClick={() => setReversing(null)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              onClick={() => reverse.mutate()}
              disabled={reverse.isPending || reason.trim() === ''}
            >
              {reverse.isPending ? 'Reversing…' : 'Reverse credit'}
            </Button>
          </>
        }
      >
        <TextAreaField
          label="Reason"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          rows={3}
          required
          hint="Required, and kept on the record beside the credit."
        />
      </Modal>

      <Modal
        open={deciding !== null}
        onClose={() => setDeciding(null)}
        title={deciding?.approve ? 'Approve this request' : 'Reject this request'}
        description={
          deciding?.approve
            ? 'If this is the final stage, attendance for those days is recalculated as comp-off.'
            : 'The credits it was holding go back to the employee.'
        }
        footer={
          <>
            <Button variant="secondary" onClick={() => setDeciding(null)}>
              Cancel
            </Button>
            <Button
              variant={deciding?.approve ? 'primary' : 'danger'}
              onClick={() => decide.mutate()}
              disabled={decide.isPending}
            >
              {decide.isPending ? 'Recording…' : deciding?.approve ? 'Approve' : 'Reject'}
            </Button>
          </>
        }
      >
        <TextAreaField
          label="Comment"
          value={comment}
          onChange={(event) => setComment(event.target.value)}
          rows={3}
          hint="Recorded against this stage and never overwritten."
        />
      </Modal>
    </div>
  );
}
