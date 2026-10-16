/**
 * The device form's state shape and the payload it builds.
 *
 * Kept out of the component file so it can be tested directly, and so the
 * component module exports only components.
 */

import type { AdapterType, DirectionMode } from '@/features/devices/api';

export interface DeviceFormState {
  code: string;
  name: string;
  adapterType: AdapterType;
  directionMode: DirectionMode;
  workLocationId: string;
  timezoneOverride: string;
  vendor: string;
  model: string;
  serialNumber: string;
  webhookSecret: string;
  apiSecret: string;
  // Field mapping
  employeeRef: string;
  timestampField: string;
  timestampFormat: string;
  directionField: string;
  eventIdField: string;
  // rest_poll
  url: string;
  authMode: string;
  allowPrivateNetwork: boolean;
  // csv_upload
  delimiter: string;
}

export const INITIAL_DEVICE_FORM: DeviceFormState = {
  code: '',
  name: '',
  adapterType: 'webhook',
  directionMode: 'alternate',
  workLocationId: '',
  timezoneOverride: '',
  vendor: '',
  model: '',
  serialNumber: '',
  webhookSecret: '',
  apiSecret: '',
  employeeRef: 'user_id',
  timestampField: 'timestamp',
  timestampFormat: 'iso8601',
  directionField: '',
  eventIdField: '',
  url: '',
  authMode: 'none',
  allowPrivateNetwork: false,
  delimiter: ',',
};

/**
 * Turn the form state into the request body.
 *
 * An unfilled optional field is **omitted**, never sent blank. Sending
 * `direction: ""` would have the backend look for a field named empty-string,
 * find nothing, and treat every punch as having no direction - a failure that
 * is invisible until someone checks a day's in/out sequence.
 */
export function buildDevicePayload(form: DeviceFormState) {
  const fieldMapping: Record<string, unknown> = {
    employee_ref: form.employeeRef.trim(),
    timestamp: form.timestampField.trim(),
    timestamp_format: form.timestampFormat,
  };
  if (form.eventIdField.trim()) fieldMapping['event_id'] = form.eventIdField.trim();
  if (form.directionField.trim()) fieldMapping['direction'] = form.directionField.trim();

  const config: Record<string, unknown> = { field_mapping: fieldMapping };

  // Adapter-specific settings are sent only for the adapter they belong to, so
  // switching adapter in the form cannot leave a stale URL in the config.
  if (form.adapterType === 'rest_poll') {
    config['url'] = form.url.trim();
    config['auth_mode'] = form.authMode;
    config['allow_private_network'] = form.allowPrivateNetwork;
  }
  if (form.adapterType === 'csv_upload') {
    config['delimiter'] = form.delimiter || ',';
  }

  return {
    code: form.code.trim(),
    name: form.name.trim(),
    adapter_type: form.adapterType,
    direction_mode: form.directionMode,
    config,
    ...(form.workLocationId ? { work_location_id: Number(form.workLocationId) } : {}),
    ...(form.timezoneOverride.trim()
      ? { timezone_override: form.timezoneOverride.trim() }
      : {}),
    ...(form.vendor.trim() ? { vendor: form.vendor.trim() } : {}),
    ...(form.model.trim() ? { model: form.model.trim() } : {}),
    ...(form.serialNumber.trim() ? { serial_number: form.serialNumber.trim() } : {}),
    ...(form.webhookSecret ? { webhook_secret: form.webhookSecret } : {}),
    ...(form.apiSecret ? { api_secret: form.apiSecret } : {}),
  };
}

/**
 * A signing secret from the browser's CSPRNG.
 *
 * `crypto.getRandomValues`, not `Math.random`: this value is the only thing
 * standing between the webhook endpoint and anyone who knows its URL.
 */
export function randomWebhookSecret(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}
