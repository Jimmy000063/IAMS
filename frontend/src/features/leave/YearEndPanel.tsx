/**
 * Leave-year closing, and the reconciliation that has to balance.
 *
 * The dry run is the default and the panel says so: closing a year moves
 * everybody's entitlement at once, so reading the outcome before applying it is
 * the normal way to use this.
 */

import { useMutation, useQuery } from '@tanstack/react-query';
import { Archive, CircleAlert, Play, Table2 } from 'lucide-react';
import { useState } from 'react';

import { api } from '@/lib/api';
import { Button } from '@/components/ui/Button';
import { SelectField } from '@/components/ui/Field';
import { Alert, EmptyState, ErrorPanel, SkeletonRows } from '@/components/ui/Feedback';
import { Badge, Card, CardBody, CardHeader, TableShell, Td, Th } from '@/components/ui/Surface';
import { formatDays } from '@/features/leave/api';
import { todayIso } from '@/lib/dates';
import { useToast } from '@/lib/toast';

interface YearEndRow {
  employee_id: number;
  employee_code: string;
  leave_type_code: string;
  opening_days: string;
  accrued_days: string;
  used_days: string;
  closing_days: string;
  carried_days: string;
  encashed_days: string;
  lapsed_days: string;
  carried_expires_on: string | null;
  reason: string;
  action: string;
}

interface YearEndResult {
  leave_year: number;
  dry_run: boolean;
  company_rule: string;
  employees_considered: number;
  employees_closed: number;
  already_closed: number;
  skipped_no_policy: number;
  skipped_nothing_to_close: number;
  carried_days: string;
  encashed_days: string;
  lapsed_days: string;
  rows: YearEndRow[];
}

interface ReconciliationRow {
  employee_id: number;
  employee_code: string | null;
  employee_name: string | null;
  leave_type_code: string | null;
  leave_year: number;
  opening_days: string;
  accrued_days: string;
  adjusted_days: string;
  used_days: string;
  carried_out_days: string;
  lapsed_days: string;
  encashed_days: string;
  closing_days: string;
}

const runYearEnd = (leaveYear: number, dryRun: boolean) =>
  api.post<YearEndResult>('/leave/year-end/run', {
    leave_year: leaveYear,
    dry_run: dryRun,
  });

const expireCarried = () =>
  api.post<{
    lots_examined: number;
    entries_posted: number;
    already_expired: number;
    nothing_left_to_expire: number;
    days_expired: string;
  }>('/leave/year-end/expire-carried', {});

const fetchReconciliation = (leaveYear: number) =>
  api.get<ReconciliationRow[]>('/leave/year-end/reconciliation', {
    query: { leave_year: leaveYear },
  });

export function YearEndPanel() {
  const toast = useToast();
  const currentYear = Number(todayIso().slice(0, 4));
  const [leaveYear, setLeaveYear] = useState(String(currentYear - 1));
  const [result, setResult] = useState<YearEndResult | null>(null);
  const [showReconciliation, setShowReconciliation] = useState(false);

  const reconciliation = useQuery({
    queryKey: ['leave', 'reconciliation', leaveYear],
    queryFn: () => fetchReconciliation(Number(leaveYear)),
    enabled: showReconciliation,
  });

  const run = useMutation({
    mutationFn: (dryRun: boolean) => runYearEnd(Number(leaveYear), dryRun),
    onSuccess: (data) => {
      setResult(data);
      if (data.dry_run) {
        toast.show({
          variant: 'info',
          title: `Preview for leave year ${data.leave_year}.`,
          description: 'Nothing was written. Review the rows, then apply.',
        });
      } else if (data.employees_closed === 0 && data.already_closed > 0) {
        toast.show({
          variant: 'info',
          title: 'Already closed.',
          description: `${data.already_closed} employee/type pair(s) had already been closed, so nothing was posted again.`,
        });
      } else {
        toast.success(
          `Closed ${data.employees_closed} pair(s): ${formatDays(data.carried_days)} carried, ${formatDays(data.lapsed_days)} lapsed.`,
        );
      }
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  const expire = useMutation({
    mutationFn: expireCarried,
    onSuccess: (data) => {
      toast.success(
        data.entries_posted === 0
          ? 'Nothing was due to expire.'
          : `${data.entries_posted} carried lot(s) expired, ${formatDays(data.days_expired)} day(s) lost.`,
      );
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  return (
    <div className="space-y-5">
      <Alert
        variant="warning"
        title="Closing a year moves everybody's entitlement"
        icon={CircleAlert}
      >
        Run the preview first and read the rows. The job is safe to re-run - every entry is
        keyed by employee, leave type and closing year, so a second run posts nothing - but the
        carry-forward, encashment and lapse amounts are worth checking before they land in
        people's balances.
      </Alert>

      <Card>
        <CardHeader
          title="Close a leave year"
          description="Carry forward up to each policy's cap, encash or lapse the rest, and open the next year with the carried balance."
          action={
            <div className="w-32">
              <SelectField
                label="Leave year"
                value={leaveYear}
                onChange={(event) => {
                  setLeaveYear(event.target.value);
                  setResult(null);
                }}
                options={[currentYear - 2, currentYear - 1, currentYear].map((year) => ({
                  value: String(year),
                  label: `${year}-${String(year + 1).slice(-2)}`,
                }))}
              />
            </div>
          }
        />
        <CardBody className="space-y-4">
          <div className="flex flex-wrap gap-2">
            <Button
              variant="secondary"
              onClick={() => run.mutate(true)}
              disabled={run.isPending}
            >
              <Play className="size-4" aria-hidden="true" />
              {run.isPending ? 'Working…' : 'Preview'}
            </Button>
            <Button
              onClick={() => run.mutate(false)}
              disabled={run.isPending || result === null || !result.dry_run}
              title={
                result === null || !result.dry_run
                  ? 'Run a preview first so you can see what will happen.'
                  : undefined
              }
            >
              <Archive className="size-4" aria-hidden="true" />
              Apply
            </Button>
            <Button
              variant="secondary"
              onClick={() => expire.mutate()}
              disabled={expire.isPending}
            >
              {expire.isPending ? 'Working…' : 'Expire carried-forward credit'}
            </Button>
            <Button
              variant="ghost"
              onClick={() => setShowReconciliation((previous) => !previous)}
            >
              <Table2 className="size-4" aria-hidden="true" />
              {showReconciliation ? 'Hide' : 'Show'} reconciliation
            </Button>
          </div>

          {result && (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={result.dry_run ? 'warning' : 'success'}>
                  {result.dry_run ? 'Preview only - nothing written' : 'Applied'}
                </Badge>
                <span className="text-ink-soft text-sm">
                  rule: {result.company_rule.replace(/_/g, ' ')}
                </span>
              </div>

              <dl className="grid gap-3 sm:grid-cols-3 lg:grid-cols-5">
                {[
                  ['Carried', formatDays(result.carried_days)],
                  ['Encashed', formatDays(result.encashed_days)],
                  ['Lapsed', formatDays(result.lapsed_days)],
                  ['Pairs closed', String(result.employees_closed)],
                  ['Already closed', String(result.already_closed)],
                  ['No policy', String(result.skipped_no_policy)],
                  ['Nothing to close', String(result.skipped_nothing_to_close)],
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

              {result.rows.length > 0 && (
                <TableShell caption="Year-end outcome per employee and leave type">
                  <thead>
                    <tr>
                      <Th>Employee</Th>
                      <Th>Type</Th>
                      <Th className="text-right">Closing</Th>
                      <Th className="text-right">Carried</Th>
                      <Th className="text-right">Encashed</Th>
                      <Th className="text-right">Lapsed</Th>
                      <Th>Why</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.rows.map((row, index) => (
                      <tr key={`${row.employee_id}-${row.leave_type_code}-${index}`}>
                        <Td>{row.employee_code}</Td>
                        <Td>{row.leave_type_code}</Td>
                        <Td className="text-right tabular-nums">
                          {formatDays(row.closing_days)}
                        </Td>
                        <Td className="text-right tabular-nums">
                          {formatDays(row.carried_days)}
                          {row.carried_expires_on && (
                            <p className="text-ink-muted text-xs">
                              expires {row.carried_expires_on}
                            </p>
                          )}
                        </Td>
                        <Td className="text-right tabular-nums">
                          {formatDays(row.encashed_days)}
                        </Td>
                        <Td className="text-right tabular-nums">
                          {formatDays(row.lapsed_days)}
                        </Td>
                        <Td className="text-ink-soft max-w-sm text-sm">{row.reason}</Td>
                      </tr>
                    ))}
                  </tbody>
                </TableShell>
              )}
            </>
          )}

          <p className="text-ink-soft text-sm">
            Also available from the command line as{' '}
            <code className="bg-sunken rounded px-1 py-0.5 text-xs">
              iams close-leave-year --leave-year {leaveYear}
            </code>
            , which likewise defaults to a dry run.
          </p>
        </CardBody>
      </Card>

      {showReconciliation && (
        <Card>
          <CardHeader
            title={`Reconciliation for ${leaveYear}-${String(Number(leaveYear) + 1).slice(-2)}`}
            description="Every figure is a sum over the ledger by transaction type, not a stored total - so if this did not add up, the ledger itself would be at fault."
          />
          <CardBody>
            {reconciliation.isPending ? (
              <SkeletonRows rows={4} />
            ) : reconciliation.isError ? (
              <ErrorPanel
                error={reconciliation.error}
                onRetry={() => void reconciliation.refetch()}
              />
            ) : (reconciliation.data ?? []).length === 0 ? (
              <EmptyState
                title="Nothing moved in that leave year"
                description="No ledger entries fall inside it."
              />
            ) : (
              <TableShell caption="Leave reconciliation">
                <thead>
                  <tr>
                    <Th>Employee</Th>
                    <Th>Type</Th>
                    <Th className="text-right">Opening</Th>
                    <Th className="text-right">Accrued</Th>
                    <Th className="text-right">Adjusted</Th>
                    <Th className="text-right">Used</Th>
                    <Th className="text-right">Carried out</Th>
                    <Th className="text-right">Lapsed</Th>
                    <Th className="text-right">Encashed</Th>
                    <Th className="text-right">Closing</Th>
                  </tr>
                </thead>
                <tbody>
                  {(reconciliation.data ?? []).map((row, index) => (
                    <tr key={`${row.employee_id}-${row.leave_type_code}-${index}`}>
                      <Td>
                        <p className="font-medium">{row.employee_code}</p>
                        <p className="text-ink-soft text-xs">{row.employee_name}</p>
                      </Td>
                      <Td>{row.leave_type_code}</Td>
                      {[
                        row.opening_days,
                        row.accrued_days,
                        row.adjusted_days,
                        row.used_days,
                        row.carried_out_days,
                        row.lapsed_days,
                        row.encashed_days,
                        row.closing_days,
                      ].map((value, column) => (
                        <Td key={column} className="text-right tabular-nums">
                          {formatDays(value)}
                        </Td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </TableShell>
            )}
          </CardBody>
        </Card>
      )}
    </div>
  );
}
