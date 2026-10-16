import { api } from '@/lib/api';

/* -------------------------------------------------------------------------- */
/* Types - mirror the backend response models                                  */
/* -------------------------------------------------------------------------- */

export type EmploymentType = 'full_time' | 'part_time' | 'contract' | 'intern' | 'consultant';
export type EmploymentStatus =
  'probation' | 'active' | 'notice_period' | 'suspended' | 'exited';
export type ExitType =
  'resignation' | 'termination' | 'retirement' | 'end_of_contract' | 'other';

export const EMPLOYMENT_TYPES: EmploymentType[] = [
  'full_time',
  'part_time',
  'contract',
  'intern',
  'consultant',
];

export const EMPLOYMENT_STATUSES: EmploymentStatus[] = [
  'probation',
  'active',
  'notice_period',
  'suspended',
  'exited',
];

export const EXIT_TYPES: ExitType[] = [
  'resignation',
  'termination',
  'retirement',
  'end_of_contract',
  'other',
];

export interface EmployeeSummary {
  id: number;
  employee_code: string;
  full_name: string;
  work_email: string | null;
  department_id: number | null;
  department_name: string | null;
  designation_id: number | null;
  designation_name: string | null;
  work_location_id: number | null;
  work_location_name: string | null;
  reporting_manager_id: number | null;
  employment_type: string;
  employment_status: string;
  date_of_joining: string;
  date_of_exit: string | null;
}

export interface EmployeeDetail extends EmployeeSummary {
  first_name: string;
  middle_name: string | null;
  last_name: string | null;
  date_of_birth: string | null;
  gender: string | null;
  marital_status: string | null;
  personal_email: string | null;
  work_phone: string | null;
  personal_phone: string | null;
  emergency_contact_name: string | null;
  emergency_contact_phone: string | null;
  emergency_contact_relation: string | null;
  address_line1: string | null;
  address_line2: string | null;
  city: string | null;
  state: string | null;
  postal_code: string | null;
  probation_end_date: string | null;
  date_of_confirmation: string | null;
  exit_type: string | null;
  exit_reason: string | null;
  last_working_day: string | null;
  notes: string | null;
  reporting_manager_name: string | null;
  reporting_manager_code: string | null;
  work_location_timezone: string | null;
  direct_report_count: number;
  linked_user_id: number | null;
  has_statutory_details: boolean;
  has_bank_account: boolean;
  created_at: string;
  updated_at: string;
}

export interface EmploymentHistoryEntry {
  id: number;
  effective_from: string;
  effective_to: string | null;
  department_id: number | null;
  designation_id: number | null;
  work_location_id: number | null;
  reporting_manager_id: number | null;
  employment_type: string | null;
  employment_status: string | null;
  change_type: string;
  change_reason: string | null;
}

export interface PageMeta {
  page: number;
  page_size: number;
  total_items: number;
  total_pages: number;
  has_next: boolean;
  has_previous: boolean;
}

export interface Paged<T> {
  items: T[];
  meta: PageMeta;
}

export interface Department {
  id: number;
  code: string;
  name: string;
  description: string | null;
  parent_id: number | null;
  head_employee_id: number | null;
  is_active: boolean;
}

export interface Designation {
  id: number;
  code: string;
  name: string;
  description: string | null;
  level: number | null;
  is_active: boolean;
}

export interface WorkLocation {
  id: number;
  code: string;
  name: string;
  timezone: string;
  address_line1: string | null;
  address_line2: string | null;
  city: string | null;
  state: string | null;
  postal_code: string | null;
  country_code: string;
  is_active: boolean;
}

export interface StatutoryDetails {
  employee_id: number;
  pf_uan: string | null;
  pf_number: string | null;
  esi_number: string | null;
  pan_masked: string | null;
  aadhaar_last4: string | null;
  pf_opted_in: boolean | null;
  esi_eligible_override: boolean | null;
  pt_state: string | null;
  tax_regime: string | null;
}

export interface BankAccount {
  id: number;
  employee_id: number;
  account_holder_name: string;
  account_number_masked: string;
  ifsc_code: string;
  bank_name: string;
  branch_name: string | null;
  account_type: string | null;
  is_primary: boolean;
  is_verified: boolean;
}

export interface ImportReport {
  ok: boolean;
  dry_run: boolean;
  total_rows: number;
  valid_rows: number;
  error_count: number;
  errors: { row: number; column: string | null; message: string }[];
  created_codes: string[];
  preview: {
    row: number;
    employee_code: string;
    name: string;
    work_email: string | null;
    employment_type: string;
    date_of_joining: string;
    reporting_manager_code: string | null;
  }[];
}

export interface HrDashboard {
  as_of: string;
  headcount: {
    active_total: number;
    by_status: Record<string, number>;
    by_employment_type: Record<string, number>;
    by_department: { department_id: number | null; department_name: string; count: number }[];
    by_work_location: {
      work_location_id: number | null;
      work_location_name: string;
      count: number;
    }[];
  };
  joiners: {
    recent: { id: number; employee_code: string; full_name: string; date_of_joining: string }[];
    upcoming: {
      id: number;
      employee_code: string;
      full_name: string;
      date_of_joining: string;
    }[];
  };
  attention: {
    probation_ending_soon: {
      id: number;
      employee_code: string;
      full_name: string;
      probation_end_date: string | null;
    }[];
    reports_needing_manager: { id: number; employee_code: string; full_name: string }[];
    employees_without_account: number;
  };
  configuration: {
    total: number;
    confirmed: number;
    unconfirmed: number;
    awaiting_legal_verification: number;
    unconfirmed_keys: string[];
    legal_review_keys: string[];
  };
  not_yet_available: Record<string, string>;
}

export interface EmployeeDashboard {
  as_of: string;
  employee: {
    id: number;
    employee_code: string;
    full_name: string;
    department_name: string | null;
    work_location_name: string | null;
    work_location_timezone: string | null;
    employment_status: string;
    employment_type: string;
    date_of_joining: string;
    tenure_days: number;
    is_pre_joining: boolean;
    probation_end_date: string | null;
    manager_name: string | null;
    manager_code: string | null;
  } | null;
  team?: { direct_reports: number; total_reports: number; is_manager: boolean };
  message?: string;
  not_yet_available: Record<string, string>;
}

/* -------------------------------------------------------------------------- */
/* Query keys                                                                 */
/* -------------------------------------------------------------------------- */

export const employeeKeys = {
  all: ['employees'] as const,
  list: (params: Record<string, unknown>) => ['employees', 'list', params] as const,
  graph: (params: Record<string, unknown>) => ['employees', 'graph', params] as const,
  detail: (id: number) => ['employees', 'detail', id] as const,
  history: (id: number) => ['employees', 'history', id] as const,
  statutory: (id: number) => ['employees', 'statutory', id] as const,
  bank: (id: number) => ['employees', 'bank', id] as const,
  me: () => ['employees', 'me'] as const,
  myTeam: () => ['employees', 'me', 'team'] as const,
};

export const orgKeys = {
  all: ['org'] as const,
  departments: (includeInactive = false) => ['org', 'departments', includeInactive] as const,
  designations: (includeInactive = false) => ['org', 'designations', includeInactive] as const,
  locations: (includeInactive = false) => ['org', 'locations', includeInactive] as const,
};

export const dashboardKeys = {
  hr: () => ['dashboard', 'hr'] as const,
  me: () => ['dashboard', 'me'] as const,
};

/* -------------------------------------------------------------------------- */
/* Requests                                                                   */
/* -------------------------------------------------------------------------- */

export interface EmployeeListParams {
  page?: number;
  page_size?: number;
  q?: string;
  department_id?: number;
  work_location_id?: number;
  employment_status?: string;
  employment_type?: string;
  include_exited?: boolean;
  sort_by?: string;
  sort_dir?: 'asc' | 'desc';
}

export function fetchEmployees(params: EmployeeListParams): Promise<Paged<EmployeeSummary>> {
  return api.get<Paged<EmployeeSummary>>('/employees', {
    query: params as Record<string, string | number | boolean | undefined>,
  });
}

/** Rows per request when collecting the whole set; the server's own maximum. */
const GRAPH_PAGE_SIZE = 200;

/** How many of those requests to make before giving up and saying so. */
const GRAPH_PAGE_LIMIT = 10;

export interface EmployeeGraph {
  employees: EmployeeSummary[];
  /** Total the server reports, which may exceed what was collected. */
  totalItems: number;
  /** True when the cap was reached, so the tree below is incomplete. */
  truncated: boolean;
}

/**
 * Every employee matching the filters, for views that need the whole set at
 * once - the organisation chart cannot be drawn from one page of twenty-five,
 * because a manager and their reports routinely land on different pages.
 *
 * Paged through rather than requested as one unbounded list: the endpoint caps
 * `page_size` at 200, and an endpoint that will return an unbounded number of
 * rows is a denial of service waiting for a large enough company. The cap is
 * reported rather than hidden, so the chart can say it is incomplete instead of
 * quietly drawing a wrong hierarchy.
 */
export async function fetchEmployeeGraph(
  params: Omit<EmployeeListParams, 'page' | 'page_size'>,
): Promise<EmployeeGraph> {
  const employees: EmployeeSummary[] = [];
  let page = 1;

  for (;;) {
    const result = await fetchEmployees({ ...params, page, page_size: GRAPH_PAGE_SIZE });
    const totalItems = result.meta.total_items;
    employees.push(...result.items);
    if (!result.meta.has_next) return { employees, totalItems, truncated: false };
    if (page >= GRAPH_PAGE_LIMIT) return { employees, totalItems, truncated: true };
    page += 1;
  }
}

export const fetchEmployee = (id: number) => api.get<EmployeeDetail>(`/employees/${id}`);

export const fetchMyProfile = () => api.get<EmployeeDetail>('/employees/me');

export const fetchMyTeam = () => api.get<EmployeeSummary[]>('/employees/me/team');

export const fetchEmploymentHistory = (id: number) =>
  api.get<EmploymentHistoryEntry[]>(`/employees/${id}/history`);

export const fetchStatutoryDetails = (id: number) =>
  api.get<StatutoryDetails>(`/employees/${id}/statutory`);

export const fetchBankAccounts = (id: number) =>
  api.get<BankAccount[]>(`/employees/${id}/bank-accounts`);

export const fetchNextCode = (prefix = 'ITC') =>
  api.get<{ suggested_code: string }>('/employees/next-code', { query: { prefix } });

export const createEmployee = (body: Record<string, unknown>) =>
  api.post<EmployeeDetail>('/employees', body);

export const updateEmployee = (id: number, body: Record<string, unknown>) =>
  api.patch<EmployeeDetail>(`/employees/${id}`, body);

export const updateMyProfile = (body: Record<string, unknown>) =>
  api.patch<EmployeeDetail>('/employees/me', body);

export const exitEmployee = (id: number, body: Record<string, unknown>) =>
  api.post<EmployeeDetail>(`/employees/${id}/exit`, body);

export const linkAccount = (id: number, userId: number) =>
  api.post<EmployeeDetail>(`/employees/${id}/link-account`, { user_id: userId });

export const saveStatutoryDetails = (id: number, body: Record<string, unknown>) =>
  api.put<StatutoryDetails>(`/employees/${id}/statutory`, body);

export const addBankAccount = (id: number, body: Record<string, unknown>) =>
  api.post<BankAccount>(`/employees/${id}/bank-accounts`, body);

export const fetchDepartments = (includeInactive = false) =>
  api.get<Department[]>('/org/departments', { query: { include_inactive: includeInactive } });

export const fetchDesignations = (includeInactive = false) =>
  api.get<Designation[]>('/org/designations', { query: { include_inactive: includeInactive } });

export const fetchWorkLocations = (includeInactive = false) =>
  api.get<WorkLocation[]>('/org/work-locations', {
    query: { include_inactive: includeInactive },
  });

export const createDepartment = (body: Record<string, unknown>) =>
  api.post<Department>('/org/departments', body);

export const updateDepartment = (id: number, body: Record<string, unknown>) =>
  api.patch<Department>(`/org/departments/${id}`, body);

export const createDesignation = (body: Record<string, unknown>) =>
  api.post<Designation>('/org/designations', body);

export const updateDesignation = (id: number, body: Record<string, unknown>) =>
  api.patch<Designation>(`/org/designations/${id}`, body);

export const createWorkLocation = (body: Record<string, unknown>) =>
  api.post<WorkLocation>('/org/work-locations', body);

export const updateWorkLocation = (id: number, body: Record<string, unknown>) =>
  api.patch<WorkLocation>(`/org/work-locations/${id}`, body);

export const fetchHrDashboard = () => api.get<HrDashboard>('/dashboard/hr');

export const fetchMyDashboard = () => api.get<EmployeeDashboard>('/dashboard/me');

/**
 * Multipart upload for the CSV import.
 *
 * Uses `fetch` directly rather than the JSON client: the body is FormData, and
 * the browser must set the multipart boundary in Content-Type itself. Setting
 * that header manually produces a request the server cannot parse.
 */
export async function importEmployees(file: File, dryRun: boolean): Promise<ImportReport> {
  const { tokenStore, ApiError } = await import('@/lib/api');
  const form = new FormData();
  form.append('file', file);
  form.append('dry_run', String(dryRun));

  const tokens = tokenStore.get();
  const response = await fetch(
    `${import.meta.env.VITE_API_BASE_URL ?? '/api/v1'}/employees/import`,
    {
      method: 'POST',
      headers: tokens ? { Authorization: `Bearer ${tokens.accessToken}` } : {},
      body: form,
    },
  );

  const body = await response.json();
  if (!response.ok) {
    // A whole-file problem (bad encoding, missing column) comes back as the
    // standard error envelope; per-row problems come back as a 200 report.
    throw new ApiError({
      status: response.status,
      code: body?.error?.code ?? 'http_error',
      message: body?.error?.message ?? 'The import could not be processed.',
      details: body?.error?.details ?? [],
      requestId: response.headers.get('X-Request-ID'),
    });
  }
  return body as ImportReport;
}

/**
 * Download a file from an authenticated endpoint.
 *
 * A plain `<a href>` cannot carry the bearer token, so the bytes are fetched,
 * wrapped in a blob URL and handed to a synthetic link. The blob URL is
 * revoked straight away so it does not leak for the page's lifetime.
 */
export async function downloadAuthenticatedFile(
  path: string,
  fallbackFilename: string,
): Promise<void> {
  const { tokenStore, ApiError } = await import('@/lib/api');
  const tokens = tokenStore.get();

  const response = await fetch(`${import.meta.env.VITE_API_BASE_URL ?? '/api/v1'}${path}`, {
    headers: tokens ? { Authorization: `Bearer ${tokens.accessToken}` } : {},
  });

  if (!response.ok) {
    throw new ApiError({
      status: response.status,
      code: 'http_error',
      message: 'The file could not be downloaded.',
      requestId: response.headers.get('X-Request-ID'),
    });
  }

  // Prefer the server's filename; it is the one the audit log recorded.
  const disposition = response.headers.get('content-disposition') ?? '';
  const match = /filename="?([^";]+)"?/.exec(disposition);
  const filename = match?.[1] ?? fallbackFilename;

  const url = URL.createObjectURL(await response.blob());
  try {
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
  } finally {
    URL.revokeObjectURL(url);
  }
}

export const downloadImportTemplate = () =>
  downloadAuthenticatedFile('/employees/import-template', 'employee-import-template.csv');

export const downloadEmployeeDocument = (employeeId: number, documentId: number) =>
  downloadAuthenticatedFile(
    `/employees/${employeeId}/documents/${documentId}/download`,
    'document',
  );
