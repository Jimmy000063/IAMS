import { api } from '@/lib/api';
import type { IsoDate } from '@/lib/dates';

/* -------------------------------------------------------------------------- */
/* Types                                                                      */
/* -------------------------------------------------------------------------- */

/**
 * Leave quantities arrive as decimal **strings** (`"2.50"`), not numbers.
 *
 * Deliberate: a JSON number is an IEEE double, and a balance that has had thirty
 * movements applied to it must still reconcile exactly. Format these for display
 * and compare them with `compareDays`; never coerce one to a float and add it up.
 */
export type DayQuantity = string;

export type DayPortion = 'full' | 'first_half' | 'second_half';
export type AccrualFrequency = 'none' | 'monthly' | 'quarterly' | 'half_yearly' | 'annual';
export type RoundingMode = 'none' | 'nearest_half' | 'nearest_full' | 'down_half' | 'up_half';
export type GenderRestriction = 'any' | 'female' | 'male';
export type ApplicationStatus =
  'pending_manager' | 'pending_hr' | 'approved' | 'rejected' | 'withdrawn' | 'cancelled';

export interface LeaveType {
  id: number;
  code: string;
  name: string;
  description: string | null;
  is_paid: boolean;
  deducts_balance: boolean;
  allows_half_day: boolean;
  allows_negative_balance: boolean | null;
  counts_non_working_days: boolean;
  requires_reason: boolean;
  attachment_required_above_days: DayQuantity | null;
  min_notice_days: number;
  max_consecutive_days: number | null;
  min_service_days: number;
  gender_restriction: string;
  requires_legal_review: boolean;
  display_order: number;
  colour: string | null;
  is_active: boolean;
}

export interface LeavePolicy {
  id: number;
  leave_type_id: number;
  name: string;
  effective_from: IsoDate;
  effective_to: IsoDate | null;
  annual_quota_days: DayQuantity;
  accrual_frequency: string;
  accrual_rounding: string;
  prorate_on_joining: boolean;
  prorate_on_exit: boolean;
  accrue_from_confirmation: boolean;
  max_accumulation_days: DayQuantity | null;
  carry_forward_cap_days: DayQuantity | null;
  carry_forward_expires_after_months: number | null;
  encashment_allowed: boolean;
  encashment_max_days: DayQuantity | null;
  notes: string | null;
  requires_legal_review: boolean;
}

export interface LeaveBalance {
  leave_type_id: number;
  leave_type_code: string;
  leave_type_name: string;
  colour: string | null;
  is_paid: boolean;
  deducts_balance: boolean;
  allows_half_day: boolean;
  counts_non_working_days: boolean;
  requires_reason: boolean;
  requires_legal_review: boolean;

  leave_year: number;
  leave_year_start: IsoDate;
  leave_year_end: IsoDate;
  annual_quota_days: DayQuantity | null;
  accrual_frequency: string | null;
  has_policy: boolean;

  /** The ledger sum as of the query date. The authoritative historical figure. */
  balance_days: DayQuantity;
  /** Committed by applications nobody has decided yet. */
  pending_days: DayQuantity;
  /** Approved but dated later - leave already booked for a future date. */
  committed_days: DayQuantity;
  /** balance minus pending minus committed. What a new request may draw on. */
  available_days: DayQuantity;
  accrued_days: DayQuantity;
  used_days: DayQuantity;
  expiring_days: DayQuantity;
  expiring_on: IsoDate | null;
}

export interface LeaveDay {
  leave_date: IsoDate;
  portion: string;
  day_fraction: DayQuantity;
  is_paid: boolean;
  day_type: string;
  counts_against_balance: boolean;
}

export interface LeaveApproval {
  sequence: number;
  stage: string;
  decision: string;
  decided_by_user_id: number | null;
  decided_by_employee_id: number | null;
  decided_at: string;
  comment: string | null;
  was_auto_skipped: boolean;
}

export interface LeaveApplication {
  id: number;
  employee_id: number;
  leave_type_id: number;
  leave_policy_id: number | null;
  start_date: IsoDate;
  end_date: IsoDate;
  total_days: DayQuantity;
  paid_days: DayQuantity;
  unpaid_days: DayQuantity;
  reason: string | null;
  contact_during_leave: string | null;
  status: ApplicationStatus;
  current_stage: string | null;
  escalated_from_missing_manager: boolean;
  is_backdated: boolean;
  applied_on_behalf: boolean;
  decided_at: string | null;
  cancelled_at: string | null;
  cancellation_reason: string | null;
  supersedes_application_id: number | null;
  attachment_file_id: number | null;
  policy_snapshot: Record<string, unknown> | null;
  created_at: string;
  days: LeaveDay[];
  approvals: LeaveApproval[];
}

export interface LeaveApplicationSummary {
  id: number;
  employee_id: number;
  employee_code: string | null;
  employee_name: string | null;
  leave_type_id: number;
  leave_type_code: string | null;
  leave_type_name: string | null;
  start_date: IsoDate;
  end_date: IsoDate;
  total_days: DayQuantity;
  paid_days: DayQuantity;
  unpaid_days: DayQuantity;
  status: ApplicationStatus;
  current_stage: string | null;
  reason: string | null;
  is_backdated: boolean;
  escalated_from_missing_manager: boolean;
  applied_on_behalf: boolean;
  created_at: string;
}

export interface LedgerEntry {
  id: number;
  leave_type_id: number;
  txn_type: string;
  /** Signed: positive credits, negative consumes. */
  days: DayQuantity;
  effective_date: IsoDate;
  leave_year: number;
  expires_on: IsoDate | null;
  application_id: number | null;
  reverses_entry_id: number | null;
  reason: string | null;
  created_at: string;
}

export interface AccrualRunResult {
  as_of: string;
  leave_year: number;
  period_key: string;
  employees_considered: number;
  entries_posted: number;
  already_posted: number;
  skipped_no_policy: number;
  skipped_not_employed: number;
  skipped_at_ceiling: number;
  days_credited: DayQuantity;
  problems: Record<string, unknown>[];
}

export interface LeaveCalendarEntry {
  employee_id: number;
  employee_code: string;
  employee_name: string;
  leave_date: IsoDate;
  portion: string;
  leave_type_code: string;
  leave_type_name: string;
  colour: string | null;
  status: string;
}

export interface LeavePreview {
  leave_type_code: string;
  total_days: DayQuantity;
  paid_days: DayQuantity;
  unpaid_days: DayQuantity;
  span_days: number;
  available_days: DayQuantity;
  split_reason: string;
  notes: string[];
  days: LeaveDay[];
  /** Every reason it would be refused, not just the first. */
  problems: { field: string | null; message: string }[];
  can_submit: boolean;
}

export interface ApplyInput {
  leave_type_id: number;
  start_date: IsoDate;
  end_date: IsoDate;
  start_portion?: DayPortion;
  end_portion?: DayPortion;
  reason?: string | null;
  contact_during_leave?: string | null;
}

/* -------------------------------------------------------------------------- */
/* Quantity helpers                                                           */
/* -------------------------------------------------------------------------- */

/**
 * Format a decimal day string for display: `"2.00"` becomes `2`, `"0.50"`
 * becomes `0.5`.
 *
 * Presentation only. The original string stays the value of record.
 */
export function formatDays(value: DayQuantity | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—';
  const trimmed = value.replace(/\.0+$/, '').replace(/(\.\d*[1-9])0+$/, '$1');
  return trimmed === '-0' ? '0' : trimmed;
}

/** Compare two decimal day strings without going through a float. */
export function compareDays(a: DayQuantity, b: DayQuantity): number {
  const scale = (value: string) => Math.round(Number.parseFloat(value) * 100);
  return scale(a) - scale(b);
}

/** Whether a quantity is greater than zero, for enabling an action. */
export function isPositive(value: DayQuantity | null | undefined): boolean {
  return value !== null && value !== undefined && compareDays(value, '0') > 0;
}

/* -------------------------------------------------------------------------- */
/* Requests                                                                   */
/* -------------------------------------------------------------------------- */

export const fetchLeaveTypes = (includeInactive = false) =>
  api.get<LeaveType[]>('/leave/types', { query: { include_inactive: includeInactive } });

export const createLeaveType = (input: Partial<LeaveType>) =>
  api.post<LeaveType>('/leave/types', input);

export const updateLeaveType = (typeId: number, input: Partial<LeaveType>) =>
  api.patch<LeaveType>(`/leave/types/${typeId}`, input);

export const fetchPolicies = (leaveTypeId?: number) =>
  api.get<LeavePolicy[]>('/leave/policies', {
    query: { leave_type_id: leaveTypeId ?? undefined },
  });

export const createPolicy = (input: Record<string, unknown>) =>
  api.post<LeavePolicy>('/leave/policies', input);

export const closePolicy = (policyId: number, effectiveTo: IsoDate) =>
  api.post<LeavePolicy>(`/leave/policies/${policyId}/close`, { effective_to: effectiveTo });

export const fetchMyBalances = (asOf?: IsoDate) =>
  api.get<LeaveBalance[]>('/leave/me/balances', { query: { as_of: asOf ?? undefined } });

export const fetchEmployeeBalances = (employeeId: number, asOf?: IsoDate) =>
  api.get<LeaveBalance[]>(`/leave/employees/${employeeId}/balances`, {
    query: { as_of: asOf ?? undefined },
  });

export const fetchMyApplications = (limit = 50) =>
  api.get<LeaveApplicationSummary[]>('/leave/me/applications', { query: { limit } });

export const fetchApplications = (params: {
  employee_id?: number;
  leave_type_id?: number;
  status?: string;
  start?: IsoDate;
  end?: IsoDate;
  limit?: number;
}) => api.get<LeaveApplicationSummary[]>('/leave/applications', { query: params });

export const fetchApplication = (applicationId: number) =>
  api.get<LeaveApplication>(`/leave/applications/${applicationId}`);

/**
 * Ask the backend what an application would cost.
 *
 * The form does not count days itself: a browser-side copy of the calendar and
 * sandwich rules would drift from the backend and show the applicant a number
 * that turns out to be wrong after approval.
 */
export const previewLeave = (input: ApplyInput & { employee_id?: number }) =>
  api.post<LeavePreview>('/leave/preview', input);

export const applyForLeave = (input: ApplyInput) =>
  api.post<LeaveApplication>('/leave/me/applications', input);

export const applyOnBehalf = (input: ApplyInput & { employee_id: number }) =>
  api.post<LeaveApplication>('/leave/applications/on-behalf', input);

export const decideApplication = (
  applicationId: number,
  approve: boolean,
  comment?: string | null,
) =>
  api.post<LeaveApplication>(`/leave/applications/${applicationId}/decide`, {
    approve,
    comment: comment ?? null,
  });

export const withdrawApplication = (applicationId: number, reason?: string | null) =>
  api.post<LeaveApplication>(`/leave/applications/${applicationId}/withdraw`, {
    reason: reason ?? null,
  });

export const cancelApplication = (applicationId: number, reason: string) =>
  api.post<LeaveApplication>(`/leave/applications/${applicationId}/cancel`, { reason });

export const fetchPendingApprovals = () =>
  api.get<LeaveApplicationSummary[]>('/leave/approvals/pending');

export const fetchMyLedger = (leaveTypeId?: number) =>
  api.get<LedgerEntry[]>('/leave/me/ledger', {
    query: { leave_type_id: leaveTypeId ?? undefined },
  });

export const fetchEmployeeLedger = (employeeId: number, leaveTypeId?: number) =>
  api.get<LedgerEntry[]>(`/leave/employees/${employeeId}/ledger`, {
    query: { leave_type_id: leaveTypeId ?? undefined },
  });

export const postAdjustment = (input: {
  employee_id: number;
  leave_type_id: number;
  days: string;
  effective_date: IsoDate;
  reason: string;
  token: string;
}) => api.post<LedgerEntry>('/leave/adjustments', input);

export const grantOpeningBalance = (input: {
  employee_id: number;
  leave_type_id: number;
  days: string;
  leave_year?: number | null;
  reason?: string;
}) => api.post<LedgerEntry | null>('/leave/opening-balances', input);

export const runAccrual = (input: {
  as_of?: IsoDate | null;
  leave_type_id?: number | null;
  employee_id?: number | null;
}) => api.post<AccrualRunResult>('/leave/accrual/run', input);

export const fetchLeaveCalendar = (params: {
  start?: IsoDate;
  end?: IsoDate;
  include_pending?: boolean;
}) => api.get<LeaveCalendarEntry[]>('/leave/calendar', { query: params });

export const assignPolicy = (input: {
  employee_id: number;
  leave_policy_id: number;
  effective_from: IsoDate;
  effective_to?: IsoDate | null;
  notes?: string | null;
}) => api.post<unknown>('/leave/policy-assignments', input);

/* -------------------------------------------------------------------------- */
/* Query keys                                                                 */
/* -------------------------------------------------------------------------- */

export const leaveKeys = {
  all: ['leave'] as const,
  types: (includeInactive = false) => ['leave', 'types', includeInactive] as const,
  policies: (leaveTypeId?: number) => ['leave', 'policies', leaveTypeId ?? 'all'] as const,
  myBalances: (asOf?: string) => ['leave', 'me', 'balances', asOf ?? 'today'] as const,
  myApplications: () => ['leave', 'me', 'applications'] as const,
  myLedger: (leaveTypeId?: number) => ['leave', 'me', 'ledger', leaveTypeId ?? 'all'] as const,
  applications: (params: Record<string, unknown>) => ['leave', 'applications', params] as const,
  application: (id: number) => ['leave', 'application', id] as const,
  pending: () => ['leave', 'approvals', 'pending'] as const,
  employeeBalances: (id: number, asOf?: string) =>
    ['leave', 'employee', id, 'balances', asOf ?? 'today'] as const,
  employeeLedger: (id: number, leaveTypeId?: number) =>
    ['leave', 'employee', id, 'ledger', leaveTypeId ?? 'all'] as const,
  calendar: (params: Record<string, unknown>) => ['leave', 'calendar', params] as const,
  preview: (params: Record<string, unknown>) => ['leave', 'preview', params] as const,
};
