/**
 * Dashboard.
 *
 * Shows what exists and names what does not. Metrics whose data source arrives
 * in a later milestone are listed as pending rather than rendered as zeros,
 * because "0 absent today" reads as a fact and would be a lie.
 *
 * The same rule governs the decoration. There are no trend lines on this page:
 * the API returns today's figures, not a series, and a sparkline drawn from a
 * single number is an invention. Every ring, bar and meter here is a proportion
 * of two numbers the server actually sent.
 */

import { useQuery } from '@tanstack/react-query';
import {
  ArrowRight,
  CalendarClock,
  CalendarDays,
  CircleCheck,
  ClipboardList,
  FileText,
  Network,
  Scale,
  ShieldCheck,
  Sliders,
  TriangleAlert,
  UserRound,
  UserX,
  Users,
} from 'lucide-react';
import { Link } from 'react-router-dom';

import { Button } from '@/components/ui/Button';
import { Alert, ErrorPanel, LoadingPanel } from '@/components/ui/Feedback';
import { Meter, Ring, StatTile } from '@/components/ui/Stat';
import {
  Avatar,
  Badge,
  Card,
  CardBody,
  CardHeader,
  GlassCard,
  PageHeader,
} from '@/components/ui/Surface';
import { dashboardKeys, fetchHrDashboard, fetchMyDashboard } from '@/features/employees/api';
import { EmploymentStatusBadge } from '@/features/employees/shared';
import { useAuth } from '@/lib/auth';
import { cn, formatDate, humanise } from '@/lib/utils';

export function DashboardPage() {
  const { user, can } = useAuth();
  const isHr = can('employee.read');

  return (
    <div>
      <PageHeader
        eyebrow="Overview"
        title={`Welcome, ${user?.username ?? 'there'}`}
        description="Ishvara Attendance Management System"
      />
      <QuickActions />
      {isHr ? <HrDashboard /> : <MyDashboard />}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Quick actions                                                              */
/* -------------------------------------------------------------------------- */

const ACTIONS: {
  to: string;
  label: string;
  hint: string;
  icon: React.ComponentType<{ className?: string }>;
  permissions: string[];
}[] = [
  {
    to: '/my-attendance',
    label: 'My attendance',
    hint: 'Punch in or out, see today',
    icon: CalendarClock,
    permissions: ['attendance.read_self'],
  },
  {
    to: '/leave',
    label: 'Leave',
    hint: 'Balances and requests',
    icon: ClipboardList,
    permissions: ['leave.read_self'],
  },
  {
    to: '/employees?view=chart',
    label: 'Org chart',
    hint: 'Who reports to whom',
    icon: Network,
    permissions: ['employee.read', 'employee.read_team'],
  },
  {
    to: '/reports',
    label: 'Reports',
    hint: 'Export registers',
    icon: FileText,
    permissions: ['report.attendance', 'report.leave', 'report.employee', 'report.payroll'],
  },
  {
    to: '/settings',
    label: 'Settings',
    hint: 'Policy and configuration',
    icon: Sliders,
    permissions: ['settings.read'],
  },
];

/**
 * A short row of destinations, filtered by what the account may actually reach.
 * Four at most: a wall of shortcuts is a second navigation menu, not a shortcut.
 */
function QuickActions() {
  const { canAny } = useAuth();
  const available = ACTIONS.filter((action) => canAny(...action.permissions)).slice(0, 4);
  if (available.length === 0) return null;

  return (
    <div className="stagger mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {available.map((action) => {
        const Icon = action.icon;
        return (
          <Link key={action.to} to={action.to} className="animate-in block">
            <GlassCard className="hover-lift group h-full px-4 py-3.5">
              <div className="flex items-center gap-3">
                <span className="bg-brand-50 text-brand-600 dark:bg-brand-950/60 dark:text-brand-300 grid size-9 shrink-0 place-items-center rounded-lg">
                  <Icon className="size-4.5" aria-hidden="true" />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-ink truncate text-sm font-medium">{action.label}</p>
                  <p className="text-ink-muted truncate text-xs">{action.hint}</p>
                </div>
                <ArrowRight
                  className="text-ink-muted size-4 shrink-0 transition-transform duration-200 group-hover:translate-x-0.5"
                  aria-hidden="true"
                />
              </div>
            </GlassCard>
          </Link>
        );
      })}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* HR / Admin                                                                 */
/* -------------------------------------------------------------------------- */

/** Status colours, matched to the badges used everywhere else in the app. */
const STATUS_METER_TONES: Record<
  string,
  'brand' | 'success' | 'warning' | 'danger' | 'neutral'
> = {
  active: 'success',
  probation: 'brand',
  notice_period: 'warning',
  suspended: 'danger',
  exited: 'neutral',
};

function HrDashboard() {
  const query = useQuery({ queryKey: dashboardKeys.hr(), queryFn: fetchHrDashboard });

  if (query.isPending) return <LoadingPanel label="Loading dashboard..." />;
  if (query.isError) {
    return (
      <ErrorPanel
        error={query.error}
        onRetry={() => void query.refetch()}
        title="Could not load the dashboard"
      />
    );
  }

  const data = query.data;
  const { headcount, attention, configuration, joiners } = data;
  const needsAttention =
    attention.probation_ending_soon.length > 0 ||
    attention.reports_needing_manager.length > 0 ||
    attention.employees_without_account > 0;

  const statusSegments = Object.entries(headcount.by_status).map(([status, count]) => ({
    label: humanise(status),
    value: count,
    tone: STATUS_METER_TONES[status] ?? 'neutral',
  }));

  return (
    <div className="space-y-6">
      <p className="text-ink-muted text-xs">
        Figures as recorded by the server on {formatDate(data.as_of)}.
      </p>

      {configuration.unconfirmed > 0 && (
        <Alert
          variant="warning"
          title={`${configuration.unconfirmed} of ${configuration.total} settings await confirmation`}
          icon={TriangleAlert}
        >
          <p>
            These are application defaults, not decisions Ishvara Tech has made.
            {configuration.awaiting_legal_verification > 0 && (
              <>
                {' '}
                {configuration.awaiting_legal_verification} have statutory consequences and must
                be verified against an authoritative source.
              </>
            )}
          </p>
          <Link
            to="/settings"
            className="mt-2 inline-flex items-center gap-1 text-sm font-medium underline underline-offset-4"
          >
            Review settings
            <ArrowRight className="size-3.5" aria-hidden="true" />
          </Link>
        </Alert>
      )}

      <div className="stagger grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile
          className="animate-in"
          label="Active employees"
          value={headcount.active_total}
          hint={`${headcount.by_department.length} department${headcount.by_department.length === 1 ? '' : 's'}`}
          icon={Users}
          tone="brand"
          action={
            <Link
              to="/employees"
              className="text-brand-600 dark:text-brand-300 text-xs font-medium hover:underline"
            >
              Open the directory
            </Link>
          }
        />
        <StatTile
          className="animate-in"
          label="Joining soon"
          value={joiners.upcoming.length}
          hint={
            joiners.upcoming[0]
              ? `Next on ${formatDate(joiners.upcoming[0].date_of_joining)}`
              : 'Nobody scheduled'
          }
          icon={CalendarDays}
        />
        <StatTile
          className="animate-in"
          label="Without a sign-in account"
          value={attention.employees_without_account}
          hint={
            attention.employees_without_account > 0
              ? 'They cannot punch in or request leave'
              : 'Everyone active can sign in'
          }
          icon={UserX}
          tone={attention.employees_without_account > 0 ? 'warning' : 'success'}
        />
        <StatTile
          className="animate-in"
          label="Settings to confirm"
          value={configuration.unconfirmed}
          hint={
            configuration.awaiting_legal_verification > 0
              ? `${configuration.awaiting_legal_verification} need legal verification`
              : 'No statutory items outstanding'
          }
          icon={Scale}
          tone={configuration.unconfirmed > 0 ? 'warning' : 'success'}
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card>
          <CardHeader title="Configuration" icon={Scale} />
          <CardBody className="flex flex-col items-center gap-4">
            <Ring
              value={configuration.confirmed}
              total={configuration.total}
              label={`${configuration.confirmed}/${configuration.total}`}
              sublabel="confirmed"
              tone={configuration.unconfirmed === 0 ? 'success' : 'warning'}
            />
            <p className="text-ink-soft text-center text-sm">
              {configuration.unconfirmed === 0
                ? 'Every setting has been reviewed and confirmed.'
                : 'An unconfirmed setting still works - it is just carrying a default nobody has agreed to.'}
            </p>
            <Link to="/settings" className="w-full">
              <Button variant="secondary" size="sm" className="w-full">
                Review settings
              </Button>
            </Link>
          </CardBody>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader title="Headcount by status" icon={Users} />
          <CardBody className="space-y-5">
            {statusSegments.length === 0 ? (
              <p className="text-ink-muted text-sm">No employees yet.</p>
            ) : (
              <Meter segments={statusSegments} />
            )}

            {Object.keys(headcount.by_employment_type).length > 0 && (
              <div>
                <p className="text-ink-soft mb-2.5 text-xs font-semibold tracking-wide uppercase">
                  By employment type
                </p>
                <BarList
                  rows={Object.entries(headcount.by_employment_type).map(([type, count]) => ({
                    label: humanise(type),
                    value: count,
                  }))}
                />
              </div>
            )}
          </CardBody>
        </Card>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="By department" icon={Users} />
          <CardBody>
            {headcount.by_department.length === 0 ? (
              <p className="text-ink-muted text-sm">No employees yet.</p>
            ) : (
              <BarList
                rows={headcount.by_department.map((row) => ({
                  label: row.department_name,
                  value: row.count,
                }))}
              />
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="By work location" icon={Users} />
          <CardBody>
            {headcount.by_work_location.length === 0 ? (
              <p className="text-ink-muted text-sm">No employees yet.</p>
            ) : (
              <BarList
                rows={headcount.by_work_location.map((row) => ({
                  label: row.work_location_name,
                  value: row.count,
                }))}
              />
            )}
          </CardBody>
        </Card>
      </div>

      {needsAttention && (
        <Card>
          <CardHeader
            title="Needs attention"
            description="Data-quality and lifecycle items that will block other work if left."
            icon={TriangleAlert}
          />
          <CardBody className="space-y-5">
            {attention.reports_needing_manager.length > 0 && (
              <div>
                <p className="text-ink mb-1 text-sm font-medium">
                  No reporting manager ({attention.reports_needing_manager.length})
                </p>
                <p className="text-ink-soft mb-2.5 text-xs">
                  Their leave and attendance requests have nobody to review them, so they would
                  stall with no visible cause.
                </p>
                <ul className="flex flex-wrap gap-2">
                  {attention.reports_needing_manager.map((row) => (
                    <li key={row.id}>
                      <Link to={`/employees/${row.id}`} className="inline-block">
                        <PersonChip
                          name={row.full_name}
                          detail={row.employee_code}
                          tone="warning"
                        />
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {attention.probation_ending_soon.length > 0 && (
              <div>
                <p className="text-ink mb-2.5 text-sm font-medium">
                  Probation ending within 30 days ({attention.probation_ending_soon.length})
                </p>
                <ul className="flex flex-wrap gap-2">
                  {attention.probation_ending_soon.map((row) => (
                    <li key={row.id}>
                      <Link to={`/employees/${row.id}`} className="inline-block">
                        <PersonChip
                          name={row.full_name}
                          detail={`ends ${formatDate(row.probation_end_date)}`}
                          tone="brand"
                        />
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {attention.employees_without_account > 0 && (
              <Alert variant="info" title="Employees without a sign-in account" icon={UserX}>
                {attention.employees_without_account} active employee
                {attention.employees_without_account === 1 ? '' : 's'} cannot sign in yet.
                Create an account and link it from their record.
              </Alert>
            )}
          </CardBody>
        </Card>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Recent joiners" description="Last 30 days" icon={UserRound} />
          <CardBody>
            <JoinerList rows={joiners.recent} empty="Nobody joined in the last 30 days." />
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Joining soon" icon={CalendarDays} />
          <CardBody>
            <JoinerList rows={joiners.upcoming} empty="No upcoming joiners." />
          </CardBody>
        </Card>
      </div>

      <PendingMetrics pending={data.not_yet_available} />
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Employee                                                                   */
/* -------------------------------------------------------------------------- */

function MyDashboard() {
  const query = useQuery({ queryKey: dashboardKeys.me(), queryFn: fetchMyDashboard });

  if (query.isPending) return <LoadingPanel label="Loading your dashboard..." />;
  if (query.isError) {
    return (
      <ErrorPanel
        error={query.error}
        onRetry={() => void query.refetch()}
        title="Could not load your dashboard"
      />
    );
  }

  const data = query.data;

  if (!data.employee) {
    return (
      <div className="space-y-6">
        <Alert variant="info" title="No employee record yet">
          {data.message ??
            'Your account is not linked to an employee record. Contact HR to have it linked.'}
        </Alert>
        <PendingMetrics pending={data.not_yet_available} />
      </div>
    );
  }

  const e = data.employee;

  return (
    <div className="space-y-6">
      <GlassCard className="animate-in overflow-hidden">
        <div className="flex flex-wrap items-center gap-5 p-5">
          <Avatar name={e.full_name} size="lg" />
          <div className="min-w-0 flex-1">
            <h2 className="text-ink truncate text-lg font-semibold">{e.full_name}</h2>
            <p className="text-ink-soft truncate text-sm">
              {e.department_name ?? 'No department recorded'}
              {e.work_location_name ? ` - ${e.work_location_name}` : ''}
            </p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              <EmploymentStatusBadge status={e.employment_status} />
              <Badge tone="brand">{humanise(e.employment_type)}</Badge>
              <Badge tone="neutral">{e.employee_code}</Badge>
            </div>
          </div>
          <div className="text-right">
            <p className="text-ink text-3xl font-semibold tabular-nums">{e.tenure_days}</p>
            <p className="text-ink-muted text-xs">
              {e.is_pre_joining ? 'days until joining' : 'days with the company'}
            </p>
          </div>
        </div>
      </GlassCard>

      {data.team?.is_manager && (
        <div className="grid gap-3 sm:grid-cols-2">
          <StatTile
            label="Direct reports"
            value={data.team.direct_reports}
            icon={Users}
            tone="brand"
            action={
              <Link
                to="/employees?view=chart"
                className="text-brand-600 dark:text-brand-300 text-xs font-medium hover:underline"
              >
                See the org chart
              </Link>
            }
          />
          <StatTile
            label="Total reports"
            value={data.team.total_reports}
            hint="Everyone beneath you, at any depth"
            icon={Network}
          />
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader title="Your details" icon={UserRound} />
          <CardBody>
            <dl className="grid gap-4 sm:grid-cols-2">
              <Field label="Employee code" value={e.employee_code} />
              <Field label="Name" value={e.full_name} />
              <Field label="Department" value={e.department_name} />
              <Field
                label="Work location"
                value={
                  e.work_location_name
                    ? `${e.work_location_name} (${e.work_location_timezone})`
                    : null
                }
              />
              <Field
                label="Reporting manager"
                value={
                  e.manager_name ? `${e.manager_name} (${e.manager_code})` : 'Not assigned'
                }
              />
              <Field label="Employment type" value={humanise(e.employment_type)} />
              <Field label="Date of joining" value={formatDate(e.date_of_joining)} />
              <Field
                label="Status"
                value={<EmploymentStatusBadge status={e.employment_status} />}
              />
              {e.probation_end_date && (
                <Field label="Probation ends" value={formatDate(e.probation_end_date)} />
              )}
            </dl>
            <Link
              to="/my-profile"
              className="text-brand-600 dark:text-brand-300 mt-5 inline-flex items-center gap-1 text-sm font-medium underline-offset-4 hover:underline"
            >
              Update my contact details
              <ArrowRight className="size-3.5" aria-hidden="true" />
            </Link>
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Security" icon={ShieldCheck} />
          <CardBody className="space-y-3 text-sm">
            <div className="flex items-start gap-2.5">
              <ShieldCheck
                className="text-success-600 mt-0.5 size-4 shrink-0"
                aria-hidden="true"
              />
              <span className="text-ink-soft">
                Your password is stored with Argon2id. Sessions use short-lived tokens that
                rotate.
              </span>
            </div>
            <div className="flex items-start gap-2.5">
              <CircleCheck
                className="text-success-600 mt-0.5 size-4 shrink-0"
                aria-hidden="true"
              />
              <span className="text-ink-soft">
                Changes to your record are recorded in an append-only audit log.
              </span>
            </div>
          </CardBody>
        </Card>
      </div>

      <PendingMetrics pending={data.not_yet_available} />
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Pieces                                                                     */
/* -------------------------------------------------------------------------- */

/** A name with its initials, for the short lists on this page. */
function PersonChip({
  name,
  detail,
  tone,
}: {
  name: string;
  detail: string;
  tone: 'brand' | 'warning';
}) {
  const tones: Record<'brand' | 'warning', string> = {
    brand: 'border-brand-200 dark:border-brand-800/60 hover:border-brand-500',
    warning: 'border-warning-600/35 hover:border-warning-600',
  };
  return (
    <span
      className={cn(
        'bg-raised flex items-center gap-2 rounded-full border py-1 pr-3 pl-1',
        'transition-colors duration-200',
        tones[tone],
      )}
    >
      <Avatar name={name} size="xs" />
      <span className="text-ink text-xs font-medium">{name}</span>
      <span className="text-ink-muted text-xs">{detail}</span>
    </span>
  );
}

/**
 * Horizontal bars for a small categorical breakdown.
 *
 * Bars are proportional to the largest value, not the total, so a dominant
 * category does not flatten the rest into invisibility.
 */
function BarList({ rows }: { rows: { label: string; value: number }[] }) {
  const max = Math.max(...rows.map((row) => row.value), 1);
  return (
    <ul className="space-y-2.5">
      {rows.map((row) => (
        <li key={row.label}>
          <div className="mb-1 flex items-baseline justify-between gap-3 text-sm">
            <span className="text-ink-soft truncate">{row.label}</span>
            <span className="text-ink font-medium tabular-nums">{row.value}</span>
          </div>
          <div className="bg-sunken h-1.5 overflow-hidden rounded-full">
            <div
              className="from-brand-500 to-brand-400 h-full rounded-full bg-linear-to-r transition-[width] duration-700"
              style={{ width: `${(row.value / max) * 100}%` }}
            />
          </div>
        </li>
      ))}
    </ul>
  );
}

function JoinerList({
  rows,
  empty,
}: {
  rows: { id: number; employee_code: string; full_name: string; date_of_joining: string }[];
  empty: string;
}) {
  if (rows.length === 0) return <p className="text-ink-muted text-sm">{empty}</p>;
  return (
    <ul className="divide-line -my-1 divide-y">
      {rows.map((row) => (
        <li key={row.id}>
          <Link
            to={`/employees/${row.id}`}
            className="hover:bg-sunken/70 -mx-2 flex items-center gap-3 rounded-[var(--radius-card)] px-2 py-2 transition-colors"
          >
            <Avatar name={row.full_name} size="sm" />
            <div className="min-w-0 flex-1">
              <p className="text-ink truncate text-sm font-medium">{row.full_name}</p>
              <p className="text-ink-muted truncate font-mono text-xs">{row.employee_code}</p>
            </div>
            <span className="text-ink-soft shrink-0 text-xs tabular-nums">
              {formatDate(row.date_of_joining)}
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  const empty = value === null || value === undefined || value === '';
  return (
    <div>
      <dt className="text-ink-muted text-xs font-medium tracking-wide uppercase">{label}</dt>
      <dd className={cn('mt-1 text-sm', empty ? 'text-ink-muted' : 'text-ink')}>
        {empty ? <>&mdash;</> : value}
      </dd>
    </div>
  );
}

/** Names the milestone for each metric that has no data source yet. */
function PendingMetrics({ pending }: { pending: Record<string, string> }) {
  const entries = Object.entries(pending);
  if (entries.length === 0) return null;

  const byMilestone = new Map<string, string[]>();
  for (const [metric, milestone] of entries) {
    byMilestone.set(milestone, [...(byMilestone.get(milestone) ?? []), metric]);
  }

  return (
    <Card>
      <CardHeader
        title="Not yet available"
        description="Listed so this dashboard does not imply figures it cannot produce. A zero here would read as a fact."
        icon={TriangleAlert}
      />
      <CardBody>
        <ul className="space-y-2 text-sm">
          {[...byMilestone.entries()]
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([milestone, metrics]) => (
              <li key={milestone} className="flex flex-wrap items-baseline gap-2">
                <Badge tone="neutral">{milestone}</Badge>
                <span className="text-ink-soft">
                  {metrics.map((metric) => humanise(metric)).join(', ')}
                </span>
              </li>
            ))}
        </ul>
      </CardBody>
    </Card>
  );
}
