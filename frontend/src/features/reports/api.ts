/**
 * Reports and exports.
 *
 * The catalogue, the columns and the filters all come from the backend. The
 * interface therefore has no second copy of which reports exist or what they
 * accept - a report added on the server appears here without a frontend change,
 * and cannot disagree about its own columns.
 */

import { api } from '@/lib/api';
import type { IsoDate } from '@/lib/dates';

export interface ReportCatalogueEntry {
  code: string;
  title: string;
  description: string;
  permission: string;
  /** Which filters this report accepts, so the form is built from the backend. */
  parameters: string[];
  /** 'all' when the caller's permissions cover everybody, 'team' when narrowed. */
  scope: 'all' | 'team';
}

export interface ReportColumn {
  key: string;
  label: string;
  kind: 'text' | 'number' | 'money' | 'days' | 'date' | 'boolean';
}

export interface ReportResult {
  code: string;
  title: string;
  description: string;
  columns: ReportColumn[];
  rows: Record<string, unknown>[];
  filters: Record<string, unknown>;
  totals: Record<string, string>;
  row_count: number;
  truncated: boolean;
}

export interface ReportFilters {
  start?: IsoDate;
  end?: IsoDate;
  as_of?: IsoDate;
  department_id?: number;
  run_id?: number;
  year?: number;
  include_exited?: boolean;
}

export const fetchCatalogue = () => api.get<ReportCatalogueEntry[]>('/reports');

export const runReport = (code: string, filters: ReportFilters) =>
  api.get<ReportResult>(`/reports/${code}`, { query: { ...filters } });

/**
 * Download an export.
 *
 * The bytes are fetched with the bearer token and handed to a synthetic link: a
 * plain `<a href>` cannot carry the token, and putting one in the URL would
 * leave it in browser history and server logs.
 */
export async function exportReport(
  code: string,
  filters: ReportFilters,
  format: 'csv' | 'xlsx',
): Promise<void> {
  const { tokenStore, ApiError } = await import('@/lib/api');
  const tokens = tokenStore.get();
  const base = import.meta.env.VITE_API_BASE_URL ?? '/api/v1';

  const query = new URLSearchParams({ format });
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== null && value !== '') {
      query.set(key, String(value));
    }
  }

  const response = await fetch(`${base}/reports/${code}/export?${query.toString()}`, {
    headers: tokens ? { Authorization: `Bearer ${tokens.accessToken}` } : {},
  });

  if (!response.ok) {
    let message = 'The export could not be produced.';
    try {
      const body = await response.json();
      message = body?.error?.message ?? message;
    } catch {
      // A non-JSON failure body; the generic message stands.
    }
    throw new ApiError({
      status: response.status,
      code: 'http_error',
      message,
      requestId: response.headers.get('X-Request-ID'),
    });
  }

  const disposition = response.headers.get('content-disposition') ?? '';
  const match = /filename="?([^";]+)"?/.exec(disposition);
  const filename = match?.[1] ?? `${code}.${format}`;

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

// ------------------------------------------------------------------ audit

export interface AuditEntry {
  id: number;
  action: string;
  entity_type: string | null;
  entity_id: string | null;
  actor_user_id: number | null;
  actor_label: string | null;
  actor_ip: string | null;
  request_id: string | null;
  before_value: unknown | null;
  after_value: unknown | null;
  context: unknown | null;
  succeeded: boolean;
  created_at: string;
}

export interface AuditPage {
  items: AuditEntry[];
  page: number;
  page_size: number;
  total: number;
  pages: number;
}

export interface AuditFilters {
  action?: string;
  entity_type?: string;
  entity_id?: string;
  actor_user_id?: number;
  request_id?: string;
  succeeded?: boolean;
  start?: IsoDate;
  end?: IsoDate;
  page?: number;
  page_size?: number;
}

export const fetchAuditLogs = (filters: AuditFilters) =>
  api.get<AuditPage>('/audit/logs', { query: { ...filters } });

export const fetchAuditActions = () =>
  api.get<{ action: string; count: number; last_seen: string | null }[]>('/audit/logs/actions');

export interface JobRun {
  id: number;
  job_name: string;
  idempotency_key: string | null;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  period_key: string | null;
  records_processed: number;
  records_skipped: number;
  records_failed: number;
  result: unknown | null;
  error_message: string | null;
}

export const fetchJobRuns = () => api.get<JobRun[]>('/audit/job-runs');

export interface RetentionReport {
  as_of: string;
  categories: {
    category: string;
    retention_days: number;
    confirmed: boolean;
    cutoff: string;
    rows_total: number;
    rows_beyond_retention: number;
    note: string;
  }[];
  unconfirmed: string[];
  note: string;
}

export const fetchRetention = () => api.get<RetentionReport>('/audit/retention');

/** A cell as text, formatted by the column kind the backend declared. */
export function formatCell(value: unknown, kind: ReportColumn['kind']): string {
  if (value === null || value === undefined || value === '') return '-';
  if (kind === 'boolean') return value ? 'yes' : 'no';
  // Money and day quantities arrive as strings and stay strings: parsing them
  // into numbers would reintroduce the floating-point error the backend's
  // Decimal arithmetic exists to avoid.
  return String(value);
}

export const isNumeric = (kind: ReportColumn['kind']) =>
  kind === 'number' || kind === 'money' || kind === 'days';
