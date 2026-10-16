/**
 * Events a device delivered that could not be used.
 *
 * This screen is why every raw event is stored. A wrong field mapping or a
 * missing employee mapping is correctable after the fact, from here, without
 * asking the device to re-send anything — and nothing a device sent is ever
 * thrown away.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CircleCheck, RotateCw, UserPlus } from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/Button';
import { CheckboxField, SelectField } from '@/components/ui/Field';
import { Alert, EmptyState, ErrorPanel, SkeletonRows } from '@/components/ui/Feedback';
import { Modal } from '@/components/ui/Modal';
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
import {
  fetchDevices,
  fetchRawEvents,
  reprocessEvents,
  resolveEvent,
  type RawEvent,
  type RawEventStatus,
} from '@/features/devices/api';
import { deviceKeys } from '@/features/devices/deviceKeys';
import { employeeKeys, fetchEmployees } from '@/features/employees/api';
import { useAuth } from '@/lib/auth';
import { useToast } from '@/lib/toast';
import { formatDateTime } from '@/lib/utils';

const STATUS_TONES: Record<RawEventStatus, BadgeTone> = {
  pending: 'neutral',
  mapped: 'success',
  duplicate: 'neutral',
  unmapped: 'warning',
  failed: 'danger',
  ignored: 'neutral',
};

const STATUS_MEANING: Record<RawEventStatus, string> = {
  pending: 'Stored, not yet processed.',
  mapped: 'Applied as an attendance punch.',
  duplicate: 'Seen before; skipped rather than double-counted.',
  unmapped: 'No employee is mapped to this device identifier.',
  failed: 'Could not be interpreted or applied.',
  ignored: 'Matched an ignore rule — not an attendance punch.',
};

const FILTERS: { value: string; label: string }[] = [
  { value: 'queue', label: 'Needs attention (unmapped and failed)' },
  { value: 'unmapped', label: 'Unmapped only' },
  { value: 'failed', label: 'Failed only' },
  { value: 'mapped', label: 'Applied' },
  { value: 'duplicate', label: 'Duplicates' },
  { value: 'ignored', label: 'Ignored' },
  { value: 'all', label: 'Everything' },
];

export function ReconciliationQueue({
  focusedDeviceId,
  onFocusChange,
}: {
  focusedDeviceId: number | null;
  onFocusChange: (deviceId: number | null) => void;
}) {
  const { can } = useAuth();
  const toast = useToast();
  const queryClient = useQueryClient();

  const [filter, setFilter] = useState('queue');
  const [resolving, setResolving] = useState<RawEvent | null>(null);

  const devices = useQuery({
    queryKey: deviceKeys.list(true),
    queryFn: () => fetchDevices(true),
  });

  const deviceId = focusedDeviceId ?? devices.data?.[0]?.id ?? null;

  // 'queue' is the useful default view, but the endpoint filters by a single
  // status, so it is fetched as two queries' worth of rows and merged.
  const unmapped = useQuery({
    queryKey: deviceKeys.events(deviceId ?? 0, 'unmapped'),
    queryFn: () => fetchRawEvents(deviceId as number, 'unmapped'),
    enabled: deviceId !== null && (filter === 'queue' || filter === 'unmapped'),
  });

  const failed = useQuery({
    queryKey: deviceKeys.events(deviceId ?? 0, 'failed'),
    queryFn: () => fetchRawEvents(deviceId as number, 'failed'),
    enabled: deviceId !== null && (filter === 'queue' || filter === 'failed'),
  });

  const singleStatus = !['queue', 'unmapped', 'failed', 'all'].includes(filter)
    ? (filter as RawEventStatus)
    : null;

  const others = useQuery({
    queryKey: deviceKeys.events(deviceId ?? 0, singleStatus ?? undefined),
    queryFn: () => fetchRawEvents(deviceId as number, singleStatus ?? undefined),
    enabled: deviceId !== null && (filter === 'all' || singleStatus !== null),
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: deviceKeys.all });
  };

  const reprocess = useMutation({
    mutationFn: () => reprocessEvents(deviceId as number, ['unmapped', 'failed', 'pending']),
    onSuccess: (result) => {
      invalidate();
      if (result.mapped > 0) {
        toast.success(
          `${result.mapped} event(s) applied.`,
          `${result.days_recomputed} attendance day(s) recalculated.`,
        );
      } else {
        toast.show({
          variant: 'warning',
          title: `Examined ${result.examined} event(s); none could be applied.`,
          description:
            'Check the field mapping and the employee identifier mappings, then try again.',
        });
      }
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  if (devices.isPending) return <SkeletonRows rows={4} />;
  if (devices.isError) {
    return <ErrorPanel error={devices.error} onRetry={() => void devices.refetch()} />;
  }

  if (devices.data.length === 0) {
    return (
      <EmptyState
        title="No devices configured"
        description="There is nothing to reconcile until a device has delivered events."
      />
    );
  }

  const rows: RawEvent[] =
    filter === 'queue'
      ? [...(unmapped.data ?? []), ...(failed.data ?? [])].sort((a, b) =>
          b.received_at.localeCompare(a.received_at),
        )
      : filter === 'unmapped'
        ? (unmapped.data ?? [])
        : filter === 'failed'
          ? (failed.data ?? [])
          : (others.data ?? []);

  const isPending =
    unmapped.isFetching || failed.isFetching || others.isFetching || reprocess.isPending;

  return (
    <div className="space-y-5">
      <Alert variant="info" title="Nothing a device sends is discarded">
        Every payload is stored, whether or not it could be used. That is what makes a
        configuration mistake fixable afterwards: correct the mapping, then reprocess the stored
        events — the device does not need to send them again.
      </Alert>

      <Card>
        <CardHeader
          title="Device events"
          description="Unmapped and failed events are the queue. Applied events are shown for reference and cannot be reprocessed, since that would duplicate the punch."
          action={
            can('device.reconcile') && (
              <Button
                variant="secondary"
                onClick={() => reprocess.mutate()}
                disabled={deviceId === null || reprocess.isPending}
              >
                <RotateCw className="size-4" aria-hidden="true" />
                {reprocess.isPending ? 'Reprocessing…' : 'Reprocess the queue'}
              </Button>
            )
          }
        />
        <CardBody className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <SelectField
              label="Device"
              value={deviceId !== null ? String(deviceId) : ''}
              onChange={(event) => onFocusChange(Number(event.target.value))}
              options={devices.data.map((device) => ({
                value: String(device.id),
                label: `${device.code} — ${device.name}`,
              }))}
            />
            <SelectField
              label="Show"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
              options={FILTERS}
            />
          </div>

          {isPending ? (
            <SkeletonRows rows={4} />
          ) : rows.length === 0 ? (
            <EmptyState
              icon={CircleCheck}
              title={
                filter === 'queue' ? 'Nothing needs attention' : 'No events match this filter'
              }
              {...(filter === 'queue'
                ? {
                    description:
                      'Every event this device sent has been applied, recognised as a duplicate, or ignored by a rule.',
                  }
                : {})}
            />
          ) : (
            <TableShell caption="Device events awaiting reconciliation">
              <thead>
                <tr>
                  <Th>Received</Th>
                  <Th>Device identifier</Th>
                  <Th>Punch</Th>
                  <Th>Outcome</Th>
                  <Th>Why</Th>
                  {can('device.reconcile') && <Th className="text-right">Actions</Th>}
                </tr>
              </thead>
              <tbody>
                {rows.map((event) => (
                  <tr key={event.id}>
                    <Td className="whitespace-nowrap">{formatDateTime(event.received_at)}</Td>
                    <Td>
                      <code className="font-mono text-sm">
                        {event.external_employee_ref ?? '—'}
                      </code>
                      {event.employee_id && (
                        <p className="text-ink-muted text-xs">employee #{event.employee_id}</p>
                      )}
                    </Td>
                    <Td>
                      {event.parsed_occurred_at ? (
                        <>
                          <span className="text-sm">
                            {formatDateTime(event.parsed_occurred_at)}
                          </span>
                          {event.parsed_direction && (
                            <p className="text-ink-muted text-xs">{event.parsed_direction}</p>
                          )}
                        </>
                      ) : (
                        <>
                          <span className="text-ink-muted text-sm">Not parsed</span>
                          {event.raw_timestamp && (
                            <p className="text-ink-muted font-mono text-xs">
                              sent: {event.raw_timestamp}
                            </p>
                          )}
                        </>
                      )}
                    </Td>
                    <Td>
                      <Badge tone={STATUS_TONES[event.status]}>{event.status}</Badge>
                      <p className="text-ink-muted mt-1 max-w-xs text-xs">
                        {STATUS_MEANING[event.status]}
                      </p>
                    </Td>
                    <Td className="text-ink-soft max-w-sm text-sm">
                      {event.error_message ?? event.resolution_note ?? '—'}
                      {event.process_attempts > 1 && (
                        <p className="text-ink-muted text-xs">
                          {event.process_attempts} attempts
                        </p>
                      )}
                    </Td>
                    {can('device.reconcile') && (
                      <Td className="text-right">
                        {['unmapped', 'failed', 'pending'].includes(event.status) && (
                          <Button variant="ghost" size="sm" onClick={() => setResolving(event)}>
                            <UserPlus className="size-4" aria-hidden="true" />
                            Attribute
                          </Button>
                        )}
                      </Td>
                    )}
                  </tr>
                ))}
              </tbody>
            </TableShell>
          )}
        </CardBody>
      </Card>

      <ResolveModal
        event={resolving}
        onClose={() => setResolving(null)}
        onResolved={(outcome) => {
          setResolving(null);
          invalidate();
          if (outcome === 'mapped') {
            toast.success('Event attributed and applied.');
          } else {
            toast.show({
              variant: 'warning',
              title: `The event is now ${outcome}.`,
              description:
                'It could not be applied even with an employee attached — check the reason on the row.',
            });
          }
        }}
      />
    </div>
  );
}

function ResolveModal({
  event,
  onClose,
  onResolved,
}: {
  event: RawEvent | null;
  onClose: () => void;
  onResolved: (outcome: string) => void;
}) {
  const toast = useToast();
  const [employeeId, setEmployeeId] = useState('');
  const [learnMapping, setLearnMapping] = useState(true);

  const employees = useQuery({
    queryKey: employeeKeys.list({ page: 1, pageSize: 200 }),
    queryFn: () => fetchEmployees({ page: 1, page_size: 200 }),
    enabled: event !== null,
  });

  const resolve = useMutation({
    mutationFn: () => resolveEvent(event!.id, Number(employeeId), learnMapping),
    onSuccess: (result) => {
      setEmployeeId('');
      onResolved(result.outcome);
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  return (
    <Modal
      open={event !== null}
      onClose={onClose}
      title="Attribute this event"
      description={
        event
          ? `Device identifier ${event.external_employee_ref ?? 'unknown'}, sent ${event.raw_timestamp ?? 'with no readable timestamp'}.`
          : undefined
      }
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => resolve.mutate()} disabled={!employeeId || resolve.isPending}>
            {resolve.isPending ? 'Applying…' : 'Attribute'}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <SelectField
          label="Employee"
          value={employeeId}
          onChange={(value) => setEmployeeId(value.target.value)}
          options={[
            { value: '', label: 'Select an employee…' },
            ...(employees.data?.items ?? []).map((employee) => ({
              value: String(employee.id),
              label: `${employee.employee_code} — ${employee.full_name}`,
            })),
          ]}
          required
        />
        <CheckboxField
          label="Also remember this identifier for future punches"
          checked={learnMapping}
          onChange={(changeEvent) => setLearnMapping(changeEvent.target.checked)}
          hint="Records the mapping from this punch's own date, so other stored events for the same identifier become resolvable and future punches need no intervention."
        />
        {event?.raw_payload && (
          <details>
            <summary className="text-brand-700 cursor-pointer text-sm font-medium">
              What the device actually sent
            </summary>
            <pre className="bg-sunken text-ink-soft mt-2 overflow-x-auto rounded p-3 font-mono text-xs">
              {JSON.stringify(event.raw_payload, null, 2)}
            </pre>
          </details>
        )}
      </div>
    </Modal>
  );
}
