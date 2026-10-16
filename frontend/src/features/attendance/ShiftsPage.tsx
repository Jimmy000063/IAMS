import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CalendarDays, Clock, Moon, Plus, Sun } from 'lucide-react';
import { useMemo, useState } from 'react';

import { Button } from '@/components/ui/Button';
import { DatePicker } from '@/components/ui/DatePicker';
import { CheckboxField, SelectField, TextAreaField, TextField } from '@/components/ui/Field';
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
  addException,
  createAssignment,
  createCalendar,
  createPattern,
  createShift,
  fetchAssignments,
  fetchCalendars,
  fetchDayTypes,
  fetchPatterns,
  fetchShifts,
  shiftKeys,
  type Shift,
} from '@/features/attendance/api';
import {
  employeeKeys,
  fetchEmployees,
  fetchWorkLocations,
  orgKeys,
} from '@/features/employees/api';
import {
  MONTH_NAMES,
  WEEKDAY_LABELS,
  addMonths,
  endOfMonth,
  formatIsoLong,
  monthGrid,
  startOfMonth,
  todayIso,
  trimGrid,
  trimSeconds,
  type IsoDate,
} from '@/lib/dates';
import { useAuth } from '@/lib/auth';
import { useToast } from '@/lib/toast';
import { cn } from '@/lib/utils';

type Tab = 'shifts' | 'calendars' | 'assignments';

const WEEKDAY_KEYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] as const;

export function ShiftsPage() {
  const [tab, setTab] = useState<Tab>('shifts');

  return (
    <div>
      <PageHeader
        title="Shifts & calendars"
        description="Working-time patterns, weekly offs and date exceptions. The calendar decides which days count as working days for attendance and leave alike."
      />

      <div
        className="border-line mb-5 flex w-fit gap-1 rounded-lg border p-1"
        role="tablist"
        aria-label="Shift sections"
      >
        {(
          [
            { id: 'shifts', label: 'Shifts', icon: Clock },
            { id: 'calendars', label: 'Work calendars', icon: CalendarDays },
            { id: 'assignments', label: 'Assignments', icon: Sun },
          ] as const
        ).map((entry) => {
          const Icon = entry.icon;
          return (
            <button
              key={entry.id}
              type="button"
              role="tab"
              aria-selected={tab === entry.id}
              onClick={() => setTab(entry.id)}
              className={
                tab === entry.id
                  ? 'bg-brand-600 inline-flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium text-white'
                  : 'text-ink-soft hover:bg-sunken inline-flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium transition-colors'
              }
            >
              <Icon className="size-4" aria-hidden="true" />
              {entry.label}
            </button>
          );
        })}
      </div>

      {tab === 'shifts' && <ShiftsPanel />}
      {tab === 'calendars' && <CalendarsPanel />}
      {tab === 'assignments' && <AssignmentsPanel />}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Shifts                                                                     */
/* -------------------------------------------------------------------------- */

function ShiftsPanel() {
  const { can } = useAuth();
  const [creating, setCreating] = useState(false);

  const query = useQuery({ queryKey: shiftKeys.list(), queryFn: () => fetchShifts() });

  return (
    <div className="space-y-6">
      {can('shift.manage') && (
        <div className="flex justify-end">
          <Button
            onClick={() => setCreating(true)}
            leadingIcon={<Plus className="size-4" aria-hidden="true" />}
          >
            Add shift
          </Button>
        </div>
      )}

      {query.isPending && <SkeletonRows rows={3} />}
      {query.isError && <ErrorPanel error={query.error} onRetry={() => void query.refetch()} />}

      {query.isSuccess && query.data.length === 0 && (
        <EmptyState
          icon={Clock}
          title="No shifts defined"
          description="Attendance falls back to company default timings until a shift is assigned."
        />
      )}

      {query.isSuccess && query.data.length > 0 && (
        <div className="grid gap-4 md:grid-cols-2">
          {query.data.map((shift) => (
            <ShiftCard key={shift.id} shift={shift} />
          ))}
        </div>
      )}

      {creating && <ShiftDialog onClose={() => setCreating(false)} />}
    </div>
  );
}

/**
 * A shift rendered as a 24-hour timeline bar.
 *
 * A night shift's two segments either side of midnight make the overnight
 * nature immediately obvious, which "22:00 – 06:00" alone does not.
 */
function ShiftCard({ shift }: { shift: Shift }) {
  const startMinutes = toMinutes(shift.start_time);
  const endMinutes = toMinutes(shift.end_time);

  const segments = shift.crosses_midnight
    ? [
        { left: (startMinutes / 1440) * 100, width: ((1440 - startMinutes) / 1440) * 100 },
        { left: 0, width: (endMinutes / 1440) * 100 },
      ]
    : [
        {
          left: (startMinutes / 1440) * 100,
          width: ((endMinutes - startMinutes) / 1440) * 100,
        },
      ];

  return (
    <Card>
      <CardBody className="space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <p className="text-ink font-semibold">
              {shift.name}{' '}
              <span className="text-ink-muted font-mono text-xs">{shift.code}</span>
            </p>
            <p className="numeric text-ink-soft mt-0.5 text-sm">
              {trimSeconds(shift.start_time)} – {trimSeconds(shift.end_time)}
            </p>
          </div>
          <div className="flex flex-col items-end gap-1">
            {shift.crosses_midnight && (
              <Badge tone="brand">
                <Moon className="size-3" aria-hidden="true" />
                Overnight
              </Badge>
            )}
            {!shift.is_active && <Badge tone="neutral">Inactive</Badge>}
          </div>
        </div>

        <div>
          <div className="bg-sunken relative h-7 overflow-hidden rounded-md">
            {segments.map((segment, index) => (
              <div
                key={index}
                className="bg-brand-500/80 absolute inset-y-0"
                style={{ left: `${segment.left}%`, width: `${segment.width}%` }}
                aria-hidden="true"
              />
            ))}
            {/* Midnight marker, so an overnight shift's wrap is readable. */}
            <div className="bg-line-strong absolute inset-y-0 left-0 w-px" aria-hidden="true" />
          </div>
          <div className="text-ink-muted mt-1 flex justify-between text-[10px]">
            <span>00:00</span>
            <span>06:00</span>
            <span>12:00</span>
            <span>18:00</span>
            <span>24:00</span>
          </div>
        </div>

        <dl className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
          <Detail label="Paid hours" value={`${(shift.scheduled_minutes / 60).toFixed(1)}h`} />
          <Detail label="Break" value={`${shift.break_minutes}m`} />
          <Detail
            label="Late grace"
            value={
              shift.grace_late_minutes === null ? 'Company' : `${shift.grace_late_minutes}m`
            }
          />
          <Detail
            label="Full day"
            value={
              shift.min_minutes_full_day === null
                ? 'Company'
                : `${(shift.min_minutes_full_day / 60).toFixed(1)}h`
            }
          />
        </dl>

        {shift.description && <p className="text-ink-soft text-xs">{shift.description}</p>}
      </CardBody>
    </Card>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-ink-muted uppercase">{label}</dt>
      <dd className="numeric text-ink font-medium">{value}</dd>
    </div>
  );
}

function toMinutes(clock: string): number {
  const [hours, minutes] = clock.split(':');
  return Number(hours) * 60 + Number(minutes);
}

function ShiftDialog({ onClose }: { onClose: () => void }) {
  const toast = useToast();
  const queryClient = useQueryClient();

  const [form, setForm] = useState({
    code: '',
    name: '',
    start_time: '09:30',
    end_time: '18:30',
    break_minutes: '60',
    description: '',
    work_location_id: '',
  });
  const [error, setError] = useState<string | null>(null);

  const locationsQuery = useQuery({
    queryKey: orgKeys.locations(),
    queryFn: () => fetchWorkLocations(),
  });

  const crossesMidnight = toMinutes(form.end_time) <= toMinutes(form.start_time);
  const spanMinutes = crossesMidnight
    ? 1440 - toMinutes(form.start_time) + toMinutes(form.end_time)
    : toMinutes(form.end_time) - toMinutes(form.start_time);
  const paidMinutes = Math.max(spanMinutes - Number(form.break_minutes || 0), 0);

  const mutation = useMutation({
    mutationFn: createShift,
    onSuccess: async () => {
      toast.success('Shift created');
      await queryClient.invalidateQueries({ queryKey: shiftKeys.all });
      onClose();
    },
    onError: (error) => toast.apiError(error, 'Could not create the shift'),
  });

  const submit = () => {
    if (!form.code.trim() || !form.name.trim()) {
      setError('A code and a name are required.');
      return;
    }
    if (form.start_time === form.end_time) {
      setError('The start and end times must differ.');
      return;
    }
    setError(null);
    mutation.mutate({
      code: form.code.trim(),
      name: form.name.trim(),
      description: form.description.trim() || null,
      start_time: form.start_time,
      end_time: form.end_time,
      break_minutes: Number(form.break_minutes || 0),
      work_location_id: form.work_location_id ? Number(form.work_location_id) : null,
    });
  };

  return (
    <Modal
      open
      onClose={onClose}
      title="Add a shift"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} isLoading={mutation.isPending}>
            Create shift
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

        <div className="grid grid-cols-2 gap-4">
          <TextField
            label="Code"
            required
            value={form.code}
            onChange={(event) => setForm({ ...form, code: event.target.value })}
          />
          <TextField
            label="Name"
            required
            value={form.name}
            onChange={(event) => setForm({ ...form, name: event.target.value })}
          />
          <TextField
            label="Start time"
            type="time"
            required
            value={form.start_time}
            onChange={(event) => setForm({ ...form, start_time: event.target.value })}
          />
          <TextField
            label="End time"
            type="time"
            required
            value={form.end_time}
            onChange={(event) => setForm({ ...form, end_time: event.target.value })}
          />
          <TextField
            label="Break (minutes)"
            inputMode="numeric"
            value={form.break_minutes}
            onChange={(event) => setForm({ ...form, break_minutes: event.target.value })}
          />
          <SelectField
            label="Work location"
            hint="Leave unset to make it available everywhere."
            value={form.work_location_id}
            onChange={(event) => setForm({ ...form, work_location_id: event.target.value })}
            options={[
              { value: '', label: 'All locations' },
              ...(locationsQuery.data ?? []).map((row) => ({
                value: String(row.id),
                label: row.name,
              })),
            ]}
          />
        </div>

        <div className="bg-sunken rounded-md p-3 text-sm">
          <p className="text-ink-muted text-xs font-semibold uppercase">This shift will be</p>
          <p className="text-ink mt-1">
            {crossesMidnight ? (
              <>
                <Moon className="mr-1 inline size-3.5" aria-hidden="true" />
                An <strong>overnight</strong> shift. Punches after midnight are attributed to
                the date the shift started.
              </>
            ) : (
              <>
                <Sun className="mr-1 inline size-3.5" aria-hidden="true" />A same-day shift.
              </>
            )}
          </p>
          <p className="numeric text-ink-soft mt-1">
            Span {(spanMinutes / 60).toFixed(1)}h · paid {(paidMinutes / 60).toFixed(1)}h
          </p>
        </div>

        <TextAreaField
          label="Description"
          rows={2}
          value={form.description}
          onChange={(event) => setForm({ ...form, description: event.target.value })}
        />

        <Alert variant="info" title="Thresholds inherit company settings">
          Grace periods and full/half-day thresholds are taken from company settings unless this
          shift overrides them, so a night shift can differ without every shift restating the
          defaults.
        </Alert>
      </div>
    </Modal>
  );
}

/* -------------------------------------------------------------------------- */
/* Calendars                                                                  */
/* -------------------------------------------------------------------------- */

function CalendarsPanel() {
  const { can } = useAuth();
  const today = todayIso();
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [creatingPattern, setCreatingPattern] = useState(false);
  const [creatingCalendar, setCreatingCalendar] = useState(false);
  const [view, setView] = useState(() => {
    const [year, month] = today.split('-');
    return { year: Number(year), month: Number(month) };
  });

  const calendarsQuery = useQuery({
    queryKey: shiftKeys.calendars(),
    queryFn: fetchCalendars,
  });
  const patternsQuery = useQuery({
    queryKey: shiftKeys.patterns(),
    queryFn: fetchPatterns,
  });

  const activeId = selectedId ?? calendarsQuery.data?.[0]?.id ?? null;
  const start = startOfMonth(view.year, view.month);
  const end = endOfMonth(view.year, view.month);

  const dayTypesQuery = useQuery({
    queryKey: shiftKeys.dayTypes(activeId ?? 0, start, end),
    queryFn: () => fetchDayTypes(activeId!, start, end),
    enabled: activeId !== null,
  });

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-64">
          <SelectField
            label="Work calendar"
            value={activeId ? String(activeId) : ''}
            onChange={(event) => setSelectedId(Number(event.target.value))}
            options={(calendarsQuery.data ?? []).map((row) => ({
              value: String(row.id),
              label: `${row.name}${row.is_default ? ' (default)' : ''}`,
            }))}
          />
        </div>
        {can('calendar.manage') && (
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" size="sm" onClick={() => setCreatingPattern(true)}>
              Add weekly pattern
            </Button>
            <Button
              size="sm"
              onClick={() => setCreatingCalendar(true)}
              leadingIcon={<Plus className="size-4" aria-hidden="true" />}
            >
              Add calendar
            </Button>
          </div>
        )}
      </div>

      {calendarsQuery.isSuccess && calendarsQuery.data.length === 0 && (
        <EmptyState
          icon={CalendarDays}
          title="No work calendars yet"
          description="Without one, attendance falls back to the company working week from settings. Create a weekly pattern, then a calendar."
        />
      )}

      {activeId !== null && (
        <Card>
          <CardHeader
            title="Working days"
            description="How this calendar classifies each date. A 'working' exception beats a weekend and a holiday; a 'non-working' one closes a working day."
          />
          <CardBody className="space-y-4">
            <div className="flex items-center justify-between gap-2">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  const next = addMonths(view.year, view.month, -1);
                  setView(next);
                }}
              >
                Previous
              </Button>
              <p className="text-ink font-semibold" aria-live="polite">
                {MONTH_NAMES[view.month - 1]} {view.year}
              </p>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  const next = addMonths(view.year, view.month, 1);
                  setView(next);
                }}
              >
                Next
              </Button>
            </div>

            {dayTypesQuery.isPending && <SkeletonRows rows={5} />}
            {dayTypesQuery.isError && (
              <ErrorPanel error={dayTypesQuery.error} title="Could not resolve day types" />
            )}
            {dayTypesQuery.isSuccess && (
              <CalendarGrid
                year={view.year}
                month={view.month}
                entries={dayTypesQuery.data}
                calendarId={activeId}
                canManage={can('calendar.manage')}
              />
            )}
          </CardBody>
        </Card>
      )}

      {patternsQuery.isSuccess && patternsQuery.data.length > 0 && (
        <Card>
          <CardHeader title="Weekly off patterns" />
          <CardBody>
            <TableShell caption="Weekly off patterns">
              <thead>
                <tr>
                  <Th>Code</Th>
                  <Th>Name</Th>
                  <Th>Off days</Th>
                </tr>
              </thead>
              <tbody>
                {patternsQuery.data.map((pattern) => (
                  <tr key={pattern.id}>
                    <Td className="font-mono text-xs">{pattern.code}</Td>
                    <Td className="font-medium">{pattern.name}</Td>
                    <Td>
                      <div className="flex gap-1">
                        {WEEKDAY_KEYS.map((key, index) => (
                          <span
                            key={key}
                            title={WEEKDAY_LABELS[index]}
                            className={cn(
                              'grid size-6 place-items-center rounded text-[10px] font-semibold uppercase',
                              pattern.off_days.includes(key)
                                ? 'bg-sunken text-ink-muted'
                                : 'bg-success-50 text-success-700',
                            )}
                          >
                            {key.slice(0, 1)}
                          </span>
                        ))}
                      </div>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </TableShell>
          </CardBody>
        </Card>
      )}

      {creatingPattern && <PatternDialog onClose={() => setCreatingPattern(false)} />}
      {creatingCalendar && (
        <CalendarDialog
          patterns={patternsQuery.data ?? []}
          onClose={() => setCreatingCalendar(false)}
        />
      )}
    </div>
  );
}

function CalendarGrid({
  year,
  month,
  entries,
  calendarId,
  canManage,
}: {
  year: number;
  month: number;
  entries: { work_date: IsoDate; day_type: string; source: string; reason: string | null }[];
  calendarId: number;
  canManage: boolean;
}) {
  const today = todayIso();
  const [adding, setAdding] = useState<IsoDate | null>(null);

  const byDate = useMemo(() => {
    const map = new Map<IsoDate, (typeof entries)[number]>();
    for (const entry of entries) map.set(entry.work_date, entry);
    return map;
  }, [entries]);

  const cells = trimGrid(monthGrid(year, month, today));

  return (
    <>
      <div className="mb-1 grid grid-cols-7 gap-1.5">
        {WEEKDAY_LABELS.map((label) => (
          <div
            key={label}
            className="text-ink-muted py-1 text-center text-[11px] font-semibold uppercase"
          >
            <span className="hidden sm:inline">{label}</span>
            <span className="sm:hidden">{label.slice(0, 1)}</span>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-7 gap-1.5">
        {cells.map((cell) => {
          const entry = byDate.get(cell.iso);
          const isException = entry?.source === 'calendar_exception';
          const style =
            entry?.day_type === 'holiday'
              ? 'bg-danger-50 border-danger-600/30 text-danger-900'
              : entry?.day_type === 'weekly_off'
                ? 'bg-sunken border-line text-ink-muted'
                : 'bg-success-50/50 border-success-600/20 text-success-900';

          return (
            <button
              key={cell.iso}
              type="button"
              disabled={!canManage}
              onClick={() => canManage && setAdding(cell.iso)}
              title={
                entry
                  ? `${entry.day_type}${entry.reason ? ` — ${entry.reason}` : ''}`
                  : undefined
              }
              className={cn(
                'relative min-h-16 rounded-lg border p-2 text-left transition-all',
                canManage && 'hover:ring-brand-400 hover:ring-2 hover:ring-inset',
                !canManage && 'cursor-default',
                !cell.inMonth && 'opacity-35',
                style,
                cell.isToday && 'ring-brand-500/50 ring-2 ring-inset',
              )}
            >
              <span className="numeric text-sm font-semibold">{cell.day}</span>
              {isException && (
                <span
                  className="bg-brand-600 absolute top-1.5 right-1.5 size-1.5 rounded-full"
                  aria-hidden="true"
                  title="Exception"
                />
              )}
              {entry?.reason && (
                <p className="mt-0.5 line-clamp-2 text-[10px] leading-tight opacity-80">
                  {entry.reason}
                </p>
              )}
            </button>
          );
        })}
      </div>

      <div className="text-ink-muted mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs">
        <span className="flex items-center gap-1.5">
          <span className="bg-success-50 border-success-600/20 size-3 rounded border" />
          Working
        </span>
        <span className="flex items-center gap-1.5">
          <span className="bg-sunken border-line size-3 rounded border" />
          Weekly off
        </span>
        <span className="flex items-center gap-1.5">
          <span className="bg-danger-50 border-danger-600/30 size-3 rounded border" />
          Holiday / closure
        </span>
        <span className="flex items-center gap-1.5">
          <span className="bg-brand-600 size-1.5 rounded-full" />
          Has an exception
        </span>
        {canManage && <span className="ml-auto">Select a date to add an exception.</span>}
      </div>

      {adding && (
        <ExceptionDialog
          calendarId={calendarId}
          date={adding}
          existing={byDate.get(adding)}
          onClose={() => setAdding(null)}
        />
      )}
    </>
  );
}

function ExceptionDialog({
  calendarId,
  date,
  existing,
  onClose,
}: {
  calendarId: number;
  date: IsoDate;
  existing?: { day_type: string; source: string; reason: string | null } | undefined;
  onClose: () => void;
}) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [type, setType] = useState<'non_working' | 'working'>('non_working');
  const [reason, setReason] = useState('');
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () =>
      addException(calendarId, {
        exception_date: date,
        exception_type: type,
        reason: reason.trim(),
      }),
    onSuccess: async () => {
      toast.success('Exception added');
      await queryClient.invalidateQueries({ queryKey: shiftKeys.all });
      onClose();
    },
    onError: (error) => toast.apiError(error, 'Could not add the exception'),
  });

  return (
    <Modal
      open
      onClose={onClose}
      title={`Exception for ${formatIsoLong(date)}`}
      description={existing ? `Currently classified as ${existing.day_type}.` : undefined}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={() => {
              if (!reason.trim()) {
                setError('A reason is required.');
                return;
              }
              setError(null);
              mutation.mutate();
            }}
            isLoading={mutation.isPending}
          >
            Add exception
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
        {existing?.source === 'calendar_exception' && (
          <Alert variant="warning" title="This date already has an exception">
            {existing.reason}. Adding another will be refused — remove the existing one first.
          </Alert>
        )}
        <SelectField
          label="Exception type"
          value={type}
          onChange={(event) => setType(event.target.value as 'non_working' | 'working')}
          options={[
            { value: 'non_working', label: 'Non-working (close a working day)' },
            { value: 'working', label: 'Working (open a weekend or holiday)' },
          ]}
          hint="A 'working' exception takes precedence over everything else."
        />
        <TextField
          label="Reason"
          required
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          hint="Shown on the calendar, e.g. 'Company closure' or 'Compensatory working Saturday'."
        />
      </div>
    </Modal>
  );
}

function PatternDialog({ onClose }: { onClose: () => void }) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [code, setCode] = useState('');
  const [name, setName] = useState('');
  const [offDays, setOffDays] = useState<string[]>(['sat', 'sun']);
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: createPattern,
    onSuccess: async () => {
      toast.success('Pattern created');
      await queryClient.invalidateQueries({ queryKey: shiftKeys.all });
      onClose();
    },
    onError: (error) => toast.apiError(error, 'Could not create the pattern'),
  });

  const toggle = (key: string) =>
    setOffDays((current) =>
      current.includes(key) ? current.filter((day) => day !== key) : [...current, key],
    );

  return (
    <Modal
      open
      onClose={onClose}
      title="Add a weekly off pattern"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={() => {
              if (!code.trim() || !name.trim()) {
                setError('A code and a name are required.');
                return;
              }
              if (offDays.length === 7) {
                setError('At least one day must remain a working day.');
                return;
              }
              setError(null);
              mutation.mutate({ code: code.trim(), name: name.trim(), off_days: offDays });
            }}
            isLoading={mutation.isPending}
          >
            Create pattern
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
        <div className="grid grid-cols-2 gap-4">
          <TextField
            label="Code"
            required
            value={code}
            onChange={(event) => setCode(event.target.value)}
          />
          <TextField
            label="Name"
            required
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </div>

        <fieldset>
          <legend className="text-ink mb-2 text-sm font-medium">Non-working days</legend>
          <div className="flex flex-wrap gap-2">
            {WEEKDAY_KEYS.map((key, index) => {
              const isOff = offDays.includes(key);
              return (
                <button
                  key={key}
                  type="button"
                  onClick={() => toggle(key)}
                  aria-pressed={isOff}
                  className={cn(
                    'rounded-md border px-3 py-2 text-sm font-medium transition-colors',
                    isOff
                      ? 'bg-sunken border-line-strong text-ink-muted'
                      : 'bg-success-50 border-success-600/30 text-success-800',
                  )}
                >
                  {WEEKDAY_LABELS[index]}
                </button>
              );
            })}
          </div>
          <p className="text-ink-muted mt-2 text-xs">
            Highlighted green = working. The company default confirmed in the brief is Monday to
            Friday.
          </p>
        </fieldset>
      </div>
    </Modal>
  );
}

function CalendarDialog({
  patterns,
  onClose,
}: {
  patterns: { id: number; code: string; name: string }[];
  onClose: () => void;
}) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [form, setForm] = useState({
    code: '',
    name: '',
    weekly_off_pattern_id: patterns[0] ? String(patterns[0].id) : '',
    work_location_id: '',
    is_default: false,
  });
  const [error, setError] = useState<string | null>(null);

  const locationsQuery = useQuery({
    queryKey: orgKeys.locations(),
    queryFn: () => fetchWorkLocations(),
  });

  const mutation = useMutation({
    mutationFn: createCalendar,
    onSuccess: async () => {
      toast.success('Calendar created');
      await queryClient.invalidateQueries({ queryKey: shiftKeys.all });
      onClose();
    },
    onError: (error) => toast.apiError(error, 'Could not create the calendar'),
  });

  return (
    <Modal
      open
      onClose={onClose}
      title="Add a work calendar"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={() => {
              if (!form.code.trim() || !form.name.trim() || !form.weekly_off_pattern_id) {
                setError('A code, a name and a weekly pattern are required.');
                return;
              }
              setError(null);
              mutation.mutate({
                code: form.code.trim(),
                name: form.name.trim(),
                weekly_off_pattern_id: Number(form.weekly_off_pattern_id),
                work_location_id: form.work_location_id ? Number(form.work_location_id) : null,
                is_default: form.is_default,
              });
            }}
            isLoading={mutation.isPending}
          >
            Create calendar
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
        {patterns.length === 0 && (
          <Alert variant="warning" title="No weekly patterns yet">
            Create a weekly off pattern first — a calendar is a pattern plus date exceptions.
          </Alert>
        )}
        <div className="grid grid-cols-2 gap-4">
          <TextField
            label="Code"
            required
            value={form.code}
            onChange={(event) => setForm({ ...form, code: event.target.value })}
          />
          <TextField
            label="Name"
            required
            value={form.name}
            onChange={(event) => setForm({ ...form, name: event.target.value })}
          />
        </div>
        <SelectField
          label="Weekly off pattern"
          required
          value={form.weekly_off_pattern_id}
          onChange={(event) => setForm({ ...form, weekly_off_pattern_id: event.target.value })}
          options={patterns.map((pattern) => ({
            value: String(pattern.id),
            label: `${pattern.name} (${pattern.code})`,
          }))}
        />
        <SelectField
          label="Work location"
          hint="Leave unset for a company-wide calendar."
          value={form.work_location_id}
          onChange={(event) => setForm({ ...form, work_location_id: event.target.value })}
          options={[
            { value: '', label: 'Company-wide' },
            ...(locationsQuery.data ?? []).map((row) => ({
              value: String(row.id),
              label: row.name,
            })),
          ]}
        />
        <CheckboxField
          label="Make this the default calendar"
          hint="Used when an employee's location has no calendar of its own. Setting this clears the flag on any other calendar."
          checked={form.is_default}
          onChange={(event) => setForm({ ...form, is_default: event.target.checked })}
        />
      </div>
    </Modal>
  );
}

/* -------------------------------------------------------------------------- */
/* Assignments                                                                */
/* -------------------------------------------------------------------------- */

function AssignmentsPanel() {
  const { can } = useAuth();
  const [employeeId, setEmployeeId] = useState<number | null>(null);
  const [assigning, setAssigning] = useState(false);

  const employeesQuery = useQuery({
    queryKey: employeeKeys.list({ purpose: 'shift-picker' }),
    queryFn: () => fetchEmployees({ page_size: 200 }),
  });

  const assignmentsQuery = useQuery({
    queryKey: shiftKeys.assignments(employeeId ?? 0),
    queryFn: () => fetchAssignments(employeeId!),
    enabled: employeeId !== null,
  });

  return (
    <div className="space-y-6">
      <Card>
        <CardBody>
          <div className="flex flex-wrap items-end gap-3">
            <div className="min-w-64 flex-1">
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
            {can('shift.manage') && employeeId !== null && (
              <Button
                onClick={() => setAssigning(true)}
                leadingIcon={<Plus className="size-4" aria-hidden="true" />}
              >
                Assign a shift
              </Button>
            )}
          </div>
        </CardBody>
      </Card>

      {employeeId === null ? (
        <EmptyState
          icon={Sun}
          title="Choose an employee"
          description="Their shift history appears here. Assignments are effective-dated, so past attendance keeps the shift it was actually calculated with."
        />
      ) : (
        <Card>
          <CardHeader
            title="Shift history"
            description="Periods must not overlap — two shifts on one day would make that day's calculation ambiguous."
          />
          <CardBody>
            {assignmentsQuery.isPending && <SkeletonRows rows={2} />}
            {assignmentsQuery.isError && <ErrorPanel error={assignmentsQuery.error} />}
            {assignmentsQuery.isSuccess &&
              (assignmentsQuery.data.length === 0 ? (
                <Alert variant="warning" title="No shift assigned">
                  Attendance for this employee is assessed against company default timings, and
                  their days are flagged as having no shift.
                </Alert>
              ) : (
                <TableShell caption="Shift assignments">
                  <thead>
                    <tr>
                      <Th>Shift</Th>
                      <Th>From</Th>
                      <Th>To</Th>
                      <Th>Status</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {assignmentsQuery.data.map((row) => (
                      <tr key={row.id}>
                        <Td>
                          <span className="text-ink font-medium">{row.shift_name}</span>
                          <span className="text-ink-muted ml-1.5 font-mono text-xs">
                            {row.shift_code}
                          </span>
                        </Td>
                        <Td className="numeric whitespace-nowrap">
                          {formatIsoLong(row.effective_from)}
                        </Td>
                        <Td className="numeric whitespace-nowrap">
                          {row.effective_to ? formatIsoLong(row.effective_to) : 'Open-ended'}
                        </Td>
                        <Td>
                          {row.is_current ? (
                            <Badge tone="success">Current</Badge>
                          ) : (
                            <Badge tone="neutral">Past</Badge>
                          )}
                        </Td>
                      </tr>
                    ))}
                  </tbody>
                </TableShell>
              ))}
          </CardBody>
        </Card>
      )}

      {assigning && employeeId !== null && (
        <AssignDialog employeeId={employeeId} onClose={() => setAssigning(false)} />
      )}
    </div>
  );
}

function AssignDialog({ employeeId, onClose }: { employeeId: number; onClose: () => void }) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [shiftId, setShiftId] = useState('');
  const [from, setFrom] = useState<IsoDate | ''>(todayIso());
  const [to, setTo] = useState<IsoDate | ''>('');
  const [notes, setNotes] = useState('');
  const [error, setError] = useState<string | null>(null);

  const shiftsQuery = useQuery({ queryKey: shiftKeys.list(), queryFn: () => fetchShifts() });

  const mutation = useMutation({
    mutationFn: createAssignment,
    onSuccess: async () => {
      toast.success('Shift assigned');
      await queryClient.invalidateQueries({ queryKey: shiftKeys.all });
      onClose();
    },
    onError: (error) => toast.apiError(error, 'Could not assign the shift'),
  });

  return (
    <Modal
      open
      onClose={onClose}
      title="Assign a shift"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={() => {
              if (!shiftId || !from) {
                setError('Choose a shift and a start date.');
                return;
              }
              setError(null);
              mutation.mutate({
                employee_id: employeeId,
                shift_id: Number(shiftId),
                effective_from: from,
                effective_to: to || null,
                notes: notes.trim() || null,
              });
            }}
            isLoading={mutation.isPending}
          >
            Assign
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
        <SelectField
          label="Shift"
          required
          value={shiftId}
          onChange={(event) => setShiftId(event.target.value)}
          options={[
            { value: '', label: 'Choose a shift…' },
            ...(shiftsQuery.data ?? []).map((shift) => ({
              value: String(shift.id),
              label: `${shift.name} (${trimSeconds(shift.start_time)}–${trimSeconds(
                shift.end_time,
              )})${shift.crosses_midnight ? ' overnight' : ''}`,
            })),
          ]}
        />
        <DatePicker
          label="Effective from"
          value={from}
          onChange={setFrom}
          required
          clearable={false}
          hint="Cannot precede the employee's joining date."
        />
        <DatePicker
          label="Effective to"
          value={to}
          onChange={setTo}
          hint="Leave empty for an open-ended assignment."
        />
        <TextAreaField
          label="Notes"
          rows={2}
          value={notes}
          onChange={(event) => setNotes(event.target.value)}
        />
        <Alert variant="info" title="Overlaps are refused">
          If this period overlaps an existing assignment, close that one first. The database
          enforces this as well as the application.
        </Alert>
      </div>
    </Modal>
  );
}
