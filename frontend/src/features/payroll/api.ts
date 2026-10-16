/**
 * Payroll, salary structures and statutory configuration.
 *
 * Every monetary value is a **string** here, not a number. The backend sends
 * Decimals as JSON strings - `"1800.00"` - and parsing them into JavaScript
 * numbers would reintroduce exactly the floating-point error the backend's
 * Decimal arithmetic exists to avoid. They are displayed as received, and
 * arithmetic on them happens on the server.
 */

import { api } from '@/lib/api';
import type { IsoDate } from '@/lib/dates';

/** A monetary amount, as the server sent it. Never parsed into a number. */
export type Money = string;
/** A day count to two places, likewise. */
export type Days = string;

export type ComponentType = 'earning' | 'deduction' | 'employer_contribution' | 'reimbursement';

export type CalculationType =
  | 'fixed'
  | 'percent_of_component'
  | 'percent_of_gross'
  | 'percent_of_ctc'
  | 'balance'
  | 'statutory'
  | 'attendance_based';

export type StatutoryDomain =
  'epf' | 'eps' | 'edli' | 'esi' | 'pt' | 'tds' | 'gratuity' | 'lwf';

export type RunStatus =
  'draft' | 'calculated' | 'reviewed' | 'approved' | 'finalized' | 'cancelled';

export interface SalaryComponent {
  id: number;
  code: string;
  name: string;
  description: string | null;
  component_type: ComponentType;
  calculation_type: CalculationType;
  of_component_id: number | null;
  percentage: string | null;
  statutory_domain: string | null;
  /** Whether it counts towards the provident-fund wage. A legal question the company answers. */
  is_pf_wage: boolean;
  is_esi_wage: boolean;
  is_pt_wage: boolean;
  is_taxable: boolean;
  prorates_on_payable_days: boolean;
  show_on_payslip: boolean;
  display_order: number;
  is_active: boolean;
  requires_legal_review: boolean;
}

export interface SalaryStructure {
  id: number;
  code: string;
  name: string;
  description: string | null;
  is_active: boolean;
  components: {
    component_id: number;
    fixed_amount: Money | null;
    percentage: string | null;
    sequence: number;
  }[];
}

export interface SalaryRevision {
  id: number;
  employee_id: number;
  structure_id: number | null;
  effective_from: IsoDate;
  effective_to: IsoDate | null;
  gross_monthly: Money;
  ctc_annual: Money | null;
  revision_reason: string | null;
  notes: string | null;
  components: {
    component_id: number;
    monthly_amount: Money;
    basis_description: string | null;
    sequence: number;
  }[];
  created_at: string;
}

export interface RuleParameter {
  key: string;
  label: string;
  numeric_value: string | null;
  json_value: unknown | null;
  unit: string | null;
  notes: string | null;
}

export interface StatutoryRuleSet {
  id: number;
  domain: StatutoryDomain;
  jurisdiction: string;
  state_code: string | null;
  version_label: string;
  effective_from: IsoDate;
  effective_to: IsoDate | null;
  status: 'draft' | 'verified' | 'superseded';
  /** The published document the figures came from. Required to verify. */
  source_reference: string | null;
  source_url: string | null;
  source_published_on: IsoDate | null;
  verified_by_user_id: number | null;
  verified_at: string | null;
  verification_note: string | null;
  notes: string | null;
  parameters: RuleParameter[];
}

export interface PayrollPeriod {
  id: number;
  year: number;
  month: number;
  start_date: IsoDate;
  end_date: IsoDate;
  pay_date: IsoDate | null;
  is_closed: boolean;
}

export interface PayrollRun {
  id: number;
  period_id: number;
  run_type: 'regular' | 'supplementary' | 'fnf';
  status: RunStatus;
  idempotency_key: string;
  notes: string | null;
  /** The rule sets this run used, which is what makes it reproducible. */
  rule_set_snapshot: Record<string, Record<string, unknown>> | null;
  settings_snapshot: Record<string, unknown> | null;
  unverified_domains: string[] | null;
  employee_count: number;
  total_gross: Money;
  total_deductions: Money;
  total_employer_cost: Money;
  total_net: Money;
  calculated_at: string | null;
  reviewed_at: string | null;
  approved_at: string | null;
  finalized_at: string | null;
  cancellation_reason: string | null;
  created_at: string;
}

export interface CalculateResult {
  run_id: number;
  status: string;
  period: string;
  payslips: number;
  skipped: { employee_id: number; employee_code: string; reason: string }[];
  total_gross: Money;
  total_deductions: Money;
  total_employer_cost: Money;
  total_net: Money;
  unverified_domains: string[];
  currency: string;
}

export interface PayslipLine {
  line_code: string;
  label: string;
  component_type: ComponentType;
  amount: Money;
  full_amount: Money | null;
  basis_amount: Money | null;
  /** What the figure was computed from, so it can be checked. */
  basis_description: string | null;
  statutory_rule_set_id: number | null;
  sequence: number;
}

export interface PayslipSummary {
  id: number;
  run_id: number;
  employee_code: string;
  employee_name: string;
  payable_days: Days;
  lop_days: Days;
  gross_earnings: Money;
  total_deductions: Money;
  net_pay: Money;
  published_at: string | null;
  review_flags: { code: string; message: string }[] | null;
}

export interface Payslip extends PayslipSummary {
  employee_id: number;
  designation_name: string | null;
  department_name: string | null;
  work_location_name: string | null;
  days_in_period: Days;
  paid_leave_days: Days;
  present_days: Days;
  weekly_off_days: Days;
  holiday_days: Days;
  overtime_minutes: number;
  employer_contributions: Money;
  is_prorated: boolean;
  proration_reason: string | null;
  calculation_notes: Record<string, unknown> | null;
  lines: PayslipLine[];
}

export interface Adjustment {
  id: number;
  employee_id: number;
  adjustment_type: 'arrear' | 'bonus' | 'recovery' | 'reversal';
  amount: Money;
  label: string;
  reason: string;
  relates_to_period_id: number | null;
  applied_in_run_id: number | null;
  is_taxable: boolean;
  status: 'pending' | 'applied' | 'cancelled';
  applied_at: string | null;
  created_at: string;
}

export interface Settlement {
  id: number;
  employee_id: number;
  exit_date: IsoDate;
  last_working_date: IsoDate | null;
  status: string;
  leave_encashment_days: Days;
  leave_encashment_amount: Money;
  notice_shortfall_days: Days;
  notice_recovery_amount: Money;
  gratuity_amount: Money;
  gratuity_rule_set_id: number | null;
  other_dues: Money;
  other_recoveries: Money;
  net_settlement: Money;
  computation_notes: ({ notes?: string[] } & Record<string, unknown>) | null;
  notes: string | null;
  approved_at: string | null;
  paid_at: string | null;
}

export interface Register {
  run_id: number;
  period: string;
  status: string;
  run_type: string;
  employee_count: number;
  total_gross: Money;
  total_deductions: Money;
  total_employer_cost: Money;
  total_net: Money;
  rule_sets_used: Record<string, Record<string, unknown>>;
  settings_used: Record<string, unknown>;
  unverified_domains: string[];
  rows: {
    payslip_id: number;
    employee_id: number;
    employee_code: string;
    employee_name: string;
    department: string | null;
    payable_days: Days;
    lop_days: Days;
    earnings: Record<string, Money>;
    deductions: Record<string, Money>;
    employer_contributions: Record<string, Money>;
    gross_earnings: Money;
    total_deductions: Money;
    net_pay: Money;
    flags: string[];
  }[];
}

export interface Reconciliation {
  run_id: number;
  balanced: boolean;
  payslip_count: number;
  lines_gross: Money;
  lines_deductions: Money;
  lines_employer: Money;
  payslips_gross: Money;
  payslips_deductions: Money;
  payslips_net: Money;
  run_gross: Money;
  run_deductions: Money;
  run_net: Money;
  run_employer_cost: Money;
  mismatched: Record<string, unknown>[];
}

export interface StatutorySummary {
  run_id: number;
  period: string;
  status: string;
  lines: {
    line_code: string;
    label: string;
    component_type: string;
    employee_count: number;
    total_amount: Money;
    rule_set_id: number | null;
  }[];
  rule_sets_used: Record<string, Record<string, unknown>>;
  note: string;
}

export interface ReadinessReport {
  ready: boolean;
  blockers: string[];
  enabled_statutory_modules: string[];
  domains: {
    domain: string;
    has_rule_set: boolean;
    status: string | null;
    version: string | null;
    parameter_count: number;
    source_reference: string | null;
  }[];
  active_components: number;
  employees_without_salary: number;
  unconfirmed_settings: string[];
}

// --------------------------------------------------------------- queries

export const fetchComponents = (includeInactive = false) =>
  api.get<SalaryComponent[]>('/payroll/components', {
    query: { include_inactive: includeInactive },
  });

export const createComponent = (body: Record<string, unknown>) =>
  api.post<SalaryComponent>('/payroll/components', body);

export const updateComponent = (id: number, body: Record<string, unknown>) =>
  api.patch<SalaryComponent>(`/payroll/components/${id}`, body);

export const fetchStructures = () => api.get<SalaryStructure[]>('/payroll/structures');

export const createStructure = (body: Record<string, unknown>) =>
  api.post<SalaryStructure>('/payroll/structures', body);

export const fetchRevisions = (employeeId: number) =>
  api.get<SalaryRevision[]>(`/payroll/revisions/${employeeId}`);

export const createRevision = (body: Record<string, unknown>) =>
  api.post<SalaryRevision>('/payroll/revisions', body);

export const fetchMySalary = () => api.get<SalaryRevision>('/payroll/me/salary');

export const fetchRuleSets = (domain?: string) =>
  api.get<StatutoryRuleSet[]>('/payroll/statutory/rule-sets', {
    ...(domain ? { query: { domain } } : {}),
  });

export const createRuleSet = (body: Record<string, unknown>) =>
  api.post<StatutoryRuleSet>('/payroll/statutory/rule-sets', body);

export const setRuleParameters = (id: number, parameters: Record<string, unknown>[]) =>
  api.put<StatutoryRuleSet>(`/payroll/statutory/rule-sets/${id}/parameters`, { parameters });

export const verifyRuleSet = (id: number, body: Record<string, unknown>) =>
  api.post<StatutoryRuleSet>(`/payroll/statutory/rule-sets/${id}/verify`, body);

export const supersedeRuleSet = (id: number, reason: string) =>
  api.post<StatutoryRuleSet>(`/payroll/statutory/rule-sets/${id}/supersede`, { reason });

export const setEligibility = (body: Record<string, unknown>) =>
  api.post<unknown>('/payroll/statutory/eligibility', body);

export const fetchEligibility = (employeeId: number) =>
  api.get<
    {
      id: number;
      employee_id: number;
      domain: string;
      is_eligible: boolean;
      effective_from: IsoDate;
      effective_to: IsoDate | null;
      reason: string | null;
    }[]
  >(`/payroll/statutory/eligibility/${employeeId}`);

export const fetchReadiness = () => api.get<ReadinessReport>('/payroll/readiness');

export const fetchRuns = (year?: number) =>
  api.get<PayrollRun[]>('/payroll/runs', { ...(year ? { query: { year } } : {}) });

export const fetchRun = (id: number) => api.get<PayrollRun>(`/payroll/runs/${id}`);

export const createRun = (body: { year: number; month: number; run_type?: string }) =>
  api.post<PayrollRun>('/payroll/runs', body);

export const calculateRun = (id: number) =>
  api.post<CalculateResult>(`/payroll/runs/${id}/calculate`, {});

export const reviewRun = (id: number) => api.post<PayrollRun>(`/payroll/runs/${id}/review`);
export const approveRun = (id: number) => api.post<PayrollRun>(`/payroll/runs/${id}/approve`);
export const finalizeRun = (id: number) => api.post<PayrollRun>(`/payroll/runs/${id}/finalize`);

export const sendRunBack = (id: number, reason: string) =>
  api.post<PayrollRun>(`/payroll/runs/${id}/send-back`, { reason });

export const cancelRun = (id: number, reason: string) =>
  api.post<PayrollRun>(`/payroll/runs/${id}/cancel`, { reason });

export const publishRun = (id: number) =>
  api.post<{
    run_id: number;
    published: number;
    notifications_created: number;
    period: string;
  }>(`/payroll/runs/${id}/publish`);

export const fetchRunPayslips = (id: number) =>
  api.get<PayslipSummary[]>(`/payroll/runs/${id}/payslips`);

export const fetchRegister = (id: number) => api.get<Register>(`/payroll/runs/${id}/register`);

export const fetchReconciliation = (id: number) =>
  api.get<Reconciliation>(`/payroll/runs/${id}/reconciliation`);

export const fetchStatutorySummary = (id: number) =>
  api.get<StatutorySummary>(`/payroll/runs/${id}/statutory-summary`);

export const fetchPayslip = (id: number) => api.get<Payslip>(`/payroll/payslips/${id}`);

export const fetchMyPayslips = () => api.get<PayslipSummary[]>('/payroll/me/payslips');

export const fetchAdjustments = () => api.get<Adjustment[]>('/payroll/adjustments');

export const createAdjustment = (body: Record<string, unknown>) =>
  api.post<Adjustment>('/payroll/adjustments', body);

export const cancelAdjustment = (id: number, reason: string) =>
  api.post<Adjustment>(`/payroll/adjustments/${id}/cancel`, { reason });

export const calculateSettlement = (employeeId: number, body: Record<string, unknown>) =>
  api.post<Settlement>(`/payroll/settlements/${employeeId}/calculate`, body);

export const approveSettlement = (employeeId: number) =>
  api.post<Settlement>(`/payroll/settlements/${employeeId}/approve`);

export const fetchSettlement = (employeeId: number) =>
  api.get<Settlement>(`/payroll/settlements/${employeeId}`);

// ---------------------------------------------------------------- display

export const DOMAIN_LABELS: Record<string, string> = {
  epf: 'Provident fund (EPF)',
  eps: 'Pension scheme (EPS)',
  edli: 'Deposit-linked insurance (EDLI)',
  esi: "Employees' state insurance (ESI)",
  pt: 'Professional tax',
  tds: 'Income tax (TDS)',
  gratuity: 'Gratuity',
  lwf: 'Labour welfare fund',
};

export const domainLabel = (code: string) => DOMAIN_LABELS[code] ?? code.toUpperCase();

export const STATUS_TONES: Record<
  string,
  'neutral' | 'brand' | 'success' | 'warning' | 'danger'
> = {
  draft: 'neutral',
  calculated: 'brand',
  reviewed: 'warning',
  approved: 'warning',
  finalized: 'success',
  cancelled: 'danger',
  verified: 'success',
  superseded: 'neutral',
};

/**
 * Group a payslip's lines the way a payslip reads.
 *
 * Employer contributions are kept apart from deductions: they are a company
 * cost shown for transparency, and listing them with deductions is the classic
 * payslip error that makes an employee think they were underpaid.
 */
export function groupLines(lines: PayslipLine[]) {
  return {
    earnings: lines.filter(
      (line) => line.component_type === 'earning' || line.component_type === 'reimbursement',
    ),
    deductions: lines.filter((line) => line.component_type === 'deduction'),
    employer: lines.filter((line) => line.component_type === 'employer_contribution'),
  };
}

/** A month as people write it, from a period's year and month. */
export const periodLabel = (year: number, month: number) =>
  new Date(year, month - 1, 1).toLocaleDateString(undefined, {
    month: 'long',
    year: 'numeric',
  });
