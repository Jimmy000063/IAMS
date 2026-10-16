/**
 * Leave administration: the approval queue, types, policies and accrual.
 *
 * Tabs appear only where the caller holds the permission, but that is
 * visibility only - every endpoint behind them is authorized on the server.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  CalendarCog,
  CalendarHeart,
  CircleCheck,
  Inbox,
  Plus,
  RefreshCw,
  Scale,
  Timer,
  Archive,
} from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/Button';
import { DatePicker } from '@/components/ui/DatePicker';
import { SelectField, TextAreaField } from '@/components/ui/Field';
import { Alert, EmptyState, ErrorPanel, SkeletonRows } from '@/components/ui/Feedback';
import { Modal } from '@/components/ui/Modal';
import {
  Badge,
  Card,
  CardBody,
  CardHeader,
  PageHeader,
  TableShell,
  Td,
  Th,
} from '@/components/ui/Surface';
import { CompoffAdminPanel } from '@/features/compoff/CompoffAdminPanel';
import { HolidayAdminPanel } from '@/features/holidays/HolidayAdminPanel';
import { YearEndPanel } from '@/features/leave/YearEndPanel';
import { ApplicationTable } from '@/features/leave/ApplicationTable';
import { DecisionHistory } from '@/features/leave/DecisionHistory';
import { LeavePolicyForm } from '@/features/leave/LeavePolicyForm';
import { LeaveTypeForm } from '@/features/leave/LeaveTypeForm';
import {
  decideApplication,
  fetchLeaveTypes,
  fetchPendingApprovals,
  fetchPolicies,
  formatDays,
  leaveKeys,
  runAccrual,
  type LeaveApplicationSummary,
} from '@/features/leave/api';
import { useAuth } from '@/lib/auth';
import { formatIsoLong, todayIso, type IsoDate } from '@/lib/dates';
import { useToast } from '@/lib/toast';
import { cn } from '@/lib/utils';

type Tab = 'approvals' | 'types' | 'policies' | 'accrual' | 'compoff' | 'holidays' | 'yearend';

export function LeaveAdminPage() {
  const { can, canAny } = useAuth();
  const [tab, setTab] = useState<Tab>('approvals');
  const [historyId, setHistoryId] = useState<number | null>(null);

  const tabs: { id: Tab; label: string; icon: typeof Inbox; visible: boolean }[] = [
    {
      id: 'approvals',
      label: 'Approvals',
      icon: Inbox,
      visible: canAny('leave.approve_as_manager', 'leave.approve_hr'),
    },
    {
      id: 'types',
      label: 'Leave types',
      icon: Scale,
      visible: can('leave.manage_policy'),
    },
    {
      id: 'policies',
      label: 'Policies',
      icon: CalendarCog,
      visible: can('leave.manage_policy'),
    },
    {
      id: 'accrual',
      label: 'Accrual',
      icon: RefreshCw,
      visible: can('leave.run_year_end'),
    },
    {
      id: 'yearend',
      label: 'Year end',
      icon: Archive,
      visible: can('leave.run_year_end'),
    },
    {
      id: 'compoff',
      label: 'Comp-off',
      icon: Timer,
      visible: canAny('compoff.verify', 'compoff.approve_hr', 'compoff.approve_as_manager'),
    },
    {
      id: 'holidays',
      label: 'Holidays',
      icon: CalendarHeart,
      visible: can('holiday.manage'),
    },
  ];
  const visible = tabs.filter((item) => item.visible);
  const active = visible.some((item) => item.id === tab) ? tab : visible[0]?.id;

  return (
    <div>
      <PageHeader
        title="Leave administration"
        description="Review requests, define leave types and entitlements, and run accrual."
      />

      <div
        className="border-line mb-5 flex gap-1 overflow-x-auto border-b"
        role="tablist"
        aria-label="Leave administration sections"
      >
        {visible.map((item) => {
          const Icon = item.icon;
          const isActive = active === item.id;
          return (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={isActive}
              onClick={() => setTab(item.id)}
              className={cn(
                'flex items-center gap-2 border-b-2 px-4 py-2.5 text-sm font-medium whitespace-nowrap transition',
                isActive
                  ? 'border-brand-600 text-brand-700'
                  : 'text-ink-soft hover:text-ink border-transparent',
              )}
            >
              <Icon className="size-4" aria-hidden="true" />
              {item.label}
            </button>
          );
        })}
      </div>

      {active === 'approvals' && <ApprovalQueue onOpenHistory={setHistoryId} />}
      {active === 'types' && <LeaveTypesTab />}
      {active === 'policies' && <PoliciesTab />}
      {active === 'accrual' && <AccrualTab />}
      {active === 'yearend' && <YearEndPanel />}
      {active === 'compoff' && <CompoffAdminPanel />}
      {active === 'holidays' && <HolidayAdminPanel />}

      <DecisionHistory applicationId={historyId} onClose={() => setHistoryId(null)} />
    </div>
  );
}

/* ------------------------------------------------------------------ approvals */

function ApprovalQueue({ onOpenHistory }: { onOpenHistory: (id: number) => void }) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [pending, setPending] = useState<{
    application: LeaveApplicationSummary;
    approve: boolean;
  } | null>(null);
  const [comment, setComment] = useState('');

  const queue = useQuery({
    queryKey: leaveKeys.pending(),
    queryFn: fetchPendingApprovals,
  });

  const decide = useMutation({
    mutationFn: () => {
      if (!pending) throw new Error('Nothing to decide');
      return decideApplication(pending.application.id, pending.approve, comment || null);
    },
    onSuccess: (application) => {
      const approved = pending?.approve;
      setPending(null);
      setComment('');
      void queryClient.invalidateQueries({ queryKey: leaveKeys.all });
      if (!approved) {
        toast.success('Rejected. Nothing was deducted from the balance.');
      } else if (application.status === 'approved') {
        toast.success(
          `Approved. ${formatDays(application.paid_days)} day(s) deducted and attendance recalculated.`,
        );
      } else {
        toast.success('Approved at your stage. It now awaits HR; nothing is deducted yet.');
      }
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  if (queue.isPending) return <SkeletonRows rows={4} />;
  if (queue.isError)
    return <ErrorPanel error={queue.error} onRetry={() => void queue.refetch()} />;

  return (
    <div className="space-y-5">
      <Alert variant="info" title="What you can decide here">
        Only requests at a stage you may act on, and only for people who actually report to you.
        Nobody can decide their own application, whatever permissions they hold — that is
        enforced on the server, not by hiding the button.
      </Alert>

      <Card>
        <CardHeader
          title={`Awaiting a decision (${queue.data.length})`}
          description="Oldest first, because the request that has waited longest should be decided next."
        />
        <CardBody>
          {queue.data.length === 0 ? (
            <EmptyState
              icon={CircleCheck}
              title="Nothing waiting"
              description="Every request you can act on has been decided."
            />
          ) : (
            <ApplicationTable
              applications={queue.data}
              showEmployee
              onOpenHistory={onOpenHistory}
              actions={[
                {
                  label: 'Approve',
                  statuses: ['pending_manager', 'pending_hr'],
                  onAct: (application) => {
                    setComment('');
                    setPending({ application, approve: true });
                  },
                },
                {
                  label: 'Reject',
                  statuses: ['pending_manager', 'pending_hr'],
                  variant: 'danger',
                  onAct: (application) => {
                    setComment('');
                    setPending({ application, approve: false });
                  },
                },
              ]}
            />
          )}
        </CardBody>
      </Card>

      <Modal
        open={pending !== null}
        onClose={() => setPending(null)}
        title={pending?.approve ? 'Approve this request' : 'Reject this request'}
        description={
          pending?.approve
            ? 'If this is the final stage, the balance is deducted and attendance for those days is recalculated.'
            : 'The request is closed. Nothing is deducted, and the decision stays on the record.'
        }
        footer={
          <>
            <Button variant="secondary" onClick={() => setPending(null)}>
              Cancel
            </Button>
            <Button
              variant={pending?.approve ? 'primary' : 'danger'}
              onClick={() => decide.mutate()}
              disabled={decide.isPending}
            >
              {decide.isPending ? 'Recording…' : pending?.approve ? 'Approve' : 'Reject'}
            </Button>
          </>
        }
      >
        {pending && (
          <div className="space-y-4">
            <dl className="text-sm">
              <div className="flex justify-between gap-3">
                <dt className="text-ink-soft">Employee</dt>
                <dd className="text-ink font-medium">
                  {pending.application.employee_code} — {pending.application.employee_name}
                </dd>
              </div>
              <div className="mt-1 flex justify-between gap-3">
                <dt className="text-ink-soft">Dates</dt>
                <dd className="text-ink">
                  {formatIsoLong(pending.application.start_date)}
                  {pending.application.start_date !== pending.application.end_date &&
                    ` to ${formatIsoLong(pending.application.end_date)}`}
                </dd>
              </div>
              <div className="mt-1 flex justify-between gap-3">
                <dt className="text-ink-soft">Days</dt>
                <dd className="text-ink">{formatDays(pending.application.total_days)}</dd>
              </div>
            </dl>
            <TextAreaField
              label="Comment"
              value={comment}
              onChange={(event) => setComment(event.target.value)}
              rows={3}
              hint="Recorded against this stage and never overwritten."
            />
          </div>
        )}
      </Modal>
    </div>
  );
}

/* ---------------------------------------------------------------- leave types */

function LeaveTypesTab() {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<number | 'new' | null>(null);

  const types = useQuery({
    queryKey: leaveKeys.types(true),
    queryFn: () => fetchLeaveTypes(true),
  });

  if (types.isPending) return <SkeletonRows rows={4} />;
  if (types.isError)
    return <ErrorPanel error={types.error} onRetry={() => void types.refetch()} />;

  return (
    <div className="space-y-5">
      <Alert variant="warning" title="No leave types are created for you">
        Statutory entitlements — maternity leave, for instance — are set by law, and this system
        has not been given verified references for them. Inventing a quota would be worse than
        leaving the list empty, so you define the types your company actually uses and mark any
        whose entitlement still needs legal checking.
      </Alert>

      <Card>
        <CardHeader
          title="Leave types"
          description="What kinds of leave exist, and the rules that apply to each."
          action={
            <Button onClick={() => setEditing('new')}>
              <Plus className="size-4" aria-hidden="true" />
              Add a type
            </Button>
          }
        />
        <CardBody>
          {types.data.length === 0 ? (
            <EmptyState
              title="No leave types yet"
              description="Create the ones your company uses, such as casual, sick and unpaid leave."
              action={<Button onClick={() => setEditing('new')}>Add a type</Button>}
            />
          ) : (
            <TableShell caption="Leave types">
              <thead>
                <tr>
                  <Th>Type</Th>
                  <Th>Pay</Th>
                  <Th>Rules</Th>
                  <Th>Status</Th>
                  <Th className="text-right">Actions</Th>
                </tr>
              </thead>
              <tbody>
                {types.data.map((type) => (
                  <tr key={type.id}>
                    <Td>
                      <p className="font-medium">{type.name}</p>
                      <p className="text-ink-muted text-xs">{type.code}</p>
                    </Td>
                    <Td>
                      <Badge tone={type.is_paid ? 'success' : 'neutral'}>
                        {type.is_paid ? 'Paid' : 'Unpaid'}
                      </Badge>
                      {!type.deducts_balance && (
                        <p className="text-ink-muted mt-1 text-xs">No balance</p>
                      )}
                    </Td>
                    <Td className="text-ink-soft text-sm">
                      <ul className="space-y-0.5">
                        {type.allows_half_day && <li>Half days allowed</li>}
                        {type.counts_non_working_days && (
                          <li className="text-warning-700">Counted in calendar days</li>
                        )}
                        {type.min_notice_days > 0 && (
                          <li>{type.min_notice_days} day(s) notice</li>
                        )}
                        {type.max_consecutive_days && (
                          <li>Max {type.max_consecutive_days} consecutive</li>
                        )}
                        {type.min_service_days > 0 && (
                          <li>{type.min_service_days} day(s) service</li>
                        )}
                        {type.gender_restriction !== 'any' && (
                          <li>Restricted: {type.gender_restriction}</li>
                        )}
                        {type.attachment_required_above_days && (
                          <li>
                            Document above {formatDays(type.attachment_required_above_days)}{' '}
                            day(s)
                          </li>
                        )}
                      </ul>
                    </Td>
                    <Td>
                      {!type.is_active && <Badge tone="neutral">Inactive</Badge>}
                      {type.requires_legal_review && (
                        <Badge tone="warning">Needs legal review</Badge>
                      )}
                    </Td>
                    <Td className="text-right">
                      <Button variant="ghost" size="sm" onClick={() => setEditing(type.id)}>
                        Edit
                      </Button>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </TableShell>
          )}
        </CardBody>
      </Card>

      <LeaveTypeForm
        open={editing !== null}
        existing={
          typeof editing === 'number'
            ? (types.data.find((item) => item.id === editing) ?? null)
            : null
        }
        onClose={() => setEditing(null)}
        onSaved={() => {
          setEditing(null);
          void queryClient.invalidateQueries({ queryKey: leaveKeys.all });
        }}
      />
    </div>
  );
}

/* ------------------------------------------------------------------- policies */

function PoliciesTab() {
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);

  const types = useQuery({
    queryKey: leaveKeys.types(true),
    queryFn: () => fetchLeaveTypes(true),
  });
  const policies = useQuery({ queryKey: leaveKeys.policies(), queryFn: () => fetchPolicies() });

  if (policies.isPending || types.isPending) return <SkeletonRows rows={4} />;
  if (policies.isError) {
    return <ErrorPanel error={policies.error} onRetry={() => void policies.refetch()} />;
  }

  const typeName = (id: number) =>
    types.data?.find((item) => item.id === id)?.name ?? `Type #${id}`;

  return (
    <div className="space-y-5">
      <Alert variant="info" title="Policies are superseded, not edited">
        To change an entitlement from a date, create a new policy from that date and close the
        previous one. Editing in place would rewrite how earlier applications were decided —
        each application records the policy version that governed it precisely so that cannot
        happen.
      </Alert>

      <Card>
        <CardHeader
          title="Leave policies"
          description="Quota, accrual and carry-forward rules, each effective from a date."
          action={
            <Button
              onClick={() => setCreating(true)}
              disabled={(types.data ?? []).length === 0}
            >
              <Plus className="size-4" aria-hidden="true" />
              Add a policy
            </Button>
          }
        />
        <CardBody>
          {(types.data ?? []).length === 0 ? (
            <EmptyState
              title="Create a leave type first"
              description="A policy sets the entitlement for a type, so the type has to exist."
            />
          ) : policies.data.length === 0 ? (
            <EmptyState
              title="No policies yet"
              description="Without one, a leave type that deducts a balance has no entitlement to draw on."
              action={<Button onClick={() => setCreating(true)}>Add a policy</Button>}
            />
          ) : (
            <TableShell caption="Leave policies">
              <thead>
                <tr>
                  <Th>Type</Th>
                  <Th>Policy</Th>
                  <Th>In force</Th>
                  <Th>Quota</Th>
                  <Th>Accrual</Th>
                  <Th>Limits</Th>
                </tr>
              </thead>
              <tbody>
                {policies.data.map((policy) => (
                  <tr key={policy.id}>
                    <Td>{typeName(policy.leave_type_id)}</Td>
                    <Td>
                      <p>{policy.name}</p>
                      {policy.requires_legal_review && (
                        <Badge tone="warning">Needs legal review</Badge>
                      )}
                    </Td>
                    <Td className="text-sm whitespace-nowrap">
                      {formatIsoLong(policy.effective_from)}
                      {policy.effective_to ? (
                        <> to {formatIsoLong(policy.effective_to)}</>
                      ) : (
                        <span className="text-ink-muted"> onwards</span>
                      )}
                    </Td>
                    <Td className="tabular-nums">{formatDays(policy.annual_quota_days)}</Td>
                    <Td className="text-sm">
                      {policy.accrual_frequency.replace('_', ' ')}
                      <p className="text-ink-muted text-xs">
                        rounds {policy.accrual_rounding.replace('_', ' ')}
                      </p>
                      {policy.prorate_on_joining && (
                        <p className="text-ink-muted text-xs">prorated for joiners</p>
                      )}
                    </Td>
                    <Td className="text-ink-soft text-sm">
                      <ul className="space-y-0.5">
                        {policy.max_accumulation_days && (
                          <li>Max {formatDays(policy.max_accumulation_days)} held</li>
                        )}
                        {policy.carry_forward_cap_days && (
                          <li>Carry {formatDays(policy.carry_forward_cap_days)}</li>
                        )}
                        {policy.encashment_allowed && <li>Encashable</li>}
                      </ul>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </TableShell>
          )}
        </CardBody>
      </Card>

      <LeavePolicyForm
        open={creating}
        types={types.data ?? []}
        onClose={() => setCreating(false)}
        onSaved={() => {
          setCreating(false);
          void queryClient.invalidateQueries({ queryKey: leaveKeys.all });
        }}
      />
    </div>
  );
}

/* -------------------------------------------------------------------- accrual */

function AccrualTab() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [asOf, setAsOf] = useState<IsoDate>(todayIso());

  const types = useQuery({ queryKey: leaveKeys.types(), queryFn: () => fetchLeaveTypes() });
  const [typeId, setTypeId] = useState<string>('');

  const run = useMutation({
    mutationFn: () =>
      runAccrual({ as_of: asOf, leave_type_id: typeId ? Number(typeId) : null }),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: leaveKeys.all });
      if (result.entries_posted === 0 && result.already_posted > 0) {
        toast.show({
          variant: 'info',
          title: `Period ${result.period_key} was already credited.`,
          description: `${result.already_posted} entr(ies) were recognised as already posted, so nothing was credited twice.`,
        });
      } else if (result.entries_posted === 0) {
        toast.show({
          variant: 'warning',
          title: 'Nothing accrued.',
          description: 'Check that a policy is in force for the employees you expected.',
        });
      } else {
        toast.success(
          `${result.entries_posted} credit(s) posted for ${result.period_key}: ${formatDays(result.days_credited)} day(s).`,
        );
      }
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  return (
    <div className="space-y-5">
      <Alert variant="info" title="Running this twice is harmless">
        Each credit is keyed by employee, leave type and period, so a second run recognises what
        is already posted rather than crediting a balance twice. That is the single most
        damaging mistake in leave management and the hardest to notice afterwards, so it is
        prevented by a database constraint rather than by remembering not to click twice.
      </Alert>

      <Card>
        <CardHeader
          title="Run accrual"
          description="Credits the accrual period containing the date below. Pick a date inside a past period to catch up a missed run."
        />
        <CardBody className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <DatePicker label="As of" value={asOf} onChange={setAsOf} />
            <SelectField
              label="Leave type"
              value={typeId}
              onChange={(event) => setTypeId(event.target.value)}
              options={[
                { value: '', label: 'All types' },
                ...(types.data ?? []).map((item) => ({
                  value: String(item.id),
                  label: item.name,
                })),
              ]}
            />
          </div>
          <Button onClick={() => run.mutate()} disabled={run.isPending}>
            <RefreshCw className="size-4" aria-hidden="true" />
            {run.isPending ? 'Running…' : 'Run accrual'}
          </Button>

          {run.data && (
            <dl className="grid gap-3 sm:grid-cols-3 lg:grid-cols-5">
              {[
                ['Period', run.data.period_key],
                ['Credits posted', String(run.data.entries_posted)],
                ['Already credited', String(run.data.already_posted)],
                ['Days credited', formatDays(run.data.days_credited)],
                ['No policy', String(run.data.skipped_no_policy)],
                ['Not employed', String(run.data.skipped_not_employed)],
                ['At ceiling', String(run.data.skipped_at_ceiling)],
              ].map(([label, value]) => (
                <div key={label} className="border-line rounded-lg border p-3">
                  <dt className="text-ink-soft text-xs font-medium tracking-wide uppercase">
                    {label}
                  </dt>
                  <dd className="text-ink mt-1 text-lg font-semibold">{value}</dd>
                </div>
              ))}
            </dl>
          )}

          <p className="text-ink-soft text-sm">
            A scheduled run is also available from the command line as{' '}
            <code className="bg-sunken rounded px-1 py-0.5 text-xs">iams accrue-leave</code>,
            intended for a monthly cron entry.
          </p>
        </CardBody>
      </Card>
    </div>
  );
}
