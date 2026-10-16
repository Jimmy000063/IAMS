import { api } from '@/lib/api';
import type { DayQuantity } from '@/features/leave/api';
import type { IsoDate } from '@/lib/dates';

export type CreditStatus =
  'pending_verification' | 'available' | 'rejected' | 'expired' | 'reversed';

export type CompoffApplicationStatus =
  'pending_manager' | 'pending_hr' | 'approved' | 'rejected' | 'withdrawn' | 'cancelled';

export interface CompoffCredit {
  id: number;
  employee_id: number;
  earned_date: IsoDate;
  /** The attendance day that proves the qualifying work. */
  attendance_day_id: number | null;
  source: string;
  day_type: string;
  worked_minutes: number;
  credit_days: DayQuantity;
  allocated_days: DayQuantity;
  /** Each credit expires on its own date, which is why they are tracked as lots. */
  expiry_date: IsoDate | null;
  status: CreditStatus;
  verified_at: string | null;
  decision_note: string | null;
  reversal_of_id: number | null;
  created_at: string;
}

export interface CompoffBalance {
  as_of: IsoDate;
  /** Verified, unexpired, unallocated. What can actually be taken. */
  available_days: DayQuantity;
  /** Earned but not yet verified, so not spendable. */
  pending_verification_days: DayQuantity;
  /** Held by requests awaiting a decision. */
  committed_days: DayQuantity;
  expiring_soon_days: DayQuantity;
  expiring_on: IsoDate | null;
  /** Past its date but not yet closed by the expiry job. */
  expired_days: DayQuantity;
}

export interface CompoffDay {
  off_date: IsoDate;
  portion: string;
  day_fraction: DayQuantity;
  day_type: string;
}

export interface CompoffAllocation {
  credit_id: number;
  days: DayQuantity;
  allocated_at: string;
}

export interface CompoffApproval {
  sequence: number;
  stage: string;
  decision: string;
  decided_by_user_id: number | null;
  decided_by_employee_id: number | null;
  decided_at: string;
  comment: string | null;
  was_auto_skipped: boolean;
}

export interface CompoffApplication {
  id: number;
  employee_id: number;
  start_date: IsoDate;
  end_date: IsoDate;
  total_days: DayQuantity;
  reason: string | null;
  status: CompoffApplicationStatus;
  current_stage: string | null;
  escalated_from_missing_manager: boolean;
  decided_at: string | null;
  cancelled_at: string | null;
  cancellation_reason: string | null;
  created_at: string;
  days: CompoffDay[];
  allocations: CompoffAllocation[];
  approvals: CompoffApproval[];
}

export interface CompoffApplicationSummary {
  id: number;
  employee_id: number;
  employee_code: string | null;
  employee_name: string | null;
  start_date: IsoDate;
  end_date: IsoDate;
  total_days: DayQuantity;
  status: CompoffApplicationStatus;
  current_stage: string | null;
  reason: string | null;
  escalated_from_missing_manager: boolean;
  created_at: string;
}

export interface GrantRunResult {
  from: string;
  to: string;
  days_examined: number;
  credits_created: number;
  already_credited: number;
  not_qualifying: number;
  days_credited: DayQuantity;
  problems: Record<string, unknown>[];
}

export interface ExpiryRunResult {
  as_of: string;
  credits_expired: number;
  days_lost: DayQuantity;
  employees_affected: number;
}

export const fetchMyCompoffBalance = (asOf?: IsoDate) =>
  api.get<CompoffBalance>('/compoff/me/balance', { query: { as_of: asOf } });

export const fetchMyCredits = () => api.get<CompoffCredit[]>('/compoff/me/credits');

export const fetchMyCompoffApplications = () =>
  api.get<CompoffApplicationSummary[]>('/compoff/me/applications');

export const applyForCompoff = (input: {
  start_date: IsoDate;
  end_date: IsoDate;
  start_portion?: string;
  end_portion?: string;
  reason?: string | null;
}) => api.post<CompoffApplication>('/compoff/me/applications', input);

export const withdrawCompoff = (applicationId: number, reason?: string | null) =>
  api.post<CompoffApplication>(`/compoff/applications/${applicationId}/withdraw`, {
    reason: reason ?? null,
  });

export const cancelCompoff = (applicationId: number, reason: string) =>
  api.post<CompoffApplication>(`/compoff/applications/${applicationId}/cancel`, { reason });

export const fetchPendingCompoffApprovals = () =>
  api.get<CompoffApplicationSummary[]>('/compoff/approvals/pending');

export const decideCompoff = (
  applicationId: number,
  approve: boolean,
  comment?: string | null,
) =>
  api.post<CompoffApplication>(`/compoff/applications/${applicationId}/decide`, {
    approve,
    comment: comment ?? null,
  });

export const fetchCreditsPendingVerification = () =>
  api.get<CompoffCredit[]>('/compoff/credits/pending-verification');

export const decideCredit = (creditId: number, approve: boolean, note?: string | null) =>
  api.post<CompoffCredit>(`/compoff/credits/${creditId}/decide`, {
    approve,
    note: note ?? null,
  });

export const reverseCredit = (creditId: number, reason: string) =>
  api.post<CompoffCredit>(`/compoff/credits/${creditId}/reverse`, { reason });

export const grantManualCredit = (input: {
  employee_id: number;
  earned_date: IsoDate;
  credit_days: string;
  reason: string;
}) => api.post<CompoffCredit>('/compoff/credits/manual', input);

export const runCreditGrant = (input: {
  start: IsoDate;
  end: IsoDate;
  employee_id?: number | null;
}) => api.post<GrantRunResult>('/compoff/credits/grant', input);

export const runCreditExpiry = (asOf?: IsoDate | null) =>
  api.post<ExpiryRunResult>('/compoff/credits/expire', { as_of: asOf ?? null });

export const fetchEmployeeCompoffBalance = (employeeId: number) =>
  api.get<CompoffBalance>(`/compoff/employees/${employeeId}/balance`);

export const fetchEmployeeCredits = (employeeId: number) =>
  api.get<CompoffCredit[]>(`/compoff/employees/${employeeId}/credits`);

export const compoffKeys = {
  all: ['compoff'] as const,
  myBalance: (asOf?: string) => ['compoff', 'me', 'balance', asOf ?? 'today'] as const,
  myCredits: () => ['compoff', 'me', 'credits'] as const,
  myApplications: () => ['compoff', 'me', 'applications'] as const,
  pending: () => ['compoff', 'approvals', 'pending'] as const,
  pendingVerification: () => ['compoff', 'credits', 'pending-verification'] as const,
  employeeBalance: (id: number) => ['compoff', 'employee', id, 'balance'] as const,
  employeeCredits: (id: number) => ['compoff', 'employee', id, 'credits'] as const,
};
