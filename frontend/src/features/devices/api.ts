import { api } from '@/lib/api';
import type { IsoDate } from '@/lib/dates';

/* -------------------------------------------------------------------------- */
/* Types                                                                      */
/* -------------------------------------------------------------------------- */

export type AdapterType = 'csv_upload' | 'webhook' | 'rest_poll' | 'simulator';
export type DirectionMode = 'explicit' | 'alternate' | 'always_in' | 'always_out';
export type AdapterMaturity = 'tested' | 'generic' | 'development_only';
export type RawEventStatus =
  'pending' | 'mapped' | 'duplicate' | 'unmapped' | 'failed' | 'ignored';

export interface Device {
  id: number;
  code: string;
  name: string;
  description: string | null;
  adapter_type: AdapterType;
  vendor: string | null;
  model: string | null;
  serial_number: string | null;
  firmware_version: string | null;
  work_location_id: number | null;
  timezone_override: string | null;
  config: Record<string, unknown>;
  direction_mode: DirectionMode;
  clock_drift_tolerance_seconds: number;
  allowed_source_ips: string[] | null;
  is_active: boolean;
  sync_cursor: string | null;
  last_sync_at: string | null;
  last_sync_status: string | null;
  last_seen_at: string | null;
  consecutive_failures: number;
  /** Presence only. A secret is never readable once stored. */
  has_api_secret: boolean;
  has_webhook_secret: boolean;
  is_push_adapter: boolean;
  created_at: string;
  updated_at: string;
}

export interface ConfigField {
  key: string;
  label: string;
  required: boolean;
  help_text: string;
  example: string;
}

export interface AdapterDescription {
  adapter_type: AdapterType;
  name: string;
  mode: 'pull' | 'push';
  maturity: AdapterMaturity;
  maturity_meaning: string;
  summary: string;
  requires_api_secret: boolean;
  requires_webhook_secret: boolean;
  config_fields: ConfigField[];
  caveats: string[];
}

export interface AdapterCatalogue {
  adapters: AdapterDescription[];
  timestamp_formats: string[];
  vendor_integration_notes: string[];
}

export interface DeviceMapping {
  id: number;
  device_id: number;
  external_employee_ref: string;
  employee_id: number;
  effective_from: IsoDate;
  effective_to: IsoDate | null;
  notes: string | null;
  created_at: string;
}

export interface IngestProblem {
  reason: string;
  field: string | null;
  employee_ref?: string | null;
  raw_timestamp?: string | null;
}

export interface IngestReport {
  device_code: string;
  status: 'succeeded' | 'partial' | 'failed';
  fetched: number;
  accepted: number;
  duplicate: number;
  unmapped: number;
  failed: number;
  ignored: number;
  days_recomputed: number;
  problems: IngestProblem[];
  sync_run_id: number | null;
  cursor_after: string | null;
}

export interface SyncRun {
  id: number;
  device_id: number;
  trigger: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  events_fetched: number;
  events_accepted: number;
  events_duplicate: number;
  events_unmapped: number;
  events_failed: number;
  events_ignored: number;
  attendance_days_recomputed: number;
  cursor_before: string | null;
  cursor_after: string | null;
  error_message: string | null;
  details: Record<string, unknown> | null;
}

export interface RawEvent {
  id: number;
  device_id: number;
  sync_run_id: number | null;
  external_event_id: string;
  external_employee_ref: string | null;
  raw_timestamp: string | null;
  raw_direction: string | null;
  parsed_occurred_at: string | null;
  parsed_direction: string | null;
  timezone_used: string | null;
  status: RawEventStatus;
  employee_id: number | null;
  attendance_event_id: number | null;
  error_message: string | null;
  process_attempts: number;
  last_attempt_at: string | null;
  received_at: string;
  resolution_note: string | null;
  raw_payload: Record<string, unknown>;
}

export interface DeviceHealth {
  as_of: string;
  devices_total: number;
  devices_active: number;
  quiet_after_minutes: number;
  quiet_devices: {
    id: number;
    code: string;
    name: string;
    last_seen_at: string;
    silent_for_hours: number;
  }[];
  never_reported: { id: number; code: string; name: string }[];
  failing_devices: {
    id: number;
    code: string;
    consecutive_failures: number;
    last_sync_status: string | null;
  }[];
  events_needing_attention: Record<string, number>;
  events_needing_attention_total: number;
}

export interface WebhookInstructions {
  url: string;
  method: string;
  content_type: string;
  signature_header: string;
  signature_algorithm: string;
  secret_configured: boolean;
  notes: string[];
}

export interface DeviceInput {
  code: string;
  name: string;
  description?: string | null;
  adapter_type: AdapterType;
  vendor?: string | null;
  model?: string | null;
  serial_number?: string | null;
  work_location_id?: number | null;
  timezone_override?: string | null;
  config: Record<string, unknown>;
  direction_mode: DirectionMode;
  clock_drift_tolerance_seconds?: number;
  allowed_source_ips?: string[] | null;
  is_active?: boolean;
  api_secret?: string;
  webhook_secret?: string;
}

/* -------------------------------------------------------------------------- */
/* Requests                                                                   */
/* -------------------------------------------------------------------------- */

export const fetchAdapterCatalogue = () => api.get<AdapterCatalogue>('/devices/adapters');

export const fetchDeviceHealth = () => api.get<DeviceHealth>('/devices/health');

export const fetchDevices = (includeInactive: boolean) =>
  api.get<Device[]>('/devices', { query: { include_inactive: includeInactive } });

export const fetchDevice = (deviceId: number) => api.get<Device>(`/devices/${deviceId}`);

export const createDevice = (input: DeviceInput) => api.post<Device>('/devices', input);

export const updateDevice = (deviceId: number, input: Partial<DeviceInput>) =>
  api.patch<Device>(`/devices/${deviceId}`, input);

export const fetchWebhookInstructions = (deviceId: number) =>
  api.get<WebhookInstructions>(`/devices/${deviceId}/webhook-url`);

export const fetchMappings = (deviceId: number) =>
  api.get<DeviceMapping[]>(`/devices/${deviceId}/mappings`);

export const createMapping = (
  deviceId: number,
  input: {
    external_employee_ref: string;
    employee_id: number;
    effective_from: IsoDate;
    effective_to?: IsoDate | null;
    notes?: string | null;
  },
) => api.post<DeviceMapping>(`/devices/${deviceId}/mappings`, input);

export const closeMapping = (mappingId: number, effectiveTo: IsoDate) =>
  api.post<DeviceMapping>(`/devices/mappings/${mappingId}/close`, {
    effective_to: effectiveTo,
  });

export const syncDevice = (deviceId: number, window?: string) =>
  api.post<IngestReport>(`/devices/${deviceId}/sync`, window ? { window } : {});

export const fetchSyncRuns = (deviceId: number) =>
  api.get<SyncRun[]>(`/devices/${deviceId}/sync-runs`, { query: { limit: 50 } });

export const fetchRawEvents = (deviceId: number, status?: RawEventStatus) =>
  api.get<RawEvent[]>(`/devices/${deviceId}/events`, {
    query: { status: status ?? undefined, limit: 200 },
  });

export const reprocessEvents = (deviceId: number, statuses: RawEventStatus[]) =>
  api.post<{
    device_code: string;
    examined: number;
    mapped: number;
    unmapped: number;
    failed: number;
    duplicate: number;
    days_recomputed: number;
  }>(`/devices/${deviceId}/reprocess`, { statuses, limit: 500 });

export const resolveEvent = (rawEventId: number, employeeId: number, createMapping: boolean) =>
  api.post<{
    raw_event_id: number;
    outcome: string;
    mapping_created: boolean;
    employee_code: string;
  }>(`/devices/events/${rawEventId}/resolve`, {
    employee_id: employeeId,
    create_mapping: createMapping,
  });

/**
 * Multipart upload of a device's exported punch log.
 *
 * Uses `fetch` directly rather than the JSON client: the body is FormData, and
 * the browser must set the multipart boundary itself. Setting Content-Type by
 * hand produces a request the server cannot parse.
 */
export async function uploadPunchLog(deviceId: number, file: File): Promise<IngestReport> {
  const { tokenStore, ApiError } = await import('@/lib/api');
  const form = new FormData();
  form.append('file', file);

  const tokens = tokenStore.get();
  const response = await fetch(
    `${import.meta.env.VITE_API_BASE_URL ?? '/api/v1'}/devices/${deviceId}/upload`,
    {
      method: 'POST',
      headers: tokens ? { Authorization: `Bearer ${tokens.accessToken}` } : {},
      body: form,
    },
  );

  const body = await response.json();
  if (!response.ok) {
    throw new ApiError({
      status: response.status,
      code: body?.error?.code ?? 'http_error',
      message: body?.error?.message ?? 'The file could not be processed.',
      details: body?.error?.details ?? [],
      requestId: response.headers.get('X-Request-ID'),
    });
  }
  // A readable file with unusable rows comes back as a 200 report, not an
  // error - one bad row must not discard the file.
  return body as IngestReport;
}
