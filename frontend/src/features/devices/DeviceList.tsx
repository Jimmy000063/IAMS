/** Device register, with the detail panel for feeding data in. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  CircleCheck,
  CircleSlash,
  Plus,
  RefreshCw,
  Router,
  TriangleAlert,
  Upload,
} from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/Button';
import { CheckboxField } from '@/components/ui/Field';
import { EmptyState, ErrorPanel, SkeletonRows } from '@/components/ui/Feedback';
import {
  Badge,
  Card,
  CardBody,
  CardHeader,
  TableShell,
  Td,
  Th,
  type BadgeTone,
} from '@/components/ui/Surface';
import { DeviceDetail } from '@/features/devices/DeviceDetail';
import { DeviceForm } from '@/features/devices/DeviceForm';
import { IngestReportPanel } from '@/features/devices/IngestReportPanel';
import {
  fetchDevices,
  syncDevice,
  uploadPunchLog,
  type Device,
  type IngestReport,
} from '@/features/devices/api';
import { deviceKeys } from '@/features/devices/deviceKeys';
import { useAuth } from '@/lib/auth';
import { useToast } from '@/lib/toast';
import { formatDateTime } from '@/lib/utils';

const SYNC_TONES: Record<string, BadgeTone> = {
  succeeded: 'success',
  partial: 'warning',
  failed: 'danger',
  running: 'neutral',
};

export function DeviceList({
  focusedDeviceId,
  onFocusChange,
  onReconcile,
}: {
  focusedDeviceId: number | null;
  onFocusChange: (deviceId: number | null) => void;
  onReconcile: (deviceId: number) => void;
}) {
  const { can } = useAuth();
  const toast = useToast();
  const queryClient = useQueryClient();

  const [includeInactive, setIncludeInactive] = useState(false);
  const [creating, setCreating] = useState(false);
  const [report, setReport] = useState<IngestReport | null>(null);

  const devices = useQuery({
    queryKey: deviceKeys.list(includeInactive),
    queryFn: () => fetchDevices(includeInactive),
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: deviceKeys.all });
  };

  const sync = useMutation({
    mutationFn: (deviceId: number) => syncDevice(deviceId),
    onSuccess: (result) => {
      setReport(result);
      invalidate();
      // A failed run still returns 200 with status 'failed', because the record
      // of the failure has to survive. Reflect that honestly here.
      if (result.status === 'failed') {
        toast.error(
          `${result.device_code}: synchronisation failed.`,
          result.problems[0]?.reason,
        );
      } else if (result.status === 'partial') {
        toast.show({
          variant: 'warning',
          title: `${result.device_code}: ${result.accepted} applied`,
          description: `${result.unmapped + result.failed} event(s) need attention.`,
        });
      } else {
        toast.success(`${result.device_code}: ${result.accepted} new punch(es).`);
      }
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  const upload = useMutation({
    mutationFn: ({ deviceId, file }: { deviceId: number; file: File }) =>
      uploadPunchLog(deviceId, file),
    onSuccess: (result) => {
      setReport(result);
      invalidate();
      if (result.status === 'failed') {
        toast.error(
          `${result.device_code}: the file could not be used.`,
          result.problems[0]?.reason,
        );
      } else {
        toast.success(
          `${result.device_code}: ${result.accepted} applied, ${result.duplicate} already known.`,
        );
      }
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  if (devices.isPending) return <SkeletonRows rows={5} />;
  if (devices.isError) {
    return <ErrorPanel error={devices.error} onRetry={() => void devices.refetch()} />;
  }

  const rows = devices.data;
  const focused = rows.find((device) => device.id === focusedDeviceId) ?? null;

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader
          title="Configured devices"
          description="Each device's adapter decides how punches arrive: it pushes to a signed webhook, we poll it, or you upload its export."
          action={
            <div className="flex flex-wrap items-center gap-3">
              <CheckboxField
                label="Show inactive"
                checked={includeInactive}
                onChange={(event) => setIncludeInactive(event.target.checked)}
              />
              {can('device.manage') && (
                <Button onClick={() => setCreating(true)}>
                  <Plus className="size-4" aria-hidden="true" />
                  Add device
                </Button>
              )}
            </div>
          }
        />
        <CardBody>
          {rows.length === 0 ? (
            <EmptyState
              icon={Router}
              title="No devices configured"
              description="Attendance works without one. Add a device when you want punches to arrive from hardware as well as the browser."
              {...(can('device.manage')
                ? { action: <Button onClick={() => setCreating(true)}>Add a device</Button> }
                : {})}
            />
          ) : (
            <TableShell caption="Configured attendance devices">
              <thead>
                <tr>
                  <Th>Device</Th>
                  <Th>How data arrives</Th>
                  <Th>Last heard from</Th>
                  <Th>Last run</Th>
                  <Th className="text-right">Actions</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((device) => (
                  <DeviceRow
                    key={device.id}
                    device={device}
                    isFocused={device.id === focusedDeviceId}
                    canSync={can('device.sync')}
                    isBusy={
                      (sync.isPending && sync.variables === device.id) ||
                      (upload.isPending && upload.variables?.deviceId === device.id)
                    }
                    onOpen={() =>
                      onFocusChange(device.id === focusedDeviceId ? null : device.id)
                    }
                    onSync={() => sync.mutate(device.id)}
                    onUpload={(file) => upload.mutate({ deviceId: device.id, file })}
                  />
                ))}
              </tbody>
            </TableShell>
          )}
        </CardBody>
      </Card>

      {report && <IngestReportPanel report={report} onDismiss={() => setReport(null)} />}

      {focused && (
        <DeviceDetail
          device={focused}
          onClose={() => onFocusChange(null)}
          onReconcile={() => onReconcile(focused.id)}
        />
      )}

      <DeviceForm
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={(device) => {
          setCreating(false);
          invalidate();
          onFocusChange(device.id);
          toast.success(`${device.code} registered.`);
        }}
      />
    </div>
  );
}

function DeviceRow({
  device,
  isFocused,
  canSync,
  isBusy,
  onOpen,
  onSync,
  onUpload,
}: {
  device: Device;
  isFocused: boolean;
  canSync: boolean;
  isBusy: boolean;
  onOpen: () => void;
  onSync: () => void;
  onUpload: (file: File) => void;
}) {
  const isUpload = device.adapter_type === 'csv_upload';
  const isPolled = !device.is_push_adapter;

  return (
    <tr className={isFocused ? 'bg-brand-50/50' : undefined}>
      <Td>
        <button
          type="button"
          onClick={onOpen}
          className="text-brand-700 font-medium underline underline-offset-2"
          aria-expanded={isFocused}
        >
          {device.code}
        </button>
        <p className="text-ink-soft text-xs">{device.name}</p>
        <div className="mt-1 flex flex-wrap gap-1">
          {!device.is_active && <Badge tone="neutral">Inactive</Badge>}
          {device.consecutive_failures > 0 && (
            <Badge tone="danger">{device.consecutive_failures} failures</Badge>
          )}
        </div>
      </Td>
      <Td>
        <p className="text-sm">
          {isUpload
            ? 'You upload its export'
            : device.is_push_adapter
              ? 'It posts to a signed webhook'
              : 'We poll it'}
        </p>
        <p className="text-ink-muted text-xs">
          {device.direction_mode === 'explicit'
            ? 'Device reports in/out'
            : device.direction_mode === 'alternate'
              ? 'In/out inferred by alternating'
              : device.direction_mode === 'always_in'
                ? 'Entry-only reader'
                : 'Exit-only reader'}
        </p>
      </Td>
      <Td>
        {device.last_seen_at ? (
          <span className="text-sm">{formatDateTime(device.last_seen_at)}</span>
        ) : (
          <Badge tone="warning">
            <TriangleAlert className="size-3.5" aria-hidden="true" />
            Never
          </Badge>
        )}
      </Td>
      <Td>
        {device.last_sync_status ? (
          <Badge tone={SYNC_TONES[device.last_sync_status] ?? 'neutral'}>
            {device.last_sync_status === 'succeeded' ? (
              <CircleCheck className="size-3.5" aria-hidden="true" />
            ) : (
              <CircleSlash className="size-3.5" aria-hidden="true" />
            )}
            {device.last_sync_status}
          </Badge>
        ) : (
          <span className="text-ink-muted text-sm">—</span>
        )}
        {device.last_sync_at && (
          <p className="text-ink-muted mt-1 text-xs">{formatDateTime(device.last_sync_at)}</p>
        )}
      </Td>
      <Td className="text-right">
        <div className="flex flex-wrap justify-end gap-2">
          {canSync && isUpload && (
            <label className="inline-flex">
              <span className="sr-only">Upload a punch log for {device.code}</span>
              <input
                type="file"
                accept=".csv,.txt,text/csv,text/plain"
                className="sr-only"
                disabled={isBusy}
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) onUpload(file);
                  // Reset so the same file can be chosen twice; a re-upload is
                  // safe because overlapping punches deduplicate.
                  event.target.value = '';
                }}
              />
              <span
                className={
                  'border-line-strong text-ink hover:bg-sunken inline-flex cursor-pointer items-center gap-2 rounded-md border px-3 py-1.5 text-sm font-medium'
                }
              >
                <Upload className="size-4" aria-hidden="true" />
                Upload log
              </span>
            </label>
          )}
          {canSync && isPolled && (
            <Button variant="secondary" onClick={onSync} disabled={isBusy || !device.is_active}>
              <RefreshCw className="size-4" aria-hidden="true" />
              {isBusy ? 'Polling…' : 'Poll now'}
            </Button>
          )}
        </div>
      </Td>
    </tr>
  );
}
