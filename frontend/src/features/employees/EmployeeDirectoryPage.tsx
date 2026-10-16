/**
 * The employee directory.
 *
 * Three ways of looking at the same filtered set, because people come to this
 * page with different questions:
 *
 * * **Cards** - "who is in this team?" Faces and roles, scannable.
 * * **Table** - "give me the data." Dense, sortable-looking, copyable.
 * * **Chart** - "who reports to whom?" The reporting hierarchy.
 *
 * Filters and the chosen view both live in the URL, so a filtered directory can
 * be bookmarked, shared with a colleague, and survives a reload - which matters
 * when HR is working through a list over an afternoon.
 */

import { keepPreviousData, useQuery } from '@tanstack/react-query';
import {
  Building2,
  CalendarDays,
  LayoutGrid,
  Mail,
  MapPin,
  Network,
  Plus,
  Rows3,
  Search,
  Upload,
  Users,
  X,
} from 'lucide-react';
import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { Button } from '@/components/ui/Button';
import { SelectField, TextField } from '@/components/ui/Field';
import {
  Alert,
  EmptyState,
  ErrorPanel,
  LoadingPanel,
  SkeletonRows,
} from '@/components/ui/Feedback';
import {
  Avatar,
  Badge,
  Card,
  CardBody,
  PageHeader,
  TableShell,
  Td,
  Th,
  Tr,
} from '@/components/ui/Surface';
import {
  EMPLOYMENT_STATUSES,
  EMPLOYMENT_TYPES,
  employeeKeys,
  fetchDepartments,
  fetchEmployeeGraph,
  fetchEmployees,
  fetchWorkLocations,
  orgKeys,
  type EmployeeListParams,
  type EmployeeSummary,
} from '@/features/employees/api';
import { OrgChart } from '@/features/employees/OrgChart';
import { EmploymentStatusBadge, EmploymentTypeBadge } from '@/features/employees/shared';
import { useAuth } from '@/lib/auth';
import { cn, formatDate, humanise } from '@/lib/utils';

const PAGE_SIZE = 24;

type View = 'cards' | 'table' | 'chart';

const VIEWS: { id: View; label: string; icon: React.ComponentType<{ className?: string }> }[] =
  [
    { id: 'cards', label: 'Cards', icon: LayoutGrid },
    { id: 'table', label: 'Table', icon: Rows3 },
    { id: 'chart', label: 'Org chart', icon: Network },
  ];

/** Query-string keys that are filters, as opposed to the view or the page. */
const FILTER_KEYS = [
  'q',
  'department_id',
  'work_location_id',
  'employment_status',
  'employment_type',
  'include_exited',
] as const;

export function EmployeeDirectoryPage() {
  const { can } = useAuth();
  const canManage = can('employee.manage');

  const [searchParams, setSearchParams] = useSearchParams();
  const [searchDraft, setSearchDraft] = useState(searchParams.get('q') ?? '');

  const viewParam = searchParams.get('view');
  const view: View = viewParam === 'table' || viewParam === 'chart' ? viewParam : 'cards';

  const filters: Omit<EmployeeListParams, 'page' | 'page_size'> = {
    ...(searchParams.get('q') ? { q: searchParams.get('q')! } : {}),
    ...(searchParams.get('department_id')
      ? { department_id: Number(searchParams.get('department_id')) }
      : {}),
    ...(searchParams.get('work_location_id')
      ? { work_location_id: Number(searchParams.get('work_location_id')) }
      : {}),
    ...(searchParams.get('employment_status')
      ? { employment_status: searchParams.get('employment_status')! }
      : {}),
    ...(searchParams.get('employment_type')
      ? { employment_type: searchParams.get('employment_type')! }
      : {}),
    include_exited: searchParams.get('include_exited') === 'true',
  };

  const params: EmployeeListParams = {
    page: Number(searchParams.get('page') ?? 1),
    page_size: PAGE_SIZE,
    ...filters,
  };

  const setParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value);
    else next.delete(key);
    // Any filter change invalidates the current page number.
    if (key !== 'page' && key !== 'view') next.delete('page');
    setSearchParams(next, { replace: true });
  };

  const clearFilters = () => {
    setSearchDraft('');
    const next = new URLSearchParams();
    // The chosen view is a preference, not a filter: clearing the search should
    // not throw somebody out of the chart they were reading.
    if (viewParam) next.set('view', viewParam);
    setSearchParams(next, { replace: true });
  };

  const departmentsQuery = useQuery({
    queryKey: orgKeys.departments(),
    queryFn: () => fetchDepartments(),
  });

  const locationsQuery = useQuery({
    queryKey: orgKeys.locations(),
    queryFn: () => fetchWorkLocations(),
  });

  const employeesQuery = useQuery({
    queryKey: employeeKeys.list(params as Record<string, unknown>),
    queryFn: () => fetchEmployees(params),
    // Keep the previous page on screen while the next one loads, so paging
    // through the directory does not flash a skeleton on every click.
    placeholderData: keepPreviousData,
    enabled: view !== 'chart',
  });

  /*
   * The chart is a separate query because it needs a different shape of answer:
   * every matching employee rather than one page. A manager and their reports
   * routinely land on different pages, so a tree built from page one would be
   * wrong rather than merely partial.
   */
  const graphQuery = useQuery({
    queryKey: employeeKeys.graph(filters as Record<string, unknown>),
    queryFn: () => fetchEmployeeGraph(filters),
    enabled: view === 'chart',
  });

  const meta = employeesQuery.data?.meta;
  const departmentName = (id: string) =>
    departmentsQuery.data?.find((row) => String(row.id) === id)?.name ?? `Department ${id}`;
  const locationName = (id: string) =>
    locationsQuery.data?.find((row) => String(row.id) === id)?.name ?? `Location ${id}`;

  const activeFilters = FILTER_KEYS.flatMap<{ key: string; label: string }>((key) => {
    const value = searchParams.get(key);
    if (!value) return [];
    if (key === 'include_exited') {
      return value === 'true' ? [{ key, label: 'Including people who have left' }] : [];
    }
    const label =
      key === 'q'
        ? `"${value}"`
        : key === 'department_id'
          ? departmentName(value)
          : key === 'work_location_id'
            ? locationName(value)
            : humanise(value);
    return [{ key, label }];
  });

  const total = view === 'chart' ? graphQuery.data?.totalItems : meta?.total_items;

  return (
    <div>
      <PageHeader
        eyebrow="People"
        title="Employee directory"
        description="Everyone you have access to. People who have left are retained and can be included."
        action={
          canManage ? (
            <div className="flex flex-wrap gap-2">
              <Link to="/employees/import">
                <Button
                  variant="secondary"
                  leadingIcon={<Upload className="size-4" aria-hidden="true" />}
                >
                  Import CSV
                </Button>
              </Link>
              <Link to="/employees/new">
                <Button leadingIcon={<Plus className="size-4" aria-hidden="true" />}>
                  Add employee
                </Button>
              </Link>
            </div>
          ) : undefined
        }
      />

      <Card className="animate-in">
        <CardBody className="space-y-4">
          <form
            className="flex flex-wrap items-end gap-3"
            onSubmit={(event) => {
              event.preventDefault();
              setParam('q', searchDraft.trim() || null);
            }}
          >
            <div className="min-w-56 flex-1">
              <TextField
                label="Search"
                placeholder="Code, name or work email"
                value={searchDraft}
                onChange={(event) => setSearchDraft(event.target.value)}
              />
            </div>

            <div className="w-44">
              <SelectField
                label="Department"
                value={searchParams.get('department_id') ?? ''}
                onChange={(event) => setParam('department_id', event.target.value || null)}
                options={[
                  { value: '', label: 'All departments' },
                  ...(departmentsQuery.data ?? []).map((row) => ({
                    value: String(row.id),
                    label: row.name,
                  })),
                ]}
              />
            </div>

            <div className="w-44">
              <SelectField
                label="Location"
                value={searchParams.get('work_location_id') ?? ''}
                onChange={(event) => setParam('work_location_id', event.target.value || null)}
                options={[
                  { value: '', label: 'All locations' },
                  ...(locationsQuery.data ?? []).map((row) => ({
                    value: String(row.id),
                    label: row.name,
                  })),
                ]}
              />
            </div>

            <div className="w-40">
              <SelectField
                label="Status"
                value={searchParams.get('employment_status') ?? ''}
                onChange={(event) => setParam('employment_status', event.target.value || null)}
                options={[
                  { value: '', label: 'Any status' },
                  ...EMPLOYMENT_STATUSES.map((value) => ({ value, label: humanise(value) })),
                ]}
              />
            </div>

            <div className="w-40">
              <SelectField
                label="Type"
                value={searchParams.get('employment_type') ?? ''}
                onChange={(event) => setParam('employment_type', event.target.value || null)}
                options={[
                  { value: '', label: 'Any type' },
                  ...EMPLOYMENT_TYPES.map((value) => ({ value, label: humanise(value) })),
                ]}
              />
            </div>

            <Button
              type="submit"
              variant="secondary"
              leadingIcon={<Search className="size-4" aria-hidden="true" />}
            >
              Search
            </Button>
          </form>

          <div className="flex flex-wrap items-center justify-between gap-3">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                className="border-line-strong accent-brand-600 size-4 rounded"
                checked={searchParams.get('include_exited') === 'true'}
                onChange={(event) =>
                  setParam('include_exited', event.target.checked ? 'true' : null)
                }
              />
              <span className="text-ink-soft">Include employees who have left</span>
            </label>

            <ViewSwitcher value={view} onChange={(next) => setParam('view', next)} />
          </div>

          {activeFilters.length > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-ink-muted text-xs font-medium tracking-wide uppercase">
                Filtered by
              </span>
              {activeFilters.map((filter) => (
                <button
                  key={filter.key}
                  type="button"
                  onClick={() => {
                    if (filter.key === 'q') setSearchDraft('');
                    setParam(filter.key, null);
                  }}
                  className={cn(
                    'bg-sunken text-ink-soft ring-line-strong inline-flex items-center gap-1.5',
                    'hover:text-ink rounded-full py-0.5 pr-1.5 pl-2.5 text-xs font-medium',
                    'ring-1 transition-colors ring-inset',
                  )}
                >
                  {filter.label}
                  <X className="size-3.5" aria-hidden="true" />
                  <span className="sr-only">Remove this filter</span>
                </button>
              ))}
              <button
                type="button"
                onClick={clearFilters}
                className="text-brand-600 dark:text-brand-300 text-xs font-medium hover:underline"
              >
                Clear all
              </button>
            </div>
          )}

          {typeof total === 'number' && (
            <p className="text-ink-soft text-sm">
              <span className="text-ink font-semibold tabular-nums">{total}</span>{' '}
              {total === 1 ? 'person' : 'people'} match
              {activeFilters.length > 0 ? ' these filters' : ''}
            </p>
          )}
        </CardBody>
      </Card>

      <div className="mt-5">
        {view === 'chart' ? (
          <Card>
            <CardBody className="space-y-4">
              {graphQuery.isPending && <LoadingPanel label="Building the reporting chart..." />}
              {graphQuery.isError && (
                <ErrorPanel
                  error={graphQuery.error}
                  onRetry={() => void graphQuery.refetch()}
                  title="Could not load the organisation chart"
                />
              )}
              {graphQuery.isSuccess && (
                <>
                  {graphQuery.data.truncated && (
                    <Alert variant="warning" title="This chart is incomplete">
                      Only the first {graphQuery.data.employees.length} of{' '}
                      {graphQuery.data.totalItems} matching employees were loaded, so branches
                      may be missing. Narrow the filters - by department, for example - to see a
                      complete hierarchy.
                    </Alert>
                  )}
                  <OrgChart employees={graphQuery.data.employees} />
                </>
              )}
            </CardBody>
          </Card>
        ) : (
          <>
            {employeesQuery.isPending && (
              <Card>
                <CardBody>
                  <SkeletonRows rows={8} />
                </CardBody>
              </Card>
            )}

            {employeesQuery.isError && (
              <ErrorPanel
                error={employeesQuery.error}
                onRetry={() => void employeesQuery.refetch()}
                title="Could not load employees"
              />
            )}

            {employeesQuery.isSuccess && employeesQuery.data.items.length === 0 && (
              <EmptyState
                icon={activeFilters.length > 0 ? Search : Users}
                title={
                  activeFilters.length > 0
                    ? 'No employees match your filters'
                    : 'No employees yet'
                }
                description={
                  activeFilters.length > 0
                    ? 'Try a broader search, or clear the filters.'
                    : canManage
                      ? 'Add your first employee, or import a CSV.'
                      : 'You do not currently have access to any employee records.'
                }
                action={
                  activeFilters.length > 0 ? (
                    <Button variant="secondary" size="sm" onClick={clearFilters}>
                      Clear filters
                    </Button>
                  ) : undefined
                }
              />
            )}

            {employeesQuery.isSuccess && employeesQuery.data.items.length > 0 && (
              <div
                className={cn(
                  'space-y-5 transition-opacity',
                  // Dim rather than unmount while the next page is in flight:
                  // the layout stays still and the change is legible.
                  employeesQuery.isPlaceholderData && 'opacity-60',
                )}
              >
                {view === 'cards' ? (
                  <EmployeeGrid employees={employeesQuery.data.items} />
                ) : (
                  <Card>
                    <CardBody className="p-0 sm:p-5">
                      <EmployeeTable employees={employeesQuery.data.items} />
                    </CardBody>
                  </Card>
                )}

                {meta && (
                  <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
                    <p className="text-ink-soft">
                      Showing{' '}
                      <span className="font-medium tabular-nums">
                        {(meta.page - 1) * meta.page_size + 1}-
                        {Math.min(meta.page * meta.page_size, meta.total_items)}
                      </span>{' '}
                      of <span className="font-medium tabular-nums">{meta.total_items}</span>
                    </p>
                    <div className="flex items-center gap-2">
                      <Button
                        variant="secondary"
                        size="sm"
                        disabled={!meta.has_previous}
                        onClick={() => setParam('page', String(meta.page - 1))}
                      >
                        Previous
                      </Button>
                      <span className="text-ink-soft tabular-nums">
                        Page {meta.page} of {meta.total_pages}
                      </span>
                      <Button
                        variant="secondary"
                        size="sm"
                        disabled={!meta.has_next}
                        onClick={() => setParam('page', String(meta.page + 1))}
                      >
                        Next
                      </Button>
                    </div>
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </div>

      {canManage && (
        <p className="text-ink-muted mt-5 text-xs">
          Bulk onboarding? Use{' '}
          <Link to="/employees/import" className="underline">
            CSV import
          </Link>
          , which validates every row and previews before writing anything.
        </p>
      )}
    </div>
  );
}

/** Segmented control for the view. A radio group, not a row of buttons. */
function ViewSwitcher({ value, onChange }: { value: View; onChange: (view: View) => void }) {
  return (
    <div
      role="radiogroup"
      aria-label="Directory view"
      className="border-line bg-sunken inline-flex gap-0.5 rounded-full border p-0.5"
    >
      {VIEWS.map((option) => {
        const Icon = option.icon;
        const selected = option.id === value;
        return (
          <button
            key={option.id}
            type="button"
            role="radio"
            aria-checked={selected}
            onClick={() => onChange(option.id)}
            className={cn(
              'inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium',
              'transition-[background-color,color,box-shadow] duration-200',
              selected ? 'bg-raised text-ink shadow-xs' : 'text-ink-muted hover:text-ink-soft',
            )}
          >
            <Icon className="size-3.5" aria-hidden="true" />
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

function EmployeeGrid({ employees }: { employees: EmployeeSummary[] }) {
  return (
    <ul className="stagger grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
      {employees.map((employee) => (
        <li key={employee.id} className="animate-in">
          <Link to={`/employees/${employee.id}`} className="block h-full focus:outline-none">
            <Card interactive className="focus-within:border-brand-500 h-full">
              <CardBody className="flex h-full flex-col gap-4">
                <div className="flex items-start gap-3">
                  <Avatar name={employee.full_name} size="lg" />
                  <div className="min-w-0 flex-1">
                    <p className="text-ink truncate font-semibold">{employee.full_name}</p>
                    <p className="text-ink-soft truncate text-sm">
                      {employee.designation_name ?? 'No designation recorded'}
                    </p>
                    <p className="text-ink-muted mt-0.5 font-mono text-xs">
                      {employee.employee_code}
                    </p>
                  </div>
                </div>

                <dl className="text-ink-soft space-y-1.5 text-xs">
                  <Facet icon={Building2} label="Department">
                    {employee.department_name}
                  </Facet>
                  <Facet icon={MapPin} label="Location">
                    {employee.work_location_name}
                  </Facet>
                  <Facet icon={Mail} label="Work email">
                    {employee.work_email}
                  </Facet>
                  <Facet icon={CalendarDays} label="Joined">
                    {formatDate(employee.date_of_joining)}
                  </Facet>
                </dl>

                <div className="mt-auto flex flex-wrap gap-1.5">
                  <EmploymentStatusBadge status={employee.employment_status} />
                  <EmploymentTypeBadge type={employee.employment_type} />
                  {employee.date_of_exit && (
                    <Badge tone="neutral">Left {formatDate(employee.date_of_exit)}</Badge>
                  )}
                </div>
              </CardBody>
            </Card>
          </Link>
        </li>
      ))}
    </ul>
  );
}

/** One labelled line in a card. Absent values are shown, not hidden. */
function Facet({
  icon: Icon,
  label,
  children,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  children: React.ReactNode;
}) {
  const empty = children === null || children === undefined || children === '';
  return (
    <div className="flex items-center gap-2">
      <dt className="shrink-0">
        <Icon className="text-ink-muted size-3.5" aria-hidden="true" />
        <span className="sr-only">{label}</span>
      </dt>
      <dd className={cn('min-w-0 truncate', empty && 'text-ink-muted')}>
        {empty ? <>&mdash;</> : children}
      </dd>
    </div>
  );
}

function EmployeeTable({ employees }: { employees: EmployeeSummary[] }) {
  return (
    <TableShell caption="Employee directory">
      <thead>
        <tr>
          <Th>Name</Th>
          <Th className="hidden md:table-cell">Department</Th>
          <Th className="hidden lg:table-cell">Designation</Th>
          <Th className="hidden lg:table-cell">Location</Th>
          <Th>Status</Th>
          <Th className="hidden sm:table-cell">Joined</Th>
        </tr>
      </thead>
      <tbody>
        {employees.map((employee) => (
          <Tr key={employee.id}>
            <Td>
              <div className="flex items-center gap-3">
                <Avatar name={employee.full_name} size="sm" />
                <div className="min-w-0">
                  <Link
                    to={`/employees/${employee.id}`}
                    className="text-ink hover:text-brand-600 dark:hover:text-brand-300 font-medium transition-colors"
                  >
                    {employee.full_name}
                  </Link>
                  <p className="text-ink-muted truncate text-xs">
                    <span className="font-mono">{employee.employee_code}</span>
                    {employee.work_email ? ` - ${employee.work_email}` : ''}
                  </p>
                </div>
              </div>
            </Td>
            <Td className="hidden md:table-cell">{employee.department_name ?? <>&mdash;</>}</Td>
            <Td className="hidden lg:table-cell">
              {employee.designation_name ?? <>&mdash;</>}
            </Td>
            <Td className="hidden lg:table-cell">
              {employee.work_location_name ?? <>&mdash;</>}
            </Td>
            <Td>
              <div className="flex flex-col items-start gap-1">
                <EmploymentStatusBadge status={employee.employment_status} />
                <EmploymentTypeBadge type={employee.employment_type} />
              </div>
            </Td>
            <Td className="hidden whitespace-nowrap tabular-nums sm:table-cell">
              {formatDate(employee.date_of_joining)}
            </Td>
          </Tr>
        ))}
      </tbody>
    </TableShell>
  );
}
