/**
 * An employee's own payslips and salary structure.
 *
 * Only published payslips appear: one from a run still under review may still
 * change, and nobody should watch their own pay move. Every line shows what it
 * was computed from, because a figure an employee cannot check is a figure they
 * cannot dispute.
 */

import { useQuery } from '@tanstack/react-query';
import { ChevronDown, FileText, Info, Wallet } from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/Button';
import { Alert, EmptyState, ErrorPanel, SkeletonRows } from '@/components/ui/Feedback';
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
import {
  fetchMyPayslips,
  fetchMySalary,
  fetchPayslip,
  groupLines,
  type PayslipLine,
} from '@/features/payroll/api';

function LineTable({
  title,
  lines,
  total,
}: {
  title: string;
  lines: PayslipLine[];
  total: string;
}) {
  if (lines.length === 0) return null;
  return (
    <div>
      <h3 className="text-ink mb-2 text-sm font-semibold">{title}</h3>
      <TableShell caption={title}>
        <thead>
          <tr>
            <Th>Line</Th>
            <Th>Computed from</Th>
            <Th className="text-right">Amount</Th>
          </tr>
        </thead>
        <tbody>
          {lines.map((line) => (
            <tr key={line.line_code}>
              <Td className="font-medium">{line.label}</Td>
              <Td className="text-ink-soft max-w-sm text-xs">{line.basis_description}</Td>
              <Td className="text-right tabular-nums">{line.amount}</Td>
            </tr>
          ))}
          <tr className="bg-sunken font-semibold">
            <Td colSpan={2}>Total</Td>
            <Td className="text-right tabular-nums">{total}</Td>
          </tr>
        </tbody>
      </TableShell>
    </div>
  );
}

function PayslipDetail({ payslipId }: { payslipId: number }) {
  const payslip = useQuery({
    queryKey: ['payroll', 'payslip', payslipId],
    queryFn: () => fetchPayslip(payslipId),
  });

  if (payslip.isPending) return <SkeletonRows rows={5} />;
  if (payslip.isError)
    return <ErrorPanel error={payslip.error} onRetry={() => void payslip.refetch()} />;
  if (!payslip.data) return null;

  const grouped = groupLines(payslip.data.lines);
  const data = payslip.data;

  return (
    <div className="space-y-5">
      <dl className="grid gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {[
          ['Payable days', data.payable_days],
          ['Loss of pay', data.lop_days],
          ['Gross', data.gross_earnings],
          ['Deductions', data.total_deductions],
          ['Net pay', data.net_pay],
        ].map(([label, value]) => (
          <div key={label} className="border-line rounded-lg border p-3">
            <dt className="text-ink-soft text-xs font-medium tracking-wide uppercase">
              {label}
            </dt>
            <dd className="text-ink mt-1 text-lg font-semibold tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>

      {data.is_prorated && (
        <Alert variant="info" title="This month was prorated" icon={Info}>
          {data.proration_reason}
        </Alert>
      )}

      <LineTable title="Earnings" lines={grouped.earnings} total={data.gross_earnings} />
      <LineTable title="Deductions" lines={grouped.deductions} total={data.total_deductions} />

      {grouped.employer.length > 0 && (
        <>
          <LineTable
            title="Employer contributions"
            lines={grouped.employer}
            total={data.employer_contributions}
          />
          <p className="text-ink-muted text-xs">
            Employer contributions are a company cost shown for transparency. They are not
            deducted from your pay and are not part of the net figure above.
          </p>
        </>
      )}
    </div>
  );
}

export function MyPayslipsPage() {
  const [openId, setOpenId] = useState<number | null>(null);

  const payslips = useQuery({
    queryKey: ['payroll', 'my-payslips'],
    queryFn: fetchMyPayslips,
  });

  const salary = useQuery({
    queryKey: ['payroll', 'my-salary'],
    queryFn: fetchMySalary,
    retry: false,
  });

  return (
    <div>
      <PageHeader
        title="Payslips"
        description="Your published payslips, and the salary structure in force. Each line shows what it was computed from."
      />

      {salary.data && (
        <Card className="mb-5">
          <CardHeader
            title="Your current salary structure"
            description={`In force from ${salary.data.effective_from}${
              salary.data.effective_to ? ` to ${salary.data.effective_to}` : ''
            }`}
            action={
              <div className="text-right">
                <p className="text-ink-soft text-xs tracking-wide uppercase">Monthly gross</p>
                <p className="text-ink text-lg font-semibold tabular-nums">
                  {salary.data.gross_monthly}
                </p>
              </div>
            }
          />
          <CardBody>
            <TableShell caption="Salary components">
              <thead>
                <tr>
                  <Th>Component</Th>
                  <Th>Basis</Th>
                  <Th className="text-right">Monthly</Th>
                </tr>
              </thead>
              <tbody>
                {salary.data.components.map((item) => (
                  <tr key={item.component_id}>
                    <Td>#{item.component_id}</Td>
                    <Td className="text-ink-soft text-xs">{item.basis_description}</Td>
                    <Td className="text-right tabular-nums">{item.monthly_amount}</Td>
                  </tr>
                ))}
              </tbody>
            </TableShell>
          </CardBody>
        </Card>
      )}

      <Card>
        <CardHeader
          title="Payslips"
          description="A payslip appears here once the payroll run has been finalized and released."
        />
        <CardBody className="space-y-4">
          {payslips.isPending ? (
            <SkeletonRows rows={4} />
          ) : payslips.isError ? (
            <ErrorPanel error={payslips.error} onRetry={() => void payslips.refetch()} />
          ) : (payslips.data ?? []).length === 0 ? (
            <EmptyState
              icon={Wallet}
              title="No payslips yet"
              description="Payslips appear here once a payroll run has been finalized and released to employees."
            />
          ) : (
            (payslips.data ?? []).map((item) => (
              <div key={item.id} className="border-line rounded-lg border">
                <div className="flex flex-wrap items-center justify-between gap-3 p-4">
                  <div className="min-w-0">
                    <p className="text-ink font-medium">
                      <FileText className="mr-1 inline size-4" aria-hidden="true" />
                      Run #{item.run_id}
                    </p>
                    <p className="text-ink-soft text-xs">
                      {item.payable_days} payable day(s)
                      {Number(item.lop_days) > 0 ? `, ${item.lop_days} loss of pay` : ''}
                    </p>
                  </div>
                  <div className="flex items-center gap-4">
                    <div className="text-right">
                      <p className="text-ink-soft text-xs tracking-wide uppercase">Net pay</p>
                      <p className="text-ink text-lg font-semibold tabular-nums">
                        {item.net_pay}
                      </p>
                    </div>
                    <Button
                      variant="secondary"
                      onClick={() => setOpenId(openId === item.id ? null : item.id)}
                      aria-expanded={openId === item.id}
                    >
                      <ChevronDown className="size-4" aria-hidden="true" />
                      {openId === item.id ? 'Hide' : 'View'}
                    </Button>
                  </div>
                </div>
                {openId === item.id && (
                  <div className="border-line border-t p-4">
                    <PayslipDetail payslipId={item.id} />
                  </div>
                )}
              </div>
            ))
          )}
        </CardBody>
      </Card>

      {salary.isError && (
        <p className="text-ink-muted mt-4 text-sm">
          <Badge tone="neutral">No salary structure</Badge> Nothing has been recorded for you
          yet. HR records a salary revision before payroll can include you.
        </p>
      )}
    </div>
  );
}
