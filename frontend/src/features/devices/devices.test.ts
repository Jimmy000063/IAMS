/**
 * The device form's payload builder.
 *
 * Worth testing because the failure is quiet: sending `direction: ""` or
 * `event_id: ""` would have the backend look for a field named empty-string,
 * find nothing, and treat every punch as having no direction or no id. An
 * omitted key behaves correctly; a blank one does not.
 */

import { describe, expect, it } from 'vitest';

import { INITIAL_DEVICE_FORM, buildDevicePayload } from '@/features/devices/devicePayload';

describe('buildDevicePayload', () => {
  it('omits optional mapping fields rather than sending them blank', () => {
    const payload = buildDevicePayload({
      ...INITIAL_DEVICE_FORM,
      code: 'GATE1',
      name: 'Main gate',
      eventIdField: '',
      directionField: '',
    });

    const mapping = (payload.config as { field_mapping: Record<string, unknown> })
      .field_mapping;
    expect(mapping).not.toHaveProperty('event_id');
    expect(mapping).not.toHaveProperty('direction');
    expect(mapping['employee_ref']).toBe('user_id');
    expect(mapping['timestamp_format']).toBe('iso8601');
  });

  it('includes the mapping fields that were filled in', () => {
    const payload = buildDevicePayload({
      ...INITIAL_DEVICE_FORM,
      code: 'GATE1',
      name: 'Main gate',
      directionMode: 'explicit',
      directionField: 'InOut',
      eventIdField: 'id',
      timestampFormat: 'dmy_hms_slash',
    });

    const mapping = (payload.config as { field_mapping: Record<string, unknown> })
      .field_mapping;
    expect(mapping['direction']).toBe('InOut');
    expect(mapping['event_id']).toBe('id');
    expect(mapping['timestamp_format']).toBe('dmy_hms_slash');
  });

  it('trims whitespace so a stray space cannot break the mapping', () => {
    const payload = buildDevicePayload({
      ...INITIAL_DEVICE_FORM,
      code: '  GATE1  ',
      name: '  Main gate  ',
      employeeRef: ' user_id ',
      timestampField: ' punch_time ',
    });

    expect(payload.code).toBe('GATE1');
    expect(payload.name).toBe('Main gate');
    const mapping = (payload.config as { field_mapping: Record<string, unknown> })
      .field_mapping;
    expect(mapping['employee_ref']).toBe('user_id');
    expect(mapping['timestamp']).toBe('punch_time');
  });

  it('omits a secret that was never entered, instead of clearing one', () => {
    const payload = buildDevicePayload({
      ...INITIAL_DEVICE_FORM,
      code: 'GATE1',
      name: 'Main gate',
      webhookSecret: '',
      apiSecret: '',
    });

    expect(payload).not.toHaveProperty('webhook_secret');
    expect(payload).not.toHaveProperty('api_secret');
  });

  it('sends polling settings only for the polling adapter', () => {
    const polled = buildDevicePayload({
      ...INITIAL_DEVICE_FORM,
      code: 'POLL',
      name: 'Polled reader',
      adapterType: 'rest_poll',
      url: 'http://192.168.1.50/api/punches',
      authMode: 'bearer',
      allowPrivateNetwork: true,
    });
    expect(polled.config).toMatchObject({
      url: 'http://192.168.1.50/api/punches',
      auth_mode: 'bearer',
      allow_private_network: true,
    });

    const pushed = buildDevicePayload({
      ...INITIAL_DEVICE_FORM,
      code: 'GATE1',
      name: 'Main gate',
      adapterType: 'webhook',
      url: 'http://leftover.example.com',
    });
    expect(pushed.config).not.toHaveProperty('url');
  });

  it('defaults a cleared CSV delimiter to a comma', () => {
    const payload = buildDevicePayload({
      ...INITIAL_DEVICE_FORM,
      code: 'EXPORT',
      name: 'Monthly export',
      adapterType: 'csv_upload',
      delimiter: '',
    });
    expect(payload.config).toMatchObject({ delimiter: ',' });
  });

  it('omits an empty timezone override so the location’s zone is used', () => {
    const payload = buildDevicePayload({
      ...INITIAL_DEVICE_FORM,
      code: 'GATE1',
      name: 'Main gate',
      timezoneOverride: '   ',
    });
    expect(payload).not.toHaveProperty('timezone_override');
  });
});
