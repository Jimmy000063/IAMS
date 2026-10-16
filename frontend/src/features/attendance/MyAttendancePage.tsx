import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CalendarClock, FilePlus2, Info } from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/Button';
import { DatePicker } from '@/components/ui/DatePicker';
import { TextAreaField, TextField } from '@/components/ui/Field';
import { Alert, ErrorPanel, SkeletonRows } from '@/components/ui/Feedback';
import { Modal } from '@/components/ui/Modal';
import {
  Badge,
  Card,
  CardBody,
  CardHeader,
  PageHeader,
  TableShell,
  Td,
  Th,
} from '@/components/ui/Surface';
import {
  REGULARIZATION_STATUS_LABELS,
  attendanceKeys,
  cancelRegularization,
  fetchMyAttendance,
  fetchMyRegularizations,
  fetchMySummary,
  requestRegularization,
} from '@/features/attendance/api';
import { AttendanceCalendar } from '@/features/attendance/AttendanceCalendar';
import { PunchWidget } from '@/features/attendance/PunchWidget';
import {
  endOfMonth,
  formatClock,
  formatIsoLong,
  formatMinutes,
  startOfMonth,
  todayIso,
  type IsoDate,
} from '@/lib/dates';
import { useToast } from '@/lib/toast';

export function MyAttendancePage() {
  const today = todayIso();
  const [view, setView] = useState(() => {
    const [year, month] = today.split('-');
    return { year: Number(year), month: Number(month) };
  });
  const [correctionDate, setCorrectionDate] = useState<IsoDate | null>(null);

  const start = startOfMonth(view.year, view.month);
  const end = endOfMonth(view.year, view.month);

  const daysQuery = useQuery({
    queryKey: attendanceKeys.mine(start, end),
    queryFn: () => fetchMyAttendance(start, end),
    retry: false,
  });

  const summaryQuery = useQuery({
    queryKey: attendanceKeys.mySummary(start, end),
    queryFn: () => fetchMySummary(start, end),
    retry: false,
  });

  const regularizationsQuery = useQuery({
    queryKey: attendanceKeys.myRegularizations(),
    queryFn: fetchMyRegularizations,
    retry: false,
  });

  return (
    <div>
      <PageHeader
        title="My attendance"
        description="Punch in and out, review your month, and request corrections."
      />

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6">
          <PunchWidget />

          <Card>
            <CardHeader title="This month" />
            <CardBody>
              {summaryQuery.isPending && <SkeletonRows rows={3} />}
              {summaryQuery.isError && (
                <ErrorPanel error={summaryQuery.error} title="Could not load your summary" />
              )}
              {summaryQuery.isSuccess && (
                <dl className="grid grid-cols-2 gap-4">
                  <Figure
                    label="Payable days"
                    value={Number(summaryQuery.data.payable_days).toFixed(2)}
                    hint="What payroll will use"
                  />
                  <Figure
                    label="Worked"
                    value={formatMinutes(summaryQuery.data.worked_minutes)}
                  />
                  <Figure label="Late days" value={String(summaryQuery.data.late_days)} />
                  <Figure
                    label="Missing punches"
                    value={String(summaryQuery.data.missing_punch_days)}
                    tone={summaryQuery.data.missing_punch_days > 0 ? 'warning' : 'default'}
                  />
                  {summaryQuery.data.overtime_minutes > 0 && (
                    <Figure
                      label="Overtime"
                      value={formatMinutes(summaryQuery.data.overtime_minutes)}
                      tone="brand"
                    />
                  )}
                  <Figure
                    label="Days recorded"
                    value={String(summaryQuery.data.days_recorded)}
                  />
                </dl>
              )}
            </CardBody>
          </Card>

          {summaryQuery.data && summaryQuery.data.days_with_anomalies > 0 && (
            <Alert variant="warning" title="Some days need attention" icon={Info}>
              {summaryQuery.data.days_with_anomalies} day
              {summaryQuery.data.days_with_anomalies === 1 ? ' has' : 's have'} an unpaired
              punch. Select the day in the calendar and request a correction.
            </Alert>
          )}
        </div>

        <div className="space-y-6 lg:col-span-2">
          <Card>
            <CardBody>
              {daysQuery.isError ? (
                <ErrorPanel
                  error={daysQuery.error}
                  onRetry={() => void daysQuery.refetch()}
                  title="Could not load your attendance"
                />
              ) : (
                <AttendanceCalendar
                  year={view.year}
                  month={view.month}
                  days={daysQuery.data ?? []}
                  isLoading={daysQuery.isFetching}
                  onMonthChange={(year, month) => setView({ year, month })}
                  onRequestCorrection={(iso) => setCorrectionDate(iso)}
                />
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title="Correction requests"
              description="Reviewed by your manager, then decided by HR."
              action={
                <Button
                  size="sm"
                  variant="secondary"
                  leadingIcon={<FilePlus2 className="size-4" aria-hidden="true" />}
                  onClick={() => setCorrectionDate(todayIso())}
                >
                  New request
                </Button>
              }
            />
            <CardBody>
              {regularizationsQuery.isPending && <SkeletonRows rows={2} />}
              {regularizationsQuery.isError && (
                <ErrorPanel
                  error={regularizationsQuery.error}
                  title="Could not load your requests"
                />
              )}
              {regularizationsQuery.isSuccess &&
                (regularizationsQuery.data.length === 0 ? (
                  <p className="text-ink-muted text-sm">
                    You have not raised any correction requests.
                  </p>
                ) : (
                  <RegularizationList rows={regularizationsQuery.data} />
                ))}
            </CardBody>
          </Card>
        </div>
      </div>

      {correctionDate && (
        <CorrectionDialog
          initialDate={correctionDate}
          onClose={() => setCorrectionDate(null)}
        />
      )}
    </div>
  );
}

function Figure({
  label,
  value,
  hint,
  tone = 'default',
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: 'default' | 'warning' | 'brand';
}) {
  const valueTone =
    tone === 'warning' ? 'text-warning-700' : tone === 'brand' ? 'text-brand-700' : 'text-ink';
  return (
    <div>
      <dt className="text-ink-muted text-xs font-medium tracking-wide uppercase">{label}</dt>
      <dd className={`numeric mt-0.5 text-xl font-semibold ${valueTone}`}>{value}</dd>
      {hint && <p className="text-ink-muted text-[11px]">{hint}</p>}
    </div>
  );
}

function RegularizationList({
  rows,
}: {
  rows: Awaited<ReturnType<typeof fetchMyRegularizations>>;
}) {
  const toast = useToast();
  const queryClient = useQueryClient();

  const cancelMutation = useMutation({
    mutationFn: (id: number) => cancelRegularization(id),
    onSuccess: async () => {
      toast.success('Request withdrawn');
      await queryClient.invalidateQueries({ queryKey: attendanceKeys.all });
    },
    onError: (error) => toast.apiError(error, 'Could not withdraw the request'),
  });

  const tone = (status: string) =>
    status === 'approved'
      ? 'success'
      : status === 'rejected'
        ? 'danger'
        : status === 'cancelled'
          ? 'neutral'
          : 'warning';

  return (
    <TableShell caption="My correction requests">
      <thead>
        <tr>
          <Th>Date</Th>
          <Th className="hidden sm:table-cell">Requested</Th>
          <Th>Status</Th>
          <Th className="hidden md:table-cell">Decisions</Th>
          <Th className="text-right">
            <span className="sr-only">Actions</span>
          </Th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.id}>
            <Td className="whitespace-nowrap">{formatIsoLong(row.work_date)}</Td>
            <Td className="numeric hidden text-xs sm:table-cell">
              {row.requested_in_at ? `in ${formatClock(row.requested_in_at)}` : ''}
              {row.requested_in_at && row.requested_out_at ? ' · ' : ''}
              {row.requested_out_at ? `out ${formatClock(row.requested_out_at)}` : ''}
            </Td>
            <Td>
              <Badge tone={tone(row.status)}>{REGULARIZATION_STATUS_LABELS[row.status]}</Badge>
              {row.escalated_from_missing_manager && (
                <p className="text-ink-muted mt-1 text-[11px]">
                  Sent straight to HR — no manager assigned
                </p>
              )}
            </Td>
            <Td className="text-ink-soft hidden text-xs md:table-cell">
              {row.manager_decision && (
                <p>
                  Manager: {row.manager_decision}
                  {row.manager_comment ? ` — ${row.manager_comment}` : ''}
                </p>
              )}
              {row.hr_decision && (
                <p>
                  HR: {row.hr_decision}
                  {row.hr_comment ? ` — ${row.hr_comment}` : ''}
                </p>
              )}
              {!row.manager_decision && !row.hr_decision && <span>—</span>}
            </Td>
            <Td className="text-right">
              {(row.status === 'pending_manager' || row.status === 'pending_hr') && (
                <Button
                  variant="ghost"
                  size="sm"
                  isLoading={cancelMutation.isPending}
                  onClick={() => cancelMutation.mutate(row.id)}
                >
                  Withdraw
                </Button>
              )}
            </Td>
          </tr>
        ))}
      </tbody>
    </TableShell>
  );
}

function CorrectionDialog({
  initialDate,
  onClose,
}: {
  initialDate: IsoDate;
  onClose: () => void;
}) {
  const toast = useToast();
  const queryClient = useQueryClient();

  const [workDate, setWorkDate] = useState<IsoDate | ''>(initialDate);
  const [inClock, setInClock] = useState('');
  const [outClock, setOutClock] = useState('');
  const [reason, setReason] = useState('');
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: requestRegularization,
    onSuccess: async () => {
      toast.success('Request submitted', 'Your manager will review it.');
      await queryClient.invalidateQueries({ queryKey: attendanceKeys.all });
      onClose();
    },
    onError: (error) => toast.apiError(error, 'Could not submit the request'),
  });

  const submit = () => {
    if (!workDate) {
      setError('Choose the date you want corrected.');
      return;
    }
    if (!inClock && !outClock) {
      setError('Enter a corrected punch-in time, a punch-out time, or both.');
      return;
    }
    if (!reason.trim()) {
      setError('Explain why the correction is needed.');
      return;
    }
    setError(null);
    mutation.mutate({
      work_date: workDate,
      // Local wall-clock time with no offset: the server applies the
      // employee's work-location timezone.
      local_in: inClock ? `${workDate}T${inClock}:00` : null,
      local_out: outClock ? `${workDate}T${outClock}:00` : null,
      reason: reason.trim(),
    });
  };

  return (
    <Modal
      open
      onClose={onClose}
      title="Request an attendance correction"
      description="Goes to your reporting manager, then to HR for the final decision."
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={mutation.isPending}>
            Cancel
          </Button>
          <Button onClick={submit} isLoading={mutation.isPending}>
            Submit request
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {error && (
          <Alert variant="danger" title="Check this form">
            {error}
          </Alert>
        )}

        <DatePicker
          label="Date to correct"
          value={workDate}
          onChange={setWorkDate}
          max={todayIso()}
          required
          clearable={false}
          hint="Within the correction window configured by HR."
        />

        <div className="grid grid-cols-2 gap-4">
          <TextField
            label="Punch in"
            type="time"
            value={inClock}
            onChange={(event) => setInClock(event.target.value)}
            hint="Local time"
          />
          <TextField
            label="Punch out"
            type="time"
            value={outClock}
            onChange={(event) => setOutClock(event.target.value)}
            hint="Local time"
          />
        </div>

        <TextAreaField
          label="Reason"
          rows={3}
          required
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          hint="Your manager and HR will see this."
        />

        <Alert variant="info" title="What approval does" icon={CalendarClock}>
          An approved correction adds punch records; it does not quietly edit your totals. Your
          original punches stay on the record, so the change is visible as a correction.
        </Alert>
      </div>
    </Modal>
  );
}
