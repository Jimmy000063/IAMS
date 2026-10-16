/**
 * Register a device.
 *
 * The form is driven by the adapter catalogue rather than hard-coded per
 * adapter, so adding an adapter on the backend surfaces its settings here
 * without a frontend change. The field mapping is built explicitly - including
 * the timestamp format, which is chosen and never guessed.
 */

import { useMutation, useQuery } from '@tanstack/react-query';
import { KeyRound } from 'lucide-react';
import { useMemo, useState } from 'react';

import { Button } from '@/components/ui/Button';
import { CheckboxField, SelectField, TextField } from '@/components/ui/Field';
import { Alert } from '@/components/ui/Feedback';
import { Modal } from '@/components/ui/Modal';
import {
  createDevice,
  fetchAdapterCatalogue,
  type AdapterType,
  type Device,
  type DirectionMode,
} from '@/features/devices/api';
import { deviceKeys } from '@/features/devices/deviceKeys';
import {
  INITIAL_DEVICE_FORM,
  buildDevicePayload,
  randomWebhookSecret,
  type DeviceFormState,
} from '@/features/devices/devicePayload';
import { fetchWorkLocations, orgKeys } from '@/features/employees/api';
import { useToast } from '@/lib/toast';

const DIRECTION_MODES: { value: DirectionMode; label: string; hint: string }[] = [
  {
    value: 'alternate',
    label: 'Infer by alternating',
    hint: 'The device only reports that a punch happened. The first is an entry, the next an exit, and so on.',
  },
  {
    value: 'explicit',
    label: 'The device reports in or out',
    hint: 'An unrecognised value is refused rather than guessed, so a mapping gap gets fixed instead of papered over.',
  },
  { value: 'always_in', label: 'Entry-only reader', hint: 'An entry turnstile.' },
  { value: 'always_out', label: 'Exit-only reader', hint: 'An exit gate.' },
];

export function DeviceForm({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (device: Device) => void;
}) {
  const toast = useToast();
  const [form, setForm] = useState<DeviceFormState>(INITIAL_DEVICE_FORM);

  const catalogue = useQuery({
    queryKey: deviceKeys.adapters(),
    queryFn: fetchAdapterCatalogue,
    enabled: open,
    staleTime: 10 * 60 * 1000,
  });

  const locations = useQuery({
    queryKey: orgKeys.locations(),
    queryFn: () => fetchWorkLocations(false),
    enabled: open,
  });

  const adapter = useMemo(
    () =>
      catalogue.data?.adapters.find((item) => item.adapter_type === form.adapterType) ?? null,
    [catalogue.data, form.adapterType],
  );

  const set = <K extends keyof DeviceFormState>(key: K, value: DeviceFormState[K]) =>
    setForm((previous) => ({ ...previous, [key]: value }));

  const create = useMutation({
    mutationFn: () => createDevice(buildDevicePayload(form)),
    onSuccess: (device) => {
      setForm(INITIAL_DEVICE_FORM);
      onCreated(device);
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  const fieldErrors =
    create.error && typeof create.error === 'object' && 'fieldErrors' in create.error
      ? (create.error as { fieldErrors: Record<string, string> }).fieldErrors
      : {};

  const needsWebhookSecret = adapter?.requires_webhook_secret ?? false;
  const canSubmit =
    form.code.trim() !== '' &&
    form.name.trim() !== '' &&
    (!needsWebhookSecret || form.webhookSecret.length >= 16) &&
    (form.adapterType !== 'rest_poll' || form.url.trim() !== '');

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Register a device"
      description="The configuration is validated now rather than at the first sync, so a mistake surfaces while you are still looking at it."
      className="max-w-3xl"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => create.mutate()} disabled={!canSubmit || create.isPending}>
            {create.isPending ? 'Registering…' : 'Register device'}
          </Button>
        </>
      }
    >
      <div className="space-y-5">
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField
            label="Code"
            value={form.code}
            onChange={(event) => set('code', event.target.value.toUpperCase())}
            hint="Short identifier, e.g. GATE-1. Used in the webhook URL."
            error={fieldErrors['code']}
            required
          />
          <TextField
            label="Name"
            value={form.name}
            onChange={(event) => set('name', event.target.value)}
            hint="What a person would call it, e.g. Main gate reader."
            error={fieldErrors['name']}
            required
          />
        </div>

        <SelectField
          label="How punches arrive"
          value={form.adapterType}
          onChange={(event) => set('adapterType', event.target.value as AdapterType)}
          options={(catalogue.data?.adapters ?? []).map((item) => ({
            value: item.adapter_type,
            label: `${item.name}${item.maturity === 'tested' ? '' : ` (${item.maturity.replace('_', ' ')})`}`,
          }))}
          hint={adapter?.summary}
        />

        {adapter && adapter.maturity !== 'tested' && (
          <Alert
            variant={adapter.maturity === 'development_only' ? 'danger' : 'warning'}
            title={
              adapter.maturity === 'development_only'
                ? 'Development only'
                : 'Not verified against hardware'
            }
          >
            {adapter.maturity_meaning}
          </Alert>
        )}

        <div className="grid gap-4 sm:grid-cols-2">
          <SelectField
            label="Direction of each punch"
            value={form.directionMode}
            onChange={(event) => set('directionMode', event.target.value as DirectionMode)}
            options={DIRECTION_MODES.map((mode) => ({ value: mode.value, label: mode.label }))}
            hint={DIRECTION_MODES.find((mode) => mode.value === form.directionMode)?.hint}
          />
          <SelectField
            label="Work location"
            value={form.workLocationId}
            onChange={(event) => set('workLocationId', event.target.value)}
            options={[
              { value: '', label: 'Not tied to a location' },
              ...(locations.data ?? []).map((location) => ({
                value: String(location.id),
                label: `${location.code} — ${location.name}`,
              })),
            ]}
            hint="Decides the timezone its naive timestamps are read in, unless overridden below."
          />
        </div>

        {needsWebhookSecret && (
          <div className="border-line rounded-lg border p-4">
            <h3 className="text-ink flex items-center gap-2 text-sm font-semibold">
              <KeyRound className="size-4" aria-hidden="true" />
              Webhook signing secret
            </h3>
            <p className="text-ink-soft mt-1 text-sm">
              The device signs each delivery with this. It is the only authentication on that
              endpoint, it is stored encrypted, and it cannot be read back afterwards — keep a
              copy where you configure the device.
            </p>
            <div className="mt-3 flex items-end gap-2">
              <div className="flex-1">
                <TextField
                  label="Secret"
                  value={form.webhookSecret}
                  onChange={(event) => set('webhookSecret', event.target.value)}
                  hint="At least 16 characters."
                  error={fieldErrors['webhook_secret']}
                  required
                />
              </div>
              <Button
                variant="secondary"
                onClick={() => set('webhookSecret', randomWebhookSecret())}
              >
                Generate
              </Button>
            </div>
          </div>
        )}

        {form.adapterType === 'rest_poll' && (
          <div className="space-y-4">
            <TextField
              label="Endpoint URL"
              value={form.url}
              onChange={(event) => set('url', event.target.value)}
              hint="May contain {cursor} and {limit}, substituted on each poll."
              error={fieldErrors['url']}
              required
            />
            <div className="grid gap-4 sm:grid-cols-2">
              <SelectField
                label="Authentication"
                value={form.authMode}
                onChange={(event) => set('authMode', event.target.value)}
                options={[
                  { value: 'none', label: 'None' },
                  { value: 'bearer', label: 'Bearer token' },
                  { value: 'header', label: 'Custom header' },
                  { value: 'query', label: 'Query parameter' },
                ]}
              />
              {form.authMode !== 'none' && (
                <TextField
                  label="Credential"
                  value={form.apiSecret}
                  onChange={(event) => set('apiSecret', event.target.value)}
                  hint="Stored encrypted and never shown again."
                />
              )}
            </div>
            <CheckboxField
              label="This device is on our own network"
              checked={form.allowPrivateNetwork}
              onChange={(event) => set('allowPrivateNetwork', event.target.checked)}
              hint="Required for a LAN address. Without it, a private or loopback address is refused — on an internet endpoint that would indicate a misconfiguration or a request-forgery attempt. Cloud metadata addresses are refused either way."
            />
          </div>
        )}

        {form.adapterType === 'csv_upload' && (
          <TextField
            label="Column delimiter"
            value={form.delimiter}
            onChange={(event) => set('delimiter', event.target.value)}
            maxLength={1}
            hint="A comma for most exports. Use ';' or a tab if yours differs."
          />
        )}

        <div className="border-line rounded-lg border p-4">
          <h3 className="text-ink text-sm font-semibold">Field mapping</h3>
          <p className="text-ink-soft mt-1 text-sm">
            Which fields in the device's payload mean what. Dotted paths work for nested data,
            e.g. <code>data.user.id</code>.
          </p>
          <div className="mt-3 grid gap-4 sm:grid-cols-2">
            <TextField
              label="Employee identifier field"
              value={form.employeeRef}
              onChange={(event) => set('employeeRef', event.target.value)}
              error={fieldErrors['employee_ref']}
              required
            />
            <TextField
              label="Timestamp field"
              value={form.timestampField}
              onChange={(event) => set('timestampField', event.target.value)}
              required
            />
            <SelectField
              label="Timestamp format"
              value={form.timestampFormat}
              onChange={(event) => set('timestampFormat', event.target.value)}
              options={(catalogue.data?.timestamp_formats ?? []).map((format) => ({
                value: format,
                label: format,
              }))}
              hint="Stated, never guessed: 03/04/2026 is a different day under a day-first and a month-first reading."
              error={fieldErrors['timestamp_format']}
            />
            <TextField
              label="Event id field"
              value={form.eventIdField}
              onChange={(event) => set('eventIdField', event.target.value)}
              hint="Strongly recommended. Without one, an id is derived from the content — which still deduplicates, but cannot tell two genuinely identical punches apart."
            />
            {form.directionMode === 'explicit' && (
              <TextField
                label="Direction field"
                value={form.directionField}
                onChange={(event) => set('directionField', event.target.value)}
                hint="The field saying whether the punch was an entry or an exit."
                required
              />
            )}
            <TextField
              label="Timezone override"
              value={form.timezoneOverride}
              onChange={(event) => set('timezoneOverride', event.target.value)}
              hint="An IANA zone such as Asia/Kolkata. Leave empty to use the work location's. Abbreviations like 'IST' are refused — they are ambiguous."
              error={fieldErrors['timezone_override']}
            />
          </div>
        </div>

        <details>
          <summary className="text-brand-700 cursor-pointer text-sm font-medium">
            Hardware details (optional)
          </summary>
          <div className="mt-3 grid gap-4 sm:grid-cols-3">
            <TextField
              label="Vendor"
              value={form.vendor}
              onChange={(event) => set('vendor', event.target.value)}
            />
            <TextField
              label="Model"
              value={form.model}
              onChange={(event) => set('model', event.target.value)}
            />
            <TextField
              label="Serial number"
              value={form.serialNumber}
              onChange={(event) => set('serialNumber', event.target.value)}
            />
          </div>
          <p className="text-ink-muted mt-2 text-xs">
            Recorded for your own asset tracking. Naming a vendor here does not change how
            punches are read — no adapter is written against a specific product.
          </p>
        </details>
      </div>
    </Modal>
  );
}
