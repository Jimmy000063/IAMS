/** One device: how to feed it, who its identifiers belong to, what it has done. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Copy, Inbox, KeyRound, Link2, Plus, X } from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/Button';
import { DatePicker } from '@/components/ui/DatePicker';
import { SelectField, TextAreaField, TextField } from '@/components/ui/Field';
import { Alert, EmptyState, ErrorPanel, SkeletonRows } from '@/components/ui/Feedback';
import { Modal } from '@/components/ui/Modal';
import { Badge, Card, CardBody, CardHeader, TableShell, Td, Th } from '@/components/ui/Surface';
import {
  closeMapping,
  createMapping,
  fetchMappings,
  fetchSyncRuns,
  fetchWebhookInstructions,
  type Device,
} from '@/features/devices/api';
import { deviceKeys } from '@/features/devices/deviceKeys';
import { employeeKeys, fetchEmployees } from '@/features/employees/api';
import { useAuth } from '@/lib/auth';
import { todayIso, type IsoDate } from '@/lib/dates';
import { useToast } from '@/lib/toast';
import { formatDateTime } from '@/lib/utils';

export function DeviceDetail({
  device,
  onClose,
  onReconcile,
}: {
  device: Device;
  onClose: () => void;
  onReconcile: () => void;
}) {
  return (
    <Card>
      <CardHeader
        title={`${device.code} — ${device.name}`}
        description={device.description ?? undefined}
        action={
          <div className="flex gap-2">
            <Button variant="secondary" onClick={onReconcile}>
              <Inbox className="size-4" aria-hidden="true" />
              Reconcile events
            </Button>
            <Button variant="ghost" onClick={onClose} aria-label="Close device detail">
              <X className="size-4" aria-hidden="true" />
            </Button>
          </div>
        }
      />
      <CardBody className="space-y-6">
        <Summary device={device} />
        {device.is_push_adapter && device.adapter_type === 'webhook' && (
          <WebhookInstructions device={device} />
        )}
        <Mappings device={device} />
        <SyncHistory device={device} />
      </CardBody>
    </Card>
  );
}

function Summary({ device }: { device: Device }) {
  const items: { label: string; value: string }[] = [
    { label: 'Adapter', value: device.adapter_type },
    {
      label: 'Direction',
      value:
        device.direction_mode === 'explicit'
          ? 'Reported by the device'
          : device.direction_mode === 'alternate'
            ? 'Inferred by alternating from the previous punch'
            : device.direction_mode === 'always_in'
              ? 'Entry-only reader'
              : 'Exit-only reader',
    },
    {
      label: 'Timestamps interpreted in',
      value: device.timezone_override ?? 'the work location’s zone, then the company default',
    },
    {
      label: 'Future-dated tolerance',
      value: `${device.clock_drift_tolerance_seconds}s — a punch stamped further ahead is refused`,
    },
    {
      label: 'Vendor / model',
      value: [device.vendor, device.model].filter(Boolean).join(' ') || '—',
    },
    { label: 'Serial', value: device.serial_number ?? '—' },
    {
      label: 'Source address allowlist',
      value: device.allowed_source_ips?.join(', ') ?? 'Not set',
    },
    { label: 'Sync cursor', value: device.sync_cursor ?? 'Not yet set' },
  ];

  return (
    <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
      {items.map((item) => (
        <div key={item.label}>
          <dt className="text-ink-soft text-xs font-medium tracking-wide uppercase">
            {item.label}
          </dt>
          <dd className="text-ink mt-0.5 text-sm">{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function WebhookInstructions({ device }: { device: Device }) {
  const toast = useToast();
  const instructions = useQuery({
    queryKey: deviceKeys.webhook(device.id),
    queryFn: () => fetchWebhookInstructions(device.id),
  });

  if (instructions.isPending) return <SkeletonRows rows={2} />;
  if (instructions.isError) {
    return (
      <ErrorPanel
        error={instructions.error}
        title="Could not load the delivery instructions"
        onRetry={() => void instructions.refetch()}
      />
    );
  }

  const data = instructions.data;

  return (
    <div className="border-line rounded-lg border p-4">
      <h3 className="text-ink flex items-center gap-2 text-sm font-semibold">
        <Link2 className="size-4" aria-hidden="true" />
        What to configure on the device
      </h3>

      {!data.secret_configured && (
        <div className="mt-3">
          <Alert variant="danger" title="No signing secret is set" icon={KeyRound}>
            Deliveries will be refused until one is configured. The endpoint never accepts an
            unsigned body.
          </Alert>
        </div>
      )}

      <dl className="mt-3 space-y-2 text-sm">
        <div>
          <dt className="text-ink-soft text-xs font-medium tracking-wide uppercase">URL</dt>
          <dd className="mt-0.5 flex items-center gap-2">
            <code className="bg-sunken text-ink min-w-0 flex-1 overflow-x-auto rounded px-2 py-1 font-mono text-xs">
              {data.method} {data.url}
            </code>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                void navigator.clipboard
                  ?.writeText(data.url)
                  .then(() => toast.success('URL copied.'))
                  .catch(() => toast.error('Could not copy. Select the text instead.'));
              }}
            >
              <Copy className="size-4" aria-hidden="true" />
              Copy
            </Button>
          </dd>
        </div>
        <div>
          <dt className="text-ink-soft text-xs font-medium tracking-wide uppercase">
            Signature
          </dt>
          <dd className="mt-0.5">
            <code className="bg-sunken rounded px-2 py-1 font-mono text-xs">
              {data.signature_header}
            </code>
            <span className="text-ink-soft ml-2">{data.signature_algorithm}</span>
          </dd>
        </div>
      </dl>

      <ul className="text-ink-soft mt-3 list-inside list-disc space-y-1 text-sm">
        {data.notes.map((note) => (
          <li key={note}>{note}</li>
        ))}
      </ul>
    </div>
  );
}

function Mappings({ device }: { device: Device }) {
  const { can } = useAuth();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [closingId, setClosingId] = useState<number | null>(null);

  const mappings = useQuery({
    queryKey: deviceKeys.mappings(device.id),
    queryFn: () => fetchMappings(device.id),
  });

  const employees = useQuery({
    queryKey: employeeKeys.list({ page: 1, pageSize: 200 }),
    queryFn: () => fetchEmployees({ page: 1, page_size: 200 }),
    enabled: adding,
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: deviceKeys.mappings(device.id) });
  };

  const close = useMutation({
    mutationFn: ({ id, effectiveTo }: { id: number; effectiveTo: IsoDate }) =>
      closeMapping(id, effectiveTo),
    onSuccess: () => {
      setClosingId(null);
      invalidate();
      toast.success('Mapping closed. Punches after that date will need a new mapping.');
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-ink text-sm font-semibold">Employee identifiers</h3>
          <p className="text-ink-soft mt-0.5 text-sm">
            Which employee each device identifier refers to, and from when. Dated on purpose:
            enrolment slots get reused, and a punch from last March must resolve to whoever held
            that slot in March.
          </p>
        </div>
        {can('device.manage') && (
          <Button variant="secondary" onClick={() => setAdding(true)}>
            <Plus className="size-4" aria-hidden="true" />
            Map an identifier
          </Button>
        )}
      </div>

      {mappings.isPending ? (
        <SkeletonRows rows={3} />
      ) : mappings.isError ? (
        <ErrorPanel error={mappings.error} onRetry={() => void mappings.refetch()} />
      ) : mappings.data.length === 0 ? (
        <EmptyState
          title="No identifiers mapped yet"
          description="Punches will arrive and be queued as unmapped until an identifier is linked to an employee. Nothing is lost in the meantime."
        />
      ) : (
        <TableShell caption={`Device identifier mappings for ${device.code}`}>
          <thead>
            <tr>
              <Th>Device identifier</Th>
              <Th>Employee</Th>
              <Th>From</Th>
              <Th>Until</Th>
              <Th>Notes</Th>
              {can('device.manage') && <Th className="text-right">Actions</Th>}
            </tr>
          </thead>
          <tbody>
            {mappings.data.map((mapping) => (
              <tr key={mapping.id}>
                <Td>
                  <code className="font-mono text-sm">{mapping.external_employee_ref}</code>
                </Td>
                <Td>#{mapping.employee_id}</Td>
                <Td>{mapping.effective_from}</Td>
                <Td>
                  {mapping.effective_to ? (
                    mapping.effective_to
                  ) : (
                    <Badge tone="success">Open</Badge>
                  )}
                </Td>
                <Td className="text-ink-soft max-w-xs text-sm">{mapping.notes ?? '—'}</Td>
                {can('device.manage') && (
                  <Td className="text-right">
                    {!mapping.effective_to && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setClosingId(mapping.id)}
                      >
                        Close
                      </Button>
                    )}
                  </Td>
                )}
              </tr>
            ))}
          </tbody>
        </TableShell>
      )}

      <AddMappingModal
        open={adding}
        device={device}
        employees={employees.data?.items ?? []}
        onClose={() => setAdding(false)}
        onCreated={() => {
          setAdding(false);
          invalidate();
          toast.success('Identifier mapped.');
        }}
      />

      <CloseMappingModal
        open={closingId !== null}
        onClose={() => setClosingId(null)}
        isPending={close.isPending}
        onConfirm={(effectiveTo) => {
          if (closingId !== null) close.mutate({ id: closingId, effectiveTo });
        }}
      />
    </div>
  );
}

function AddMappingModal({
  open,
  device,
  employees,
  onClose,
  onCreated,
}: {
  open: boolean;
  device: Device;
  employees: { id: number; employee_code: string; full_name: string }[];
  onClose: () => void;
  onCreated: () => void;
}) {
  const toast = useToast();
  const [reference, setReference] = useState('');
  const [employeeId, setEmployeeId] = useState('');
  const [effectiveFrom, setEffectiveFrom] = useState<IsoDate>(todayIso());
  const [notes, setNotes] = useState('');

  const create = useMutation({
    mutationFn: () =>
      createMapping(device.id, {
        external_employee_ref: reference.trim(),
        employee_id: Number(employeeId),
        effective_from: effectiveFrom,
        notes: notes.trim() || null,
      }),
    onSuccess: () => {
      setReference('');
      setEmployeeId('');
      setNotes('');
      onCreated();
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  const fieldErrors = create.error ? extractFieldErrors(create.error) : {};

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Map a device identifier"
      description="The identifier is the device's own enrolment number for the person, exactly as it appears in its payloads."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={() => create.mutate()}
            disabled={!reference.trim() || !employeeId || create.isPending}
          >
            {create.isPending ? 'Saving…' : 'Save mapping'}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <TextField
          label="Device identifier"
          value={reference}
          onChange={(event) => setReference(event.target.value)}
          hint="The enrolment id the device sends, e.g. 47."
          error={fieldErrors['external_employee_ref']}
          required
        />
        <SelectField
          label="Employee"
          value={employeeId}
          onChange={(event) => setEmployeeId(event.target.value)}
          options={[
            { value: '', label: 'Select an employee…' },
            ...employees.map((employee) => ({
              value: String(employee.id),
              label: `${employee.employee_code} — ${employee.full_name}`,
            })),
          ]}
          error={fieldErrors['employee_id']}
          required
        />
        <DatePicker
          label="Effective from"
          value={effectiveFrom}
          onChange={setEffectiveFrom}
          hint="Punches on or after this date resolve to this employee. Set it to the date the slot was actually assigned, not today, so stored punches resolve too."
          {...(fieldErrors['effective_from'] ? { error: fieldErrors['effective_from'] } : {})}
        />
        <TextAreaField
          label="Notes"
          value={notes}
          onChange={(event) => setNotes(event.target.value)}
          rows={2}
          hint="Optional. Useful for recording why a slot changed hands."
        />
      </div>
    </Modal>
  );
}

function CloseMappingModal({
  open,
  onClose,
  onConfirm,
  isPending,
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: (effectiveTo: IsoDate) => void;
  isPending: boolean;
}) {
  const [effectiveTo, setEffectiveTo] = useState<IsoDate>(todayIso());

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Close this mapping"
      description="Punches after this date will no longer resolve to this employee. Existing attendance is untouched."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => onConfirm(effectiveTo)} disabled={isPending}>
            {isPending ? 'Closing…' : 'Close mapping'}
          </Button>
        </>
      }
    >
      <DatePicker
        label="Last date this identifier referred to this employee"
        value={effectiveTo}
        onChange={setEffectiveTo}
      />
    </Modal>
  );
}

function SyncHistory({ device }: { device: Device }) {
  const runs = useQuery({
    queryKey: deviceKeys.syncRuns(device.id),
    queryFn: () => fetchSyncRuns(device.id),
  });

  if (runs.isPending) return <SkeletonRows rows={3} />;
  if (runs.isError) {
    return <ErrorPanel error={runs.error} onRetry={() => void runs.refetch()} />;
  }

  if (runs.data.length === 0) {
    return (
      <div>
        <h3 className="text-ink text-sm font-semibold">Synchronisation log</h3>
        <p className="text-ink-soft mt-1 text-sm">
          Nothing has been received from this device yet.
        </p>
      </div>
    );
  }

  return (
    <div>
      <h3 className="text-ink text-sm font-semibold">Synchronisation log</h3>
      <p className="text-ink-soft mt-0.5 mb-3 text-sm">
        Every attempt is recorded, including failures — a failed run that left no trace would be
        worse than one that fails loudly.
      </p>
      <TableShell caption={`Synchronisation history for ${device.code}`}>
        <thead>
          <tr>
            <Th>Started</Th>
            <Th>Trigger</Th>
            <Th>Outcome</Th>
            <Th>Applied</Th>
            <Th>Needs attention</Th>
            <Th>Detail</Th>
          </tr>
        </thead>
        <tbody>
          {runs.data.map((run) => (
            <tr key={run.id}>
              <Td className="whitespace-nowrap">{formatDateTime(run.started_at)}</Td>
              <Td>{run.trigger}</Td>
              <Td>
                <Badge
                  tone={
                    run.status === 'succeeded'
                      ? 'success'
                      : run.status === 'partial'
                        ? 'warning'
                        : run.status === 'failed'
                          ? 'danger'
                          : 'neutral'
                  }
                >
                  {run.status}
                </Badge>
              </Td>
              <Td>
                {run.events_accepted} of {run.events_fetched}
                {run.events_duplicate > 0 && (
                  <span className="text-ink-muted text-xs">
                    {' '}
                    ({run.events_duplicate} already known)
                  </span>
                )}
              </Td>
              <Td>
                {run.events_unmapped + run.events_failed > 0 ? (
                  <Badge tone="warning">{run.events_unmapped + run.events_failed}</Badge>
                ) : (
                  <span className="text-ink-muted">—</span>
                )}
              </Td>
              <Td className="text-ink-soft max-w-xs text-sm">{run.error_message ?? '—'}</Td>
            </tr>
          ))}
        </tbody>
      </TableShell>
    </div>
  );
}

function extractFieldErrors(error: unknown): Record<string, string> {
  if (error && typeof error === 'object' && 'fieldErrors' in error) {
    return (error as { fieldErrors: Record<string, string> }).fieldErrors;
  }
  return {};
}
