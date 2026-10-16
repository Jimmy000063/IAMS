import { api } from '@/lib/api';
import type { IsoDate } from '@/lib/dates';

/* -------------------------------------------------------------------------- */
/* Types                                                                      */
/* -------------------------------------------------------------------------- */

export type AttendanceStatus =
  | 'present'
  | 'half_day'
  | 'absent'
  | 'on_leave'
  | 'holiday'
  | 'weekly_off'
  | 'incomplete'
  | 'missing_punch';

export type DayType = 'working' | 'weekly_off' | 'holiday';

export interface AttendanceSession {
  sequence: number;
  started_at: string;
  ended_at: string;
  duration_minutes: number;
}

export interface AttendanceDay {
  id: number;
  employee_id: number;
  work_date: IsoDate;
  day_type: DayType;
  status: AttendanceStatus;
  shift_id: number | null;
  first_in_at: string | null;
  last_out_at: string | null;
  worked_minutes: number;
  break_minutes: number;
  net_minutes: number;
  late_minutes: number;
  early_exit_minutes: number;
  overtime_minutes: number;
  is_late: boolean;
  is_early_exit: boolean;
  has_missing_punch: boolean;
  payable_day_fraction: string;
  leave_application_id: number | null;
  is_manual_override: boolean;
  override_reason: string | null;
  computed_at: string;
  computed_from_event_count: number;
  anomalies: string[] | null;
  policy_snapshot: Record<string, unknown> | null;
  sessions: AttendanceSession[];
}

export interface PunchState {
  work_date: IsoDate;
  timezone: string;
  server_time: string;
  is_punched_in: boolean;
  next_punch: 'in' | 'out';
  session_count: number;
  worked_minutes_so_far: number;
  shift_id: number | null;
  shift_code: string | null;
  shift_start: string;
  shift_end: string;
  has_shift_assigned: boolean;
  status: string | null;
  events: { event_type: string; occurred_at: string }[];
}

export interface PunchResult {
  recorded: boolean;
  event_type: string;
  work_date: IsoDate;
  day: AttendanceDay;
  message: string;
}

export interface AttendanceSummary {
  from: string;
  to: string;
  days_recorded: number;
  status_counts: Record<string, number>;
  payable_days: string;
  worked_minutes: number;
  overtime_minutes: number;
  late_days: number;
  early_exit_days: number;
  missing_punch_days: number;
  days_with_anomalies: number;
}

export interface Shift {
  id: number;
  code: string;
  name: string;
  description: string | null;
  start_time: string;
  end_time: string;
  crosses_midnight: boolean;
  break_minutes: number;
  scheduled_minutes: number;
  grace_late_minutes: number | null;
  grace_early_exit_minutes: number | null;
  min_minutes_full_day: number | null;
  min_minutes_half_day: number | null;
  early_punch_window_minutes: number;
  late_punch_window_minutes: number;
  work_location_id: number | null;
  is_active: boolean;
}

export interface WeeklyOffPattern {
  id: number;
  code: string;
  name: string;
  off_days: string[];
  is_active: boolean;
}

export interface WorkCalendar {
  id: number;
  code: string;
  name: string;
  weekly_off_pattern_id: number;
  weekly_off_pattern_code: string;
  off_days: string[];
  work_location_id: number | null;
  is_default: boolean;
  is_active: boolean;
  exception_count: number;
}

export interface CalendarException {
  id: number;
  calendar_id: number;
  exception_date: IsoDate;
  exception_type: 'working' | 'non_working';
  reason: string;
}

export interface DayTypeEntry {
  work_date: IsoDate;
  day_type: DayType;
  source: string;
  reason: string | null;
}

export interface ShiftAssignment {
  id: number;
  employee_id: number;
  shift_id: number;
  shift_code: string;
  shift_name: string;
  effective_from: IsoDate;
  effective_to: IsoDate | null;
  work_calendar_id: number | null;
  notes: string | null;
  is_current: boolean;
}

export interface Regularization {
  id: number;
  employee_id: number;
  employee_code: string | null;
  employee_name: string | null;
  work_date: IsoDate;
  requested_in_at: string | null;
  requested_out_at: string | null;
  reason: string;
  status: 'pending_manager' | 'pending_hr' | 'approved' | 'rejected' | 'cancelled';
  current_stage: 'reporting_manager' | 'hr_admin' | 'complete';
  status_before: string | null;
  manager_decision: string | null;
  manager_comment: string | null;
  manager_decided_at: string | null;
  hr_decision: string | null;
  hr_comment: string | null;
  hr_decided_at: string | null;
  escalated_from_missing_manager: boolean;
  created_at: string;
}

export interface TodayOverview {
  date: string;
  active_employees: number;
  status_counts: Record<string, number>;
  present: number;
  half_day: number;
  absent: number;
  on_leave: number;
  weekly_off: number;
  holiday: number;
  incomplete: number;
  late_arrivals: number;
  missing_punches: number;
  pending_regularizations: number;
  not_yet_computed: number;
}

/* -------------------------------------------------------------------------- */
/* Query keys                                                                 */
/* -------------------------------------------------------------------------- */

export const attendanceKeys = {
  all: ['attendance'] as const,
  state: () => ['attendance', 'state'] as const,
  mine: (start: string, end: string) => ['attendance', 'mine', start, end] as const,
  mySummary: (start: string, end: string) =>
    ['attendance', 'mine', 'summary', start, end] as const,
  employee: (id: number, start: string, end: string) =>
    ['attendance', 'employee', id, start, end] as const,
  today: () => ['attendance', 'today'] as const,
  anomalies: (start: string, end: string) => ['attendance', 'anomalies', start, end] as const,
  myRegularizations: () => ['attendance', 'regularizations', 'mine'] as const,
  pendingRegularizations: () => ['attendance', 'regularizations', 'pending'] as const,
};

export const shiftKeys = {
  all: ['shifts'] as const,
  list: (includeInactive = false) => ['shifts', 'list', includeInactive] as const,
  patterns: () => ['shifts', 'patterns'] as const,
  calendars: () => ['shifts', 'calendars'] as const,
  exceptions: (calendarId: number, year?: number) =>
    ['shifts', 'exceptions', calendarId, year ?? 'all'] as const,
  dayTypes: (calendarId: number, start: string, end: string) =>
    ['shifts', 'day-types', calendarId, start, end] as const,
  assignments: (employeeId: number) => ['shifts', 'assignments', employeeId] as const,
};

/* -------------------------------------------------------------------------- */
/* Requests                                                                   */
/* -------------------------------------------------------------------------- */

export const fetchPunchState = () => api.get<PunchState>('/attendance/state');

export const punch = (punchType: 'in' | 'out' | 'auto') =>
  api.post<PunchResult>('/attendance/punch', { punch_type: punchType });

export const fetchMyAttendance = (start: IsoDate, end: IsoDate) =>
  api.get<AttendanceDay[]>('/attendance/me', { query: { start, end } });

export const fetchMySummary = (start: IsoDate, end: IsoDate) =>
  api.get<AttendanceSummary>('/attendance/me/summary', { query: { start, end } });

export const fetchEmployeeAttendance = (employeeId: number, start: IsoDate, end: IsoDate) =>
  api.get<AttendanceDay[]>(`/attendance/employees/${employeeId}`, {
    query: { start, end },
  });

export const fetchTodayOverview = () => api.get<TodayOverview>('/attendance/today');

export const fetchAnomalies = (start: IsoDate, end: IsoDate) =>
  api.get<AttendanceDay[]>('/attendance/anomalies', { query: { start, end } });

export const addManualEvent = (body: {
  employee_id: number;
  event_type: 'in' | 'out';
  local_datetime: string;
  reason: string;
}) => api.post<AttendanceDay>('/attendance/events', body);

export const overrideDay = (body: {
  employee_id: number;
  work_date: IsoDate;
  status: AttendanceStatus;
  payable_day_fraction: string;
  reason: string;
}) => api.post<AttendanceDay>('/attendance/override', body);

export const recompute = (body: {
  employee_ids?: number[] | null;
  start: IsoDate;
  end: IsoDate;
  force_over_manual_override?: boolean;
  respect_lock?: boolean;
}) =>
  api.post<{
    employees: number;
    from: string;
    to: string;
    days_computed: number;
    skipped_locked: number;
    skipped_not_employed: number;
    preserved_manual_overrides: number;
    lock_cutoff: string | null;
  }>('/attendance/recompute', body);

export const requestRegularization = (body: {
  work_date: IsoDate;
  local_in?: string | null;
  local_out?: string | null;
  reason: string;
}) => api.post<Regularization>('/attendance/regularizations', body);

export const fetchMyRegularizations = () =>
  api.get<Regularization[]>('/attendance/regularizations/mine');

export const fetchPendingRegularizations = () =>
  api.get<Regularization[]>('/attendance/regularizations/pending');

export const decideRegularization = (
  id: number,
  body: { approve: boolean; comment?: string | null },
) => api.post<Regularization>(`/attendance/regularizations/${id}/decision`, body);

export const cancelRegularization = (id: number) =>
  api.post<Regularization>(`/attendance/regularizations/${id}/cancel`);

export const fetchShifts = (includeInactive = false) =>
  api.get<Shift[]>('/shifts', { query: { include_inactive: includeInactive } });

export const createShift = (body: Record<string, unknown>) => api.post<Shift>('/shifts', body);

export const updateShift = (id: number, body: Record<string, unknown>) =>
  api.patch<Shift>(`/shifts/${id}`, body);

export const fetchPatterns = () => api.get<WeeklyOffPattern[]>('/shifts/weekly-off-patterns');

export const createPattern = (body: Record<string, unknown>) =>
  api.post<WeeklyOffPattern>('/shifts/weekly-off-patterns', body);

export const fetchCalendars = () => api.get<WorkCalendar[]>('/shifts/calendars');

export const createCalendar = (body: Record<string, unknown>) =>
  api.post<WorkCalendar>('/shifts/calendars', body);

export const fetchExceptions = (calendarId: number, year?: number) =>
  api.get<CalendarException[]>(`/shifts/calendars/${calendarId}/exceptions`, {
    query: year ? { year } : {},
  });

export const addException = (
  calendarId: number,
  body: { exception_date: IsoDate; exception_type: string; reason: string },
) => api.post<CalendarException>(`/shifts/calendars/${calendarId}/exceptions`, body);

export const fetchDayTypes = (calendarId: number, start: IsoDate, end: IsoDate) =>
  api.get<DayTypeEntry[]>(`/shifts/calendars/${calendarId}/day-types`, {
    query: { start, end },
  });

export const fetchAssignments = (employeeId: number) =>
  api.get<ShiftAssignment[]>(`/shifts/assignments/${employeeId}`);

export const createAssignment = (body: Record<string, unknown>) =>
  api.post<ShiftAssignment>('/shifts/assignments', body);

export const closeAssignment = (id: number, effectiveTo: IsoDate) =>
  api.post<ShiftAssignment>(`/shifts/assignments/${id}/close`, {
    effective_to: effectiveTo,
  });

/* -------------------------------------------------------------------------- */
/* Presentation helpers                                                       */
/* -------------------------------------------------------------------------- */

/**
 * Colours for the month grid.
 *
 * `absent` is the only red: it is the one status that costs the employee a day.
 * Days off are neutral rather than coloured, so a month of weekends does not
 * dominate the grid, and `incomplete` is amber because it is a data problem
 * awaiting action rather than a verdict.
 */
export const STATUS_STYLES: Record<
  AttendanceStatus,
  { cell: string; dot: string; label: string }
> = {
  present: {
    cell: 'bg-success-50 border-success-600/30 text-success-900',
    dot: 'bg-success-600',
    label: 'Present',
  },
  half_day: {
    cell: 'bg-accent-50 border-accent-300 text-accent-900',
    dot: 'bg-accent-500',
    label: 'Half day',
  },
  absent: {
    cell: 'bg-danger-50 border-danger-600/30 text-danger-900',
    dot: 'bg-danger-600',
    label: 'Absent',
  },
  on_leave: {
    cell: 'bg-brand-50 border-brand-200 text-brand-900',
    dot: 'bg-brand-500',
    label: 'On leave',
  },
  holiday: {
    cell: 'bg-sunken border-line text-ink-soft',
    dot: 'bg-ink-muted',
    label: 'Holiday',
  },
  weekly_off: {
    cell: 'bg-sunken border-line text-ink-muted',
    dot: 'bg-ink-muted',
    label: 'Weekly off',
  },
  incomplete: {
    cell: 'bg-warning-50 border-warning-600/40 text-warning-900',
    dot: 'bg-warning-600',
    label: 'Incomplete',
  },
  missing_punch: {
    cell: 'bg-warning-50 border-warning-600/40 text-warning-900',
    dot: 'bg-warning-600',
    label: 'Missing punch',
  },
};

export const REGULARIZATION_STATUS_LABELS: Record<Regularization['status'], string> = {
  pending_manager: 'Awaiting manager',
  pending_hr: 'Awaiting HR',
  approved: 'Approved',
  rejected: 'Rejected',
  cancelled: 'Withdrawn',
};
