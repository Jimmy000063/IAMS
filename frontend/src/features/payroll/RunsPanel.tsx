/**
 * Payroll runs: open, calculate, review, approve, finalize, publish.
 *
 * The panel shows the lifecycle as the lifecycle, because each step is a
 * separate decision with a separate permission - and because somebody looking
 * at a month's pay should be able to see how far it has got and who moved it.
 *
 * The review flags are shown prominently and never hidden: a negative net or a
 * month with no attendance is occasionally correct, so the system does not block
 * it, which makes showing it the only safeguard left.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  BadgeCheck,
  CalendarPlus,
  Calculator,
  CircleAlert,
  Eye,
  Lock,
  Send,
  Table2,
  Undo2,
} from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/Button';
import { SelectField, TextAreaField } from '@/components/ui/Field';
import { Alert, EmptyState, ErrorPanel, SkeletonRows } from '@/components/ui/Feedback';
import { Modal } from '@/components/ui/Modal';
import { Badge, Card, CardBody, CardHeader, TableShell, Td, Th } from '@/components/ui/Surface';
import {
  approveRun,
  calculateRun,
  cancelRun,
  createRun,
  fetchReconciliation,
  fetchRegister,
  fetchRunPayslips,
  fetchRuns,
  finalizeRun,
  publishRun,
  reviewRun,
  sendRunBack,
  STATUS_TONES,
  type PayrollRun,
} from '@/features/payroll/api';
import { todayIso } from '@/lib/dates';
import { useToast } from '@/lib/toast';

const MONTHS = [
  'January',
  'February',
  'March',
  'April',
  'May',
  'June',
  'July',
  'August',
  'September',
  'October',
  'November',
  'December',
];

export function RunsPanel() {
  const toast = useToast();
  const queryClient = useQueryClient();

  const today = todayIso();
  const [year, setYear] = useState(today.slice(0, 4));
  const [month, setMonth] = useState(String(Number(today.slice(5, 7))));
  const [openRun, setOpenRun] = useState<number | null>(null);
  const [view, setView] = useState<'payslips' | 'register' | 'reconciliation'>('payslips');
  const [reasonFor, setReasonFor] = useState<{
    run: PayrollRun;
    action: 'back' | 'cancel';
  } | null>(null);
  const [reason, setReason] = useState('');

  const runs = useQuery({
    queryKey: ['payroll', 'runs', year],
    queryFn: () => fetchRuns(Number(year)),
  });

  const payslips = useQuery({
    queryKey: ['payroll', 'run-payslips', openRun],
    queryFn: () => fetchRunPayslips(openRun as number),
    enabled: openRun !== null && view === 'payslips',
  });

  const register = useQuery({
    queryKey: ['payroll', 'register', openRun],
    queryFn: () => fetchRegister(openRun as number),
    enabled: openRun !== null && view === 'register',
  });

  const reconciliation = useQuery({
    queryKey: ['payroll', 'reconciliation', openRun],
    queryFn: () => fetchReconciliation(openRun as number),
    enabled: openRun !== null && view === 'reconciliation',
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['payroll'] });
  };

  const open = useMutation({
    mutationFn: () => createRun({ year: Number(year), month: Number(month) }),
    onSuccess: (run) => {
      invalidate();
      setOpenRun(run.id);
      toast.success(`Run #${run.id} opened as a draft.`);
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  const calculate = useMutation({
    mutationFn: calculateRun,
    onSuccess: (result) => {
      invalidate();
      if (result.skipped.length > 0) {
        const first = result.skipped[0];
        toast.show({
          variant: 'warning',
          title: `${result.payslips} payslip(s) calculated, ${result.skipped.length} skipped.`,
          ...(first ? { description: first.reason } : {}),
        });
      } else {
        toast.success(
          `${result.payslips} payslip(s): net ${result.total_net} ${result.currency}.`,
        );
      }
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  const transition = useMutation({
    mutationFn: ({ id, action }: { id: number; action: 'review' | 'approve' | 'finalize' }) =>
      action === 'review'
        ? reviewRun(id)
        : action === 'approve'
          ? approveRun(id)
          : finalizeRun(id),
    onSuccess: (run) => {
      invalidate();
      toast.success(`Run #${run.id} is now ${run.status}.`);
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  const publish = useMutation({
    mutationFn: publishRun,
    onSuccess: (result) => {
      invalidate();
      toast.success(
        result.published === 0
          ? 'Already published; nothing was released again.'
          : `${result.published} payslip(s) released, ${result.notifications_created} employee(s) notified.`,
      );
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  const withReason = useMutation({
    mutationFn: ({
      id,
      action,
      why,
    }: {
      id: number;
      action: 'back' | 'cancel';
      why: string;
    }) => (action === 'back' ? sendRunBack(id, why) : cancelRun(id, why)),
    onSuccess: (run) => {
      invalidate();
      setReasonFor(null);
      setReason('');
      toast.success(`Run #${run.id} is now ${run.status}.`);
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  const rows = runs.data ?? [];
  const currentYear = Number(today.slice(0, 4));

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader
          title="Payroll runs"
          description="draft, calculated, reviewed, approved, finalized. Each step is a separate decision, and nobody may review what they calculated or approve what they reviewed."
          action={
            <div className="flex flex-wrap items-end gap-2">
              <div className="w-28">
                <SelectField
                  label="Year"
                  value={year}
                  onChange={(event) => setYear(event.target.value)}
                  options={[currentYear - 1, currentYear, currentYear + 1].map((item) => ({
                    value: String(item),
                    label: String(item),
                  }))}
                />
              </div>
              <div className="w-36">
                <SelectField
                  label="Month"
                  value={month}
                  onChange={(event) => setMonth(event.target.value)}
                  options={MONTHS.map((name, index) => ({
                    value: String(index + 1),
                    label: name,
                  }))}
                />
              </div>
              <Button onClick={() => open.mutate()} disabled={open.isPending}>
                <CalendarPlus className="size-4" aria-hidden="true" />
                Open a run
              </Button>
            </div>
          }
        />
        <CardBody>
          {runs.isPending ? (
            <SkeletonRows rows={4} />
          ) : runs.isError ? (
            <ErrorPanel error={runs.error} onRetry={() => void runs.refetch()} />
          ) : rows.length === 0 ? (
            <EmptyState
              title={`No payroll runs in ${year}`}
              description="Open one for a month to begin. A run starts as a draft and reaches nobody until it is finalized and released."
            />
          ) : (
            <TableShell caption="Payroll runs">
              <thead>
                <tr>
                  <Th>Run</Th>
                  <Th>Status</Th>
                  <Th className="text-right">Employees</Th>
                  <Th className="text-right">Gross</Th>
                  <Th className="text-right">Deductions</Th>
                  <Th className="text-right">Net</Th>
                  <Th>Actions</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((run) => (
                  <tr key={run.id}>
                    <Td>
                      <p className="font-medium">#{run.id}</p>
                      <p className="text-ink-soft text-xs">{run.run_type}</p>
                    </Td>
                    <Td>
                      <Badge tone={STATUS_TONES[run.status] ?? 'neutral'}>{run.status}</Badge>
                      {(run.unverified_domains ?? []).length > 0 && (
                        <p className="text-warning-700 mt-1 text-xs">
                          unverified: {(run.unverified_domains ?? []).join(', ')}
                        </p>
                      )}
                    </Td>
                    <Td className="text-right tabular-nums">{run.employee_count}</Td>
                    <Td className="text-right tabular-nums">{run.total_gross}</Td>
                    <Td className="text-right tabular-nums">{run.total_deductions}</Td>
                    <Td className="text-right font-semibold tabular-nums">{run.total_net}</Td>
                    <Td>
                      <div className="flex flex-wrap gap-1">
                        {(run.status === 'draft' || run.status === 'calculated') && (
                          <Button
                            variant="secondary"
                            onClick={() => calculate.mutate(run.id)}
                            disabled={calculate.isPending}
                          >
                            <Calculator className="size-3.5" aria-hidden="true" />
                            Calculate
                          </Button>
                        )}
                        {run.status === 'calculated' && (
                          <Button
                            variant="secondary"
                            onClick={() => transition.mutate({ id: run.id, action: 'review' })}
                          >
                            <Eye className="size-3.5" aria-hidden="true" />
                            Review
                          </Button>
                        )}
                        {run.status === 'reviewed' && (
                          <Button
                            variant="secondary"
                            onClick={() => transition.mutate({ id: run.id, action: 'approve' })}
                          >
                            <BadgeCheck className="size-3.5" aria-hidden="true" />
                            Approve
                          </Button>
                        )}
                        {run.status === 'approved' && (
                          <Button
                            onClick={() =>
                              transition.mutate({ id: run.id, action: 'finalize' })
                            }
                          >
                            <Lock className="size-3.5" aria-hidden="true" />
                            Finalize
                          </Button>
                        )}
                        {run.status === 'finalized' && (
                          <Button variant="secondary" onClick={() => publish.mutate(run.id)}>
                            <Send className="size-3.5" aria-hidden="true" />
                            Release payslips
                          </Button>
                        )}
                        {['calculated', 'reviewed', 'approved'].includes(run.status) && (
                          <Button
                            variant="ghost"
                            onClick={() => setReasonFor({ run, action: 'back' })}
                          >
                            <Undo2 className="size-3.5" aria-hidden="true" />
                            Send back
                          </Button>
                        )}
                        <Button
                          variant="ghost"
                          onClick={() => setOpenRun(openRun === run.id ? null : run.id)}
                        >
                          <Table2 className="size-3.5" aria-hidden="true" />
                          {openRun === run.id ? 'Hide' : 'Open'}
                        </Button>
                      </div>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </TableShell>
          )}
        </CardBody>
      </Card>

      {openRun !== null && (
        <Card>
          <CardHeader
            title={`Run #${openRun}`}
            description="The payslips, the register the bank transfer is prepared from, and the reconciliation that has to balance."
            action={
              <div className="flex gap-1">
                {(['payslips', 'register', 'reconciliation'] as const).map((item) => (
                  <Button
                    key={item}
                    variant={view === item ? 'secondary' : 'ghost'}
                    onClick={() => setView(item)}
                  >
                    {item}
                  </Button>
                ))}
              </div>
            }
          />
          <CardBody className="space-y-4">
            {view === 'payslips' &&
              (payslips.isPending ? (
                <SkeletonRows rows={4} />
              ) : payslips.isError ? (
                <ErrorPanel error={payslips.error} onRetry={() => void payslips.refetch()} />
              ) : (
                <TableShell caption="Payslips in this run">
                  <thead>
                    <tr>
                      <Th>Employee</Th>
                      <Th className="text-right">Payable</Th>
                      <Th className="text-right">LOP</Th>
                      <Th className="text-right">Gross</Th>
                      <Th className="text-right">Deductions</Th>
                      <Th className="text-right">Net</Th>
                      <Th>Needs a look</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {(payslips.data ?? []).map((item) => (
                      <tr key={item.id}>
                        <Td>
                          <p className="font-medium">{item.employee_code}</p>
                          <p className="text-ink-soft text-xs">{item.employee_name}</p>
                        </Td>
                        <Td className="text-right tabular-nums">{item.payable_days}</Td>
                        <Td className="text-right tabular-nums">{item.lop_days}</Td>
                        <Td className="text-right tabular-nums">{item.gross_earnings}</Td>
                        <Td className="text-right tabular-nums">{item.total_deductions}</Td>
                        <Td className="text-right font-semibold tabular-nums">
                          {item.net_pay}
                        </Td>
                        <Td>
                          {(item.review_flags ?? []).length === 0 ? (
                            <span className="text-ink-muted text-xs">-</span>
                          ) : (
                            <ul className="space-y-1">
                              {(item.review_flags ?? []).map((flag) => (
                                <li key={flag.code} className="text-warning-700 text-xs">
                                  <CircleAlert
                                    className="mr-1 inline size-3"
                                    aria-hidden="true"
                                  />
                                  {flag.message}
                                </li>
                              ))}
                            </ul>
                          )}
                        </Td>
                      </tr>
                    ))}
                  </tbody>
                </TableShell>
              ))}

            {view === 'register' &&
              (register.isPending ? (
                <SkeletonRows rows={4} />
              ) : register.isError ? (
                <ErrorPanel error={register.error} onRetry={() => void register.refetch()} />
              ) : register.data ? (
                <>
                  <Alert variant="info" title="This is the data, not the transfer">
                    A bank transfer and a statutory return are prepared from this register by a
                    person. Neither is performed by this system.
                  </Alert>
                  <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                    {[
                      ['Gross', register.data.total_gross],
                      ['Deductions', register.data.total_deductions],
                      ['Employer cost', register.data.total_employer_cost],
                      ['Net payable', register.data.total_net],
                    ].map(([label, value]) => (
                      <div key={label} className="border-line rounded-lg border p-3">
                        <dt className="text-ink-soft text-xs font-medium tracking-wide uppercase">
                          {label}
                        </dt>
                        <dd className="text-ink mt-1 text-lg font-semibold tabular-nums">
                          {value}
                        </dd>
                      </div>
                    ))}
                  </dl>
                  <div className="border-line rounded-lg border p-3">
                    <p className="text-ink mb-1 text-sm font-medium">
                      Rules and settings this run used
                    </p>
                    <p className="text-ink-soft text-xs">
                      Recorded on the run, which is what keeps it reproducible after a rate or a
                      setting changes.
                    </p>
                    <pre className="text-ink-soft mt-2 max-h-40 overflow-auto text-xs">
                      {JSON.stringify(
                        {
                          rule_sets: register.data.rule_sets_used,
                          settings: register.data.settings_used,
                        },
                        null,
                        2,
                      )}
                    </pre>
                  </div>
                </>
              ) : null)}

            {view === 'reconciliation' &&
              (reconciliation.isPending ? (
                <SkeletonRows rows={3} />
              ) : reconciliation.isError ? (
                <ErrorPanel
                  error={reconciliation.error}
                  onRetry={() => void reconciliation.refetch()}
                />
              ) : reconciliation.data ? (
                <>
                  <Alert
                    variant={reconciliation.data.balanced ? 'success' : 'danger'}
                    title={
                      reconciliation.data.balanced
                        ? 'The run adds up to its own lines'
                        : 'The stored totals disagree with the lines'
                    }
                  >
                    Every figure here is re-summed from the payslip lines rather than read from
                    the stored totals. A payroll that cannot prove it adds up should not be
                    paid.
                  </Alert>
                  <TableShell caption="Reconciliation">
                    <thead>
                      <tr>
                        <Th>Figure</Th>
                        <Th className="text-right">From the lines</Th>
                        <Th className="text-right">On the payslips</Th>
                        <Th className="text-right">On the run</Th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr>
                        <Td>Gross</Td>
                        <Td className="text-right tabular-nums">
                          {reconciliation.data.lines_gross}
                        </Td>
                        <Td className="text-right tabular-nums">
                          {reconciliation.data.payslips_gross}
                        </Td>
                        <Td className="text-right tabular-nums">
                          {reconciliation.data.run_gross}
                        </Td>
                      </tr>
                      <tr>
                        <Td>Deductions</Td>
                        <Td className="text-right tabular-nums">
                          {reconciliation.data.lines_deductions}
                        </Td>
                        <Td className="text-right tabular-nums">
                          {reconciliation.data.payslips_deductions}
                        </Td>
                        <Td className="text-right tabular-nums">
                          {reconciliation.data.run_deductions}
                        </Td>
                      </tr>
                      <tr>
                        <Td>Net</Td>
                        <Td className="text-right tabular-nums">-</Td>
                        <Td className="text-right tabular-nums">
                          {reconciliation.data.payslips_net}
                        </Td>
                        <Td className="text-right tabular-nums">
                          {reconciliation.data.run_net}
                        </Td>
                      </tr>
                    </tbody>
                  </TableShell>
                </>
              ) : null)}
          </CardBody>
        </Card>
      )}

      <Modal
        open={reasonFor !== null}
        onClose={() => {
          setReasonFor(null);
          setReason('');
        }}
        title={
          reasonFor?.action === 'cancel' ? 'Cancel this run' : 'Send this run back to draft'
        }
        description={
          reasonFor?.action === 'cancel'
            ? 'The row is kept, because its id may already have been quoted.'
            : 'Recalculating under a reviewer would invalidate their review without telling anybody, so the step is recorded with a reason.'
        }
        footer={
          <>
            <Button
              variant="ghost"
              onClick={() => {
                setReasonFor(null);
                setReason('');
              }}
            >
              Cancel
            </Button>
            <Button
              onClick={() =>
                reasonFor &&
                withReason.mutate({
                  id: reasonFor.run.id,
                  action: reasonFor.action,
                  why: reason,
                })
              }
              disabled={reason.trim().length < 3 || withReason.isPending}
            >
              Confirm
            </Button>
          </>
        }
      >
        <TextAreaField
          label="Reason"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          rows={3}
          required
        />
      </Modal>
    </div>
  );
}
