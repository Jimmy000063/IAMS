import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  CheckCheck,
  CircleCheck,
  CircleX,
  Pencil,
  RefreshCw,
  Users,
} from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import { Button } from '@/components/ui/Button';
import { DatePicker } from '@/components/ui/DatePicker';
import { SelectField, TextAreaField, TextField } from '@/components/ui/Field';
import { Alert, EmptyState, ErrorPanel, SkeletonRows } from '@/components/ui/Feedback';
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
  STATUS_STYLES,
  addManualEvent,
  attendanceKeys,
  decideRegularization,
  fetchAnomalies,
  fetchEmployeeAttendance,
  fetchPendingRegularizations,
  fetchTodayOverview,
  overrideDay,
  recompute,
  type AttendanceDay,
  type AttendanceStatus,
  type Regularization,
} from '@/features/attendance/api';
import { AttendanceCalendar } from '@/features/attendance/AttendanceCalendar';
import { employeeKeys, fetchEmployees } from '@/features/employees/api';
import {
  addDays,
  endOfMonth,
  formatClock,
  formatIsoLong,
  formatMinutes,
  startOfMonth,
  todayIso,
  type IsoDate,
} from '@/lib/dates';
import { useAuth } from '@/lib/auth';
import { useToast } from '@/lib/toast';
import { humanise } from '@/lib/utils';

type Tab = 'today' | 'employee' | 'approvals' | 'anomalies';

export function AttendanceAdminPage() {
  const { can } = useAuth();
  const [tab, setTab] = useState<Tab>('today');

  const tabs: { id: Tab; label: string; visible: boolean }[] = [
    { id: 'today', label: 'Today', visible: can('attendance.read_all') },
    { id: 'employee', label: 'By employee', visible: can('attendance.read_all') },
    {
      id: 'approvals',
      label: 'Approvals',
      visible: can('regularization.approve_hr') || can('regularization.approve_as_manager'),
    },
    { id: 'anomalies', label: 'Needs attention', visible: can('attendance.read_all') },
  ];
  const visibleTabs = tabs.filter((entry) => entry.visible);

  return (
    <div>
      <PageHeader
        title="Attendance"
        description="Company-wide attendance, corrections and the approval queue."
      />

      <div
        className="border-line mb-5 flex w-fit gap-1 rounded-lg border p-1"
        role="tablist"
        aria-label="Attendance sections"
      >
        {visibleTabs.map((entry) => (
          <button
            key={entry.id}
            type="button"
            role="tab"
            aria-selected={tab === entry.id}
            onClick={() => setTab(entry.id)}
            className={
              tab === entry.id
                ? 'bg-brand-600 rounded-md px-3 py-1.5 text-sm font-medium text-white'
                : 'text-ink-soft hover:bg-sunken rounded-md px-3 py-1.5 text-sm font-medium transition-colors'
            }
          >
            {entry.label}
          </button>
        ))}
      </div>

      {tab === 'today' && <TodayPanel />}
      {tab === 'employee' && <EmployeePanel />}
      {tab === 'approvals' && <ApprovalsPanel />}
      {tab === 'anomalies' && <AnomaliesPanel />}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Today                                                                      */
/* -------------------------------------------------------------------------- */

function TodayPanel() {
  const query = useQuery({
    queryKey: attendanceKeys.today(),
    queryFn: fetchTodayOverview,
    refetchInterval: 120_000,
  });

  if (query.isPending) return <SkeletonRows rows={4} />;
  if (query.isError) {
    return (
      <ErrorPanel
        error={query.error}
        onRetry={() => void query.refetch()}
        title="Could not load today's attendance"
      />
    );
  }

  const data = query.data;
  const accountedFor = data.active_employees - data.not_yet_computed;

  return (
    <div className="space-y-6">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Tile label="Present" value={data.present} tone="success" />
        <Tile
          label="Absent"
          value={data.absent}
          tone={data.absent > 0 ? 'danger' : 'neutral'}
        />
        <Tile
          label="Incomplete"
          value={data.incomplete}
          tone={data.incomplete > 0 ? 'warning' : 'neutral'}
        />
        <Tile
          label="Not yet computed"
          value={data.not_yet_computed}
          tone="neutral"
          hint="Not the same as absent"
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Breakdown" description={`${data.date}`} />
          <CardBody>
            {Object.keys(data.status_counts).length === 0 ? (
              <EmptyState
                icon={Users}
                title="No attendance computed for today yet"
                description="Rows appear as employees punch in, or when the day is recomputed."
              />
            ) : (
              <ul className="space-y-2">
                {Object.entries(data.status_counts)
                  .sort(([, a], [, b]) => b - a)
                  .map(([status, count]) => {
                    const style = STATUS_STYLES[status as AttendanceStatus];
                    return (
                      <li key={status} className="flex items-center justify-between gap-3">
                        <span className="text-ink-soft flex items-center gap-2 text-sm">
                          <span
                            className={`size-2.5 rounded-full ${style?.dot ?? 'bg-ink-muted'}`}
                            aria-hidden="true"
                          />
                          {style?.label ?? humanise(status)}
                        </span>
                        <span className="numeric text-ink font-medium">{count}</span>
                      </li>
                    );
                  })}
              </ul>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Exceptions" />
          <CardBody>
            <dl className="grid grid-cols-2 gap-4">
              <Figure label="Late arrivals" value={data.late_arrivals} />
              <Figure
                label="Missing punches"
                value={data.missing_punches}
                tone={data.missing_punches > 0 ? 'warning' : 'default'}
              />
              <Figure
                label="Pending corrections"
                value={data.pending_regularizations}
                tone={data.pending_regularizations > 0 ? 'warning' : 'default'}
              />
              <Figure label="Accounted for" value={accountedFor} />
            </dl>

            {data.not_yet_computed > 0 && (
              <Alert variant="info" title="Some employees have no row for today">
                {data.not_yet_computed} of {data.active_employees} active employees. That is
                reported separately from absence — they may be mid-shift, or the day may simply
                not have been computed yet.
              </Alert>
            )}
          </CardBody>
        </Card>
      </div>

      <RecomputePanel />
    </div>
  );
}

function Tile({
  label,
  value,
  tone,
  hint,
}: {
  label: string;
  value: number;
  tone: 'success' | 'danger' | 'warning' | 'neutral';
  hint?: string;
}) {
  const valueTone = {
    success: 'text-success-700',
    danger: 'text-danger-700',
    warning: 'text-warning-700',
    neutral: 'text-ink',
  }[tone];
  return (
    <Card>
      <CardBody className="py-4">
        <p className="text-ink-muted text-xs font-medium tracking-wide uppercase">{label}</p>
        <p className={`numeric mt-1 text-2xl font-semibold ${valueTone}`}>{value}</p>
        {hint && <p className="text-ink-muted mt-0.5 text-[11px]">{hint}</p>}
      </CardBody>
    </Card>
  );
}

function Figure({
  label,
  value,
  tone = 'default',
}: {
  label: string;
  value: number;
  tone?: 'default' | 'warning';
}) {
  return (
    <div>
      <dt className="text-ink-muted text-xs font-medium tracking-wide uppercase">{label}</dt>
      <dd
        className={`numeric mt-0.5 text-lg font-semibold ${
          tone === 'warning' ? 'text-warning-700' : 'text-ink'
        }`}
      >
        {value}
      </dd>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Recompute                                                                  */
/* -------------------------------------------------------------------------- */

function RecomputePanel() {
  const { can } = useAuth();
  const toast = useToast();
  const queryClient = useQueryClient();
  const today = todayIso();

  const [start, setStart] = useState<IsoDate | ''>(addDays(today, -7));
  const [end, setEnd] = useState<IsoDate | ''>(today);
  const [respectLock, setRespectLock] = useState(true);

  const mutation = useMutation({
    mutationFn: recompute,
    onSuccess: async (result) => {
      toast.success(
        'Recomputation complete',
        `${result.days_computed} day(s) rebuilt across ${result.employees} employee(s).` +
          (result.skipped_locked ? ` ${result.skipped_locked} skipped as locked.` : '') +
          (result.preserved_manual_overrides
            ? ` ${result.preserved_manual_overrides} HR override(s) preserved.`
            : ''),
      );
      await queryClient.invalidateQueries({ queryKey: attendanceKeys.all });
    },
    onError: (error) => toast.apiError(error, 'Could not recompute attendance'),
  });

  if (!can('attendance.recalculate')) return null;

  return (
    <Card>
      <CardHeader
        title="Recompute attendance"
        description="Rebuilds days from their raw punches. Safe to run repeatedly — it is idempotent."
      />
      <CardBody className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <DatePicker label="From" value={start} onChange={setStart} clearable={false} />
          <DatePicker label="To" value={end} onChange={setEnd} clearable={false} />
        </div>

        <label className="flex items-start gap-2.5 text-sm">
          <input
            type="checkbox"
            className="border-line-strong accent-brand-600 mt-0.5 size-4 rounded"
            checked={respectLock}
            onChange={(event) => setRespectLock(event.target.checked)}
          />
          <span className="text-ink-soft">
            Honour the recomputation lock. Keep this on — it is what stops a policy change
            silently rewriting old attendance. Waiving it is recorded in the audit log.
          </span>
        </label>

        <Button
          leadingIcon={<RefreshCw className="size-4" aria-hidden="true" />}
          isLoading={mutation.isPending}
          disabled={!start || !end}
          onClick={() =>
            mutation.mutate({
              employee_ids: null,
              start: start as IsoDate,
              end: end as IsoDate,
              respect_lock: respectLock,
            })
          }
        >
          Recompute all employees
        </Button>
      </CardBody>
    </Card>
  );
}

/* -------------------------------------------------------------------------- */
/* By employee                                                                */
/* -------------------------------------------------------------------------- */

function EmployeePanel() {
  const { can } = useAuth();
  const today = todayIso();
  const [employeeId, setEmployeeId] = useState<number | null>(null);
  const [view, setView] = useState(() => {
    const [year, month] = today.split('-');
    return { year: Number(year), month: Number(month) };
  });
  const [editing, setEditing] = useState<AttendanceDay | null>(null);

  const employeesQuery = useQuery({
    queryKey: employeeKeys.list({ purpose: 'attendance-picker' }),
    queryFn: () => fetchEmployees({ page_size: 200 }),
  });

  const start = startOfMonth(view.year, view.month);
  const end = endOfMonth(view.year, view.month);

  const daysQuery = useQuery({
    queryKey: attendanceKeys.employee(employeeId ?? 0, start, end),
    queryFn: () => fetchEmployeeAttendance(employeeId!, start, end),
    enabled: employeeId !== null,
  });

  return (
    <div className="space-y-6">
      <Card>
        <CardBody>
          <div className="max-w-md">
            <SelectField
              label="Employee"
              value={employeeId ? String(employeeId) : ''}
              onChange={(event) =>
                setEmployeeId(event.target.value ? Number(event.target.value) : null)
              }
              options={[
                { value: '', label: 'Choose an employee…' },
                ...(employeesQuery.data?.items ?? []).map((row) => ({
                  value: String(row.id),
                  label: `${row.employee_code} — ${row.full_name}`,
                })),
              ]}
            />
          </div>
        </CardBody>
      </Card>

      {employeeId === null ? (
        <EmptyState
          icon={Users}
          title="Choose an employee"
          description="Their month of attendance will appear as a calendar."
        />
      ) : (
        <Card>
          <CardBody>
            {daysQuery.isError ? (
              <ErrorPanel
                error={daysQuery.error}
                onRetry={() => void daysQuery.refetch()}
                title="Could not load attendance"
              />
            ) : (
              <AttendanceCalendar
                year={view.year}
                month={view.month}
                days={daysQuery.data ?? []}
                isLoading={daysQuery.isFetching}
                onMonthChange={(year, month) => setView({ year, month })}
                renderDayActions={
                  can('attendance.manage')
                    ? (day) => (
                        <Button
                          size="sm"
                          variant="secondary"
                          leadingIcon={<Pencil className="size-3.5" aria-hidden="true" />}
                          onClick={() => setEditing(day)}
                        >
                          Correct this day
                        </Button>
                      )
                    : undefined
                }
              />
            )}
          </CardBody>
        </Card>
      )}

      {editing && employeeId !== null && (
        <CorrectDayDialog
          employeeId={employeeId}
          day={editing}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  );
}

function CorrectDayDialog({
  employeeId,
  day,
  onClose,
}: {
  employeeId: number;
  day: AttendanceDay;
  onClose: () => void;
}) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<'event' | 'override'>('event');

  const [eventType, setEventType] = useState<'in' | 'out'>('in');
  const [clock, setClock] = useState('09:30');
  const [status, setStatus] = useState<AttendanceStatus>(day.status);
  const [fraction, setFraction] = useState(day.payable_day_fraction);
  const [reason, setReason] = useState('');
  const [error, setError] = useState<string | null>(null);

  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: attendanceKeys.all });
  };

  const eventMutation = useMutation({
    mutationFn: addManualEvent,
    onSuccess: async () => {
      toast.success('Punch added', 'The day has been recomputed.');
      await invalidate();
      onClose();
    },
    onError: (error) => toast.apiError(error, 'Could not add the punch'),
  });

  const overrideMutation = useMutation({
    mutationFn: overrideDay,
    onSuccess: async () => {
      toast.success('Day overridden', 'It is now pinned against recomputation.');
      await invalidate();
      onClose();
    },
    onError: (error) => toast.apiError(error, 'Could not override the day'),
  });

  const submit = () => {
    if (!reason.trim()) {
      setError('A reason is required and is recorded in the audit log.');
      return;
    }
    setError(null);
    if (mode === 'event') {
      eventMutation.mutate({
        employee_id: employeeId,
        event_type: eventType,
        // Local wall-clock time with no offset; the server anchors it to the
        // employee's work-location timezone.
        local_datetime: `${day.work_date}T${clock}:00`,
        reason: reason.trim(),
      });
    } else {
      overrideMutation.mutate({
        employee_id: employeeId,
        work_date: day.work_date,
        status,
        payable_day_fraction: fraction,
        reason: reason.trim(),
      });
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={`Correct ${formatIsoLong(day.work_date)}`}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={submit}
            isLoading={eventMutation.isPending || overrideMutation.isPending}
          >
            Apply correction
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

        <div className="border-line flex gap-1 rounded-lg border p-1" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'event'}
            onClick={() => setMode('event')}
            className={
              mode === 'event'
                ? 'bg-brand-600 flex-1 rounded-md px-3 py-1.5 text-sm font-medium text-white'
                : 'text-ink-soft hover:bg-sunken flex-1 rounded-md px-3 py-1.5 text-sm font-medium'
            }
          >
            Add a punch
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'override'}
            onClick={() => setMode('override')}
            className={
              mode === 'override'
                ? 'bg-brand-600 flex-1 rounded-md px-3 py-1.5 text-sm font-medium text-white'
                : 'text-ink-soft hover:bg-sunken flex-1 rounded-md px-3 py-1.5 text-sm font-medium'
            }
          >
            Set the status
          </button>
        </div>

        {mode === 'event' ? (
          <>
            <Alert variant="info" title="Preferred">
              Adding a punch lets the engine recompute the day as it would any other. The
              original punches stay on the record, so the change reads as a correction rather
              than as rewritten history.
            </Alert>
            <div className="grid grid-cols-2 gap-4">
              <SelectField
                label="Punch type"
                value={eventType}
                onChange={(event) => setEventType(event.target.value as 'in' | 'out')}
                options={[
                  { value: 'in', label: 'Punch in' },
                  { value: 'out', label: 'Punch out' },
                ]}
              />
              <TextField
                label="Time"
                type="time"
                value={clock}
                onChange={(event) => setClock(event.target.value)}
                hint="Employee's local time"
              />
            </div>
          </>
        ) : (
          <>
            <Alert variant="warning" title="Use sparingly" icon={AlertTriangle}>
              This pins the day: later recomputations will preserve it rather than recalculating
              from the punches. Prefer adding a punch where you can.
            </Alert>
            <div className="grid grid-cols-2 gap-4">
              <SelectField
                label="Status"
                value={status}
                onChange={(event) => setStatus(event.target.value as AttendanceStatus)}
                options={(Object.keys(STATUS_STYLES) as AttendanceStatus[]).map((key) => ({
                  value: key,
                  label: STATUS_STYLES[key].label,
                }))}
              />
              <SelectField
                label="Counts as"
                value={fraction}
                onChange={(event) => setFraction(event.target.value)}
                options={[
                  { value: '1.00', label: 'Full day (1.00)' },
                  { value: '0.50', label: 'Half day (0.50)' },
                  { value: '0.00', label: 'No day (0.00)' },
                ]}
              />
            </div>
          </>
        )}

        <TextAreaField
          label="Reason"
          rows={3}
          required
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          hint="Recorded in the audit log against your account."
        />
      </div>
    </Modal>
  );
}

/* -------------------------------------------------------------------------- */
/* Approvals                                                                  */
/* -------------------------------------------------------------------------- */

function ApprovalsPanel() {
  const query = useQuery({
    queryKey: attendanceKeys.pendingRegularizations(),
    queryFn: fetchPendingRegularizations,
  });

  if (query.isPending) return <SkeletonRows rows={3} />;
  if (query.isError) {
    return (
      <ErrorPanel
        error={query.error}
        onRetry={() => void query.refetch()}
        title="Could not load the approval queue"
      />
    );
  }

  if (query.data.length === 0) {
    return (
      <EmptyState
        icon={CheckCheck}
        title="Nothing awaiting your decision"
        description="Correction requests you can act on will appear here."
      />
    );
  }

  return (
    <Card>
      <CardHeader
        title={`${query.data.length} request${query.data.length === 1 ? '' : 's'} awaiting you`}
        description="Only requests you can actually decide are shown."
      />
      <CardBody className="space-y-3">
        {query.data.map((request) => (
          <RequestCard key={request.id} request={request} />
        ))}
      </CardBody>
    </Card>
  );
}

function RequestCard({ request }: { request: Regularization }) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [comment, setComment] = useState('');

  const mutation = useMutation({
    mutationFn: (approve: boolean) =>
      decideRegularization(request.id, { approve, comment: comment.trim() || null }),
    onSuccess: async (result) => {
      toast.success(
        result.status === 'approved'
          ? 'Approved'
          : result.status === 'pending_hr'
            ? 'Sent to HR'
            : 'Rejected',
        result.status === 'pending_hr'
          ? 'Your approval has been recorded. HR gives the final decision.'
          : undefined,
      );
      await queryClient.invalidateQueries({ queryKey: attendanceKeys.all });
    },
    onError: (error) => toast.apiError(error, 'Could not record the decision'),
  });

  return (
    <div className="border-line rounded-lg border p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-ink font-medium">
            <Link
              to={`/employees/${request.employee_id}`}
              className="text-brand-600 underline-offset-4 hover:underline"
            >
              {request.employee_code}
            </Link>{' '}
            {request.employee_name}
          </p>
          <p className="text-ink-soft mt-0.5 text-sm">
            {formatIsoLong(request.work_date)}
            {request.status_before && (
              <span className="text-ink-muted">
                {' '}
                · currently {humanise(request.status_before)}
              </span>
            )}
          </p>
        </div>
        <Badge tone={request.current_stage === 'hr_admin' ? 'brand' : 'warning'}>
          {REGULARIZATION_STATUS_LABELS[request.status]}
        </Badge>
      </div>

      <dl className="mt-3 grid grid-cols-2 gap-3 text-sm sm:grid-cols-3">
        <div>
          <dt className="text-ink-muted text-xs uppercase">Requested in</dt>
          <dd className="numeric text-ink">{formatClock(request.requested_in_at)}</dd>
        </div>
        <div>
          <dt className="text-ink-muted text-xs uppercase">Requested out</dt>
          <dd className="numeric text-ink">{formatClock(request.requested_out_at)}</dd>
        </div>
        <div>
          <dt className="text-ink-muted text-xs uppercase">Raised</dt>
          <dd className="text-ink">{formatClock(request.created_at)}</dd>
        </div>
      </dl>

      <div className="bg-sunken mt-3 rounded-md p-3">
        <p className="text-ink-muted text-xs font-semibold uppercase">Employee&apos;s reason</p>
        <p className="text-ink mt-1 text-sm">{request.reason}</p>
      </div>

      {request.manager_decision && (
        <p className="text-ink-soft mt-2 text-xs">
          Manager: {request.manager_decision}
          {request.manager_comment ? ` — ${request.manager_comment}` : ''}
        </p>
      )}

      {request.escalated_from_missing_manager && (
        <p className="text-warning-700 mt-2 text-xs">
          The manager stage was skipped — this employee has no active reporting manager.
        </p>
      )}

      <div className="mt-3 space-y-2">
        <TextField
          label="Comment"
          value={comment}
          onChange={(event) => setComment(event.target.value)}
          hint="Visible to the employee."
        />
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            isLoading={mutation.isPending}
            leadingIcon={<CircleCheck className="size-4" aria-hidden="true" />}
            onClick={() => mutation.mutate(true)}
          >
            {request.current_stage === 'hr_admin' ? 'Approve and apply' : 'Approve'}
          </Button>
          <Button
            size="sm"
            variant="danger"
            isLoading={mutation.isPending}
            leadingIcon={<CircleX className="size-4" aria-hidden="true" />}
            onClick={() => mutation.mutate(false)}
          >
            Reject
          </Button>
        </div>
        {request.current_stage === 'reporting_manager' && (
          <p className="text-ink-muted text-xs">
            Your approval advances this to HR; it does not apply the change.
          </p>
        )}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Anomalies                                                                  */
/* -------------------------------------------------------------------------- */

function AnomaliesPanel() {
  const today = todayIso();
  const [start, setStart] = useState<IsoDate | ''>(addDays(today, -30));
  const [end, setEnd] = useState<IsoDate | ''>(today);

  const query = useQuery({
    queryKey: attendanceKeys.anomalies(start || today, end || today),
    queryFn: () => fetchAnomalies(start as IsoDate, end as IsoDate),
    enabled: Boolean(start && end),
  });

  return (
    <div className="space-y-6">
      <Card>
        <CardBody>
          <div className="grid gap-4 sm:grid-cols-2 lg:max-w-xl">
            <DatePicker label="From" value={start} onChange={setStart} clearable={false} />
            <DatePicker label="To" value={end} onChange={setEnd} clearable={false} />
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Days needing attention"
          description="Unpaired punches and missing punch-outs. These need a human decision — left invisible, they become payroll disputes."
        />
        <CardBody>
          {query.isPending && <SkeletonRows rows={4} />}
          {query.isError && (
            <ErrorPanel error={query.error} onRetry={() => void query.refetch()} />
          )}
          {query.isSuccess &&
            (query.data.length === 0 ? (
              <EmptyState
                icon={CircleCheck}
                title="Nothing needs attention"
                description="No unpaired punches in this period."
              />
            ) : (
              <TableShell caption="Days with attendance anomalies">
                <thead>
                  <tr>
                    <Th>Date</Th>
                    <Th>Employee</Th>
                    <Th>Status</Th>
                    <Th className="hidden sm:table-cell">Worked</Th>
                    <Th>Problem</Th>
                  </tr>
                </thead>
                <tbody>
                  {query.data.map((day) => (
                    <tr key={day.id}>
                      <Td className="whitespace-nowrap">{formatIsoLong(day.work_date)}</Td>
                      <Td>
                        <Link
                          to={`/employees/${day.employee_id}`}
                          className="text-brand-600 underline-offset-4 hover:underline"
                        >
                          #{day.employee_id}
                        </Link>
                      </Td>
                      <Td>
                        <Badge tone={day.has_missing_punch ? 'warning' : 'neutral'}>
                          {STATUS_STYLES[day.status]?.label ?? day.status}
                        </Badge>
                      </Td>
                      <Td className="numeric hidden sm:table-cell">
                        {formatMinutes(day.net_minutes)}
                      </Td>
                      <Td className="text-ink-soft text-xs">
                        {day.anomalies?.join(' ') ?? 'Missing punch'}
                      </Td>
                    </tr>
                  ))}
                </tbody>
              </TableShell>
            ))}
        </CardBody>
      </Card>
    </div>
  );
}
