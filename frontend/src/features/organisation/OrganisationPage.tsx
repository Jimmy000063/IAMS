import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Building2, Globe, IdCard, Pencil, Plus } from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/Button';
import { CheckboxField, SelectField, TextField } from '@/components/ui/Field';
import { Alert, EmptyState, ErrorPanel, SkeletonRows } from '@/components/ui/Feedback';
import { Modal } from '@/components/ui/Modal';
import { Badge, Card, CardBody, PageHeader, TableShell, Td, Th } from '@/components/ui/Surface';
import {
  createDepartment,
  createDesignation,
  createWorkLocation,
  fetchDepartments,
  fetchDesignations,
  fetchWorkLocations,
  orgKeys,
  updateDepartment,
  updateDesignation,
  updateWorkLocation,
  type Department,
  type Designation,
  type WorkLocation,
} from '@/features/employees/api';
import { ApiError } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useToast } from '@/lib/toast';

type Tab = 'departments' | 'designations' | 'locations';

const TABS: { id: Tab; label: string; icon: typeof Building2 }[] = [
  { id: 'departments', label: 'Departments', icon: Building2 },
  { id: 'designations', label: 'Designations', icon: IdCard },
  { id: 'locations', label: 'Work locations', icon: Globe },
];

export function OrganisationPage() {
  const { can } = useAuth();
  const canManage = can('org.manage');
  const [tab, setTab] = useState<Tab>('departments');
  const [showInactive, setShowInactive] = useState(false);

  return (
    <div>
      <PageHeader
        title="Organisation"
        description="Departments, designations and work locations. Records are deactivated rather than deleted, so employee history keeps resolving."
      />

      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div
          className="border-line flex gap-1 rounded-lg border p-1"
          role="tablist"
          aria-label="Organisation sections"
        >
          {TABS.map((entry) => {
            const Icon = entry.icon;
            const isActive = tab === entry.id;
            return (
              <button
                key={entry.id}
                type="button"
                role="tab"
                aria-selected={isActive}
                onClick={() => setTab(entry.id)}
                className={
                  isActive
                    ? 'bg-brand-600 inline-flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium text-white'
                    : 'text-ink-soft hover:bg-sunken inline-flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium transition-colors'
                }
              >
                <Icon className="size-4" aria-hidden="true" />
                {entry.label}
              </button>
            );
          })}
        </div>

        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            className="border-line-strong accent-brand-600 size-4 rounded"
            checked={showInactive}
            onChange={(event) => setShowInactive(event.target.checked)}
          />
          <span className="text-ink-soft">Show deactivated</span>
        </label>
      </div>

      {tab === 'departments' && (
        <DepartmentsPanel canManage={canManage} showInactive={showInactive} />
      )}
      {tab === 'designations' && (
        <DesignationsPanel canManage={canManage} showInactive={showInactive} />
      )}
      {tab === 'locations' && (
        <LocationsPanel canManage={canManage} showInactive={showInactive} />
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Departments                                                                */
/* -------------------------------------------------------------------------- */

function DepartmentsPanel({
  canManage,
  showInactive,
}: {
  canManage: boolean;
  showInactive: boolean;
}) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<Department | null>(null);
  const [creating, setCreating] = useState(false);

  const query = useQuery({
    queryKey: orgKeys.departments(showInactive),
    queryFn: () => fetchDepartments(showInactive),
  });

  const mutation = useMutation({
    mutationFn: ({ id, body }: { id: number | null; body: Record<string, unknown> }) =>
      id === null ? createDepartment(body) : updateDepartment(id, body),
    onSuccess: async () => {
      toast.success('Department saved');
      setEditing(null);
      setCreating(false);
      await queryClient.invalidateQueries({ queryKey: orgKeys.all });
    },
    onError: (error) => toast.apiError(error, 'Could not save the department'),
  });

  const byId = new Map((query.data ?? []).map((row) => [row.id, row]));

  return (
    <Card>
      <CardBody className="space-y-4">
        {canManage && (
          <div className="flex justify-end">
            <Button
              size="sm"
              onClick={() => setCreating(true)}
              leadingIcon={<Plus className="size-4" aria-hidden="true" />}
            >
              Add department
            </Button>
          </div>
        )}

        {query.isPending && <SkeletonRows rows={4} />}
        {query.isError && (
          <ErrorPanel error={query.error} onRetry={() => void query.refetch()} />
        )}

        {query.isSuccess && query.data.length === 0 && (
          <EmptyState
            icon={Building2}
            title="No departments yet"
            description="Departments group employees for reporting and for targeting announcements."
          />
        )}

        {query.isSuccess && query.data.length > 0 && (
          <TableShell caption="Departments">
            <thead>
              <tr>
                <Th>Code</Th>
                <Th>Name</Th>
                <Th className="hidden sm:table-cell">Parent</Th>
                <Th>Status</Th>
                {canManage && (
                  <Th className="text-right">
                    <span className="sr-only">Actions</span>
                  </Th>
                )}
              </tr>
            </thead>
            <tbody>
              {query.data.map((row) => (
                <tr key={row.id} className="hover:bg-sunken/60">
                  <Td className="font-mono text-xs">{row.code}</Td>
                  <Td className="font-medium">{row.name}</Td>
                  <Td className="hidden sm:table-cell">
                    {row.parent_id
                      ? (byId.get(row.parent_id)?.name ?? `#${row.parent_id}`)
                      : '—'}
                  </Td>
                  <Td>
                    {row.is_active ? (
                      <Badge tone="success">Active</Badge>
                    ) : (
                      <Badge tone="neutral">Deactivated</Badge>
                    )}
                  </Td>
                  {canManage && (
                    <Td className="text-right">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setEditing(row)}
                        leadingIcon={<Pencil className="size-3.5" aria-hidden="true" />}
                      >
                        Edit
                      </Button>
                    </Td>
                  )}
                </tr>
              ))}
            </tbody>
          </TableShell>
        )}
      </CardBody>

      {(creating || editing) && (
        <DepartmentDialog
          key={editing?.id ?? 'new'}
          department={editing}
          candidates={(query.data ?? []).filter((row) => row.id !== editing?.id)}
          isSubmitting={mutation.isPending}
          onClose={() => {
            setEditing(null);
            setCreating(false);
          }}
          onSubmit={(body) => mutation.mutate({ id: editing?.id ?? null, body })}
        />
      )}
    </Card>
  );
}

function DepartmentDialog({
  department,
  candidates,
  isSubmitting,
  onClose,
  onSubmit,
}: {
  department: Department | null;
  candidates: Department[];
  isSubmitting: boolean;
  onClose: () => void;
  onSubmit: (body: Record<string, unknown>) => void;
}) {
  const [code, setCode] = useState(department?.code ?? '');
  const [name, setName] = useState(department?.name ?? '');
  const [parentId, setParentId] = useState(
    department?.parent_id ? String(department.parent_id) : '',
  );
  const [isActive, setIsActive] = useState(department?.is_active ?? true);
  const [error, setError] = useState<string | null>(null);

  const submit = () => {
    if (!code.trim() || !name.trim()) {
      setError('A code and a name are required.');
      return;
    }
    setError(null);
    onSubmit({
      code: code.trim(),
      name: name.trim(),
      parent_id: parentId ? Number(parentId) : null,
      is_active: isActive,
    });
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={department ? `Edit ${department.name}` : 'Add department'}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={isSubmitting}>
            Cancel
          </Button>
          <Button onClick={submit} isLoading={isSubmitting}>
            Save
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {error && (
          <Alert variant="danger" title="Check this form">
            {error}
          </Alert>
        )}
        <TextField
          label="Code"
          required
          hint="Short, stable identifier. Stored upper case."
          value={code}
          onChange={(event) => setCode(event.target.value)}
        />
        <TextField
          label="Name"
          required
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
        <SelectField
          label="Parent department"
          hint="Optional. A parent that would create a loop is rejected."
          value={parentId}
          onChange={(event) => setParentId(event.target.value)}
          options={[
            { value: '', label: 'None (top level)' },
            ...candidates.map((row) => ({ value: String(row.id), label: row.name })),
          ]}
        />
        <CheckboxField
          label="Active"
          hint="Deactivating hides it from pickers; existing employees keep pointing at it."
          checked={isActive}
          onChange={(event) => setIsActive(event.target.checked)}
        />
      </div>
    </Modal>
  );
}

/* -------------------------------------------------------------------------- */
/* Designations                                                               */
/* -------------------------------------------------------------------------- */

function DesignationsPanel({
  canManage,
  showInactive,
}: {
  canManage: boolean;
  showInactive: boolean;
}) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<Designation | null>(null);
  const [creating, setCreating] = useState(false);

  const query = useQuery({
    queryKey: orgKeys.designations(showInactive),
    queryFn: () => fetchDesignations(showInactive),
  });

  const mutation = useMutation({
    mutationFn: ({ id, body }: { id: number | null; body: Record<string, unknown> }) =>
      id === null ? createDesignation(body) : updateDesignation(id, body),
    onSuccess: async () => {
      toast.success('Designation saved');
      setEditing(null);
      setCreating(false);
      await queryClient.invalidateQueries({ queryKey: orgKeys.all });
    },
    onError: (error) => toast.apiError(error, 'Could not save the designation'),
  });

  return (
    <Card>
      <CardBody className="space-y-4">
        {canManage && (
          <div className="flex justify-end">
            <Button
              size="sm"
              onClick={() => setCreating(true)}
              leadingIcon={<Plus className="size-4" aria-hidden="true" />}
            >
              Add designation
            </Button>
          </div>
        )}

        {query.isPending && <SkeletonRows rows={4} />}
        {query.isError && (
          <ErrorPanel error={query.error} onRetry={() => void query.refetch()} />
        )}

        {query.isSuccess && query.data.length === 0 && (
          <EmptyState icon={IdCard} title="No designations yet" />
        )}

        {query.isSuccess && query.data.length > 0 && (
          <TableShell caption="Designations">
            <thead>
              <tr>
                <Th>Code</Th>
                <Th>Name</Th>
                <Th className="hidden sm:table-cell">Level</Th>
                <Th>Status</Th>
                {canManage && (
                  <Th className="text-right">
                    <span className="sr-only">Actions</span>
                  </Th>
                )}
              </tr>
            </thead>
            <tbody>
              {query.data.map((row) => (
                <tr key={row.id} className="hover:bg-sunken/60">
                  <Td className="font-mono text-xs">{row.code}</Td>
                  <Td className="font-medium">{row.name}</Td>
                  <Td className="numeric hidden sm:table-cell">{row.level ?? '—'}</Td>
                  <Td>
                    {row.is_active ? (
                      <Badge tone="success">Active</Badge>
                    ) : (
                      <Badge tone="neutral">Deactivated</Badge>
                    )}
                  </Td>
                  {canManage && (
                    <Td className="text-right">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setEditing(row)}
                        leadingIcon={<Pencil className="size-3.5" aria-hidden="true" />}
                      >
                        Edit
                      </Button>
                    </Td>
                  )}
                </tr>
              ))}
            </tbody>
          </TableShell>
        )}
      </CardBody>

      {(creating || editing) && (
        <DesignationDialog
          key={editing?.id ?? 'new'}
          designation={editing}
          isSubmitting={mutation.isPending}
          onClose={() => {
            setEditing(null);
            setCreating(false);
          }}
          onSubmit={(body) => mutation.mutate({ id: editing?.id ?? null, body })}
        />
      )}
    </Card>
  );
}

function DesignationDialog({
  designation,
  isSubmitting,
  onClose,
  onSubmit,
}: {
  designation: Designation | null;
  isSubmitting: boolean;
  onClose: () => void;
  onSubmit: (body: Record<string, unknown>) => void;
}) {
  const [code, setCode] = useState(designation?.code ?? '');
  const [name, setName] = useState(designation?.name ?? '');
  const [level, setLevel] = useState(
    designation?.level != null ? String(designation.level) : '',
  );
  const [isActive, setIsActive] = useState(designation?.is_active ?? true);
  const [error, setError] = useState<string | null>(null);

  const submit = () => {
    if (!code.trim() || !name.trim()) {
      setError('A code and a name are required.');
      return;
    }
    if (level && !/^\d+$/.test(level)) {
      setError('Level must be a whole number.');
      return;
    }
    setError(null);
    onSubmit({
      code: code.trim(),
      name: name.trim(),
      level: level ? Number(level) : null,
      is_active: isActive,
    });
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={designation ? `Edit ${designation.name}` : 'Add designation'}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={isSubmitting}>
            Cancel
          </Button>
          <Button onClick={submit} isLoading={isSubmitting}>
            Save
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {error && (
          <Alert variant="danger" title="Check this form">
            {error}
          </Alert>
        )}
        <TextField
          label="Code"
          required
          value={code}
          onChange={(event) => setCode(event.target.value)}
        />
        <TextField
          label="Name"
          required
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
        <TextField
          label="Level"
          inputMode="numeric"
          hint="Orders titles in reports. It does not confer authority — that follows the reporting line."
          value={level}
          onChange={(event) => setLevel(event.target.value)}
        />
        <CheckboxField
          label="Active"
          checked={isActive}
          onChange={(event) => setIsActive(event.target.checked)}
        />
      </div>
    </Modal>
  );
}

/* -------------------------------------------------------------------------- */
/* Work locations                                                             */
/* -------------------------------------------------------------------------- */

/** A short list of common zones; any valid IANA name is accepted by the server. */
const COMMON_TIMEZONES = [
  'Asia/Kolkata',
  'Asia/Dubai',
  'Asia/Singapore',
  'Europe/London',
  'America/New_York',
  'America/Los_Angeles',
  'UTC',
];

function LocationsPanel({
  canManage,
  showInactive,
}: {
  canManage: boolean;
  showInactive: boolean;
}) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<WorkLocation | null>(null);
  const [creating, setCreating] = useState(false);

  const query = useQuery({
    queryKey: orgKeys.locations(showInactive),
    queryFn: () => fetchWorkLocations(showInactive),
  });

  const mutation = useMutation({
    mutationFn: ({ id, body }: { id: number | null; body: Record<string, unknown> }) =>
      id === null ? createWorkLocation(body) : updateWorkLocation(id, body),
    onSuccess: async () => {
      toast.success('Work location saved');
      setEditing(null);
      setCreating(false);
      await queryClient.invalidateQueries({ queryKey: orgKeys.all });
    },
    onError: (error) => {
      if (error instanceof ApiError && error.code === 'validation_error') {
        toast.error(error.message, 'Use a name such as Asia/Kolkata.');
      } else {
        toast.apiError(error, 'Could not save the work location');
      }
    },
  });

  return (
    <Card>
      <CardBody className="space-y-4">
        <Alert variant="warning" title="Timezone drives attendance dates">
          Each location&apos;s timezone decides which calendar day a punch belongs to for the
          employees assigned there. Changing it affects future calculations only; past
          attendance is not rewritten.
        </Alert>

        {canManage && (
          <div className="flex justify-end">
            <Button
              size="sm"
              onClick={() => setCreating(true)}
              leadingIcon={<Plus className="size-4" aria-hidden="true" />}
            >
              Add work location
            </Button>
          </div>
        )}

        {query.isPending && <SkeletonRows rows={4} />}
        {query.isError && (
          <ErrorPanel error={query.error} onRetry={() => void query.refetch()} />
        )}

        {query.isSuccess && query.data.length === 0 && (
          <EmptyState
            icon={Globe}
            title="No work locations yet"
            description="Attendance needs at least one location so punches can be dated correctly."
          />
        )}

        {query.isSuccess && query.data.length > 0 && (
          <TableShell caption="Work locations">
            <thead>
              <tr>
                <Th>Code</Th>
                <Th>Name</Th>
                <Th>Timezone</Th>
                <Th className="hidden sm:table-cell">State</Th>
                <Th>Status</Th>
                {canManage && (
                  <Th className="text-right">
                    <span className="sr-only">Actions</span>
                  </Th>
                )}
              </tr>
            </thead>
            <tbody>
              {query.data.map((row) => (
                <tr key={row.id} className="hover:bg-sunken/60">
                  <Td className="font-mono text-xs">{row.code}</Td>
                  <Td className="font-medium">{row.name}</Td>
                  <Td className="font-mono text-xs">{row.timezone}</Td>
                  <Td className="hidden sm:table-cell">{row.state ?? '—'}</Td>
                  <Td>
                    {row.is_active ? (
                      <Badge tone="success">Active</Badge>
                    ) : (
                      <Badge tone="neutral">Deactivated</Badge>
                    )}
                  </Td>
                  {canManage && (
                    <Td className="text-right">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setEditing(row)}
                        leadingIcon={<Pencil className="size-3.5" aria-hidden="true" />}
                      >
                        Edit
                      </Button>
                    </Td>
                  )}
                </tr>
              ))}
            </tbody>
          </TableShell>
        )}
      </CardBody>

      {(creating || editing) && (
        <LocationDialog
          key={editing?.id ?? 'new'}
          location={editing}
          isSubmitting={mutation.isPending}
          onClose={() => {
            setEditing(null);
            setCreating(false);
          }}
          onSubmit={(body) => mutation.mutate({ id: editing?.id ?? null, body })}
        />
      )}
    </Card>
  );
}

function LocationDialog({
  location,
  isSubmitting,
  onClose,
  onSubmit,
}: {
  location: WorkLocation | null;
  isSubmitting: boolean;
  onClose: () => void;
  onSubmit: (body: Record<string, unknown>) => void;
}) {
  const [code, setCode] = useState(location?.code ?? '');
  const [name, setName] = useState(location?.name ?? '');
  const [timezone, setTimezone] = useState(location?.timezone ?? 'Asia/Kolkata');
  const [city, setCity] = useState(location?.city ?? '');
  const [state, setState] = useState(location?.state ?? '');
  const [isActive, setIsActive] = useState(location?.is_active ?? true);
  const [error, setError] = useState<string | null>(null);

  const timezoneChanged = location !== null && location.timezone !== timezone;

  const submit = () => {
    if (!code.trim() || !name.trim() || !timezone.trim()) {
      setError('Code, name and timezone are all required.');
      return;
    }
    setError(null);
    onSubmit({
      code: code.trim(),
      name: name.trim(),
      timezone: timezone.trim(),
      city: city.trim() || null,
      state: state.trim() || null,
      is_active: isActive,
    });
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={location ? `Edit ${location.name}` : 'Add work location'}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={isSubmitting}>
            Cancel
          </Button>
          <Button onClick={submit} isLoading={isSubmitting}>
            Save
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {error && (
          <Alert variant="danger" title="Check this form">
            {error}
          </Alert>
        )}
        {timezoneChanged && (
          <Alert variant="warning" title="You are changing the timezone">
            Attendance dates for employees at this location will be derived in the new zone from
            now on. Existing attendance records are left as they are.
          </Alert>
        )}
        <TextField
          label="Code"
          required
          value={code}
          onChange={(event) => setCode(event.target.value)}
        />
        <TextField
          label="Name"
          required
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
        <SelectField
          label="Timezone"
          required
          hint="IANA name. Any valid name is accepted; these are the common ones."
          value={COMMON_TIMEZONES.includes(timezone) ? timezone : ''}
          onChange={(event) => setTimezone(event.target.value)}
          options={[
            ...(COMMON_TIMEZONES.includes(timezone)
              ? []
              : [{ value: '', label: `Other: ${timezone}` }]),
            ...COMMON_TIMEZONES.map((value) => ({ value, label: value })),
          ]}
        />
        <TextField
          label="Timezone (exact IANA name)"
          required
          hint="Edit directly if your zone is not in the list above."
          value={timezone}
          onChange={(event) => setTimezone(event.target.value)}
        />
        <TextField
          label="City"
          value={city}
          onChange={(event) => setCity(event.target.value)}
        />
        <TextField
          label="State"
          hint="Relevant to professional tax, which is levied per state."
          value={state}
          onChange={(event) => setState(event.target.value)}
        />
        <CheckboxField
          label="Active"
          checked={isActive}
          onChange={(event) => setIsActive(event.target.checked)}
        />
      </div>
    </Modal>
  );
}
