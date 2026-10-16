/**
 * Payroll administration: runs, statutory rules, components and adjustments.
 *
 * Tabs appear only where the caller holds the permission - visibility only, as
 * every endpoint behind them is authorized on the server. The lifecycle
 * deliberately needs several permissions, so an account that can calculate a
 * run often cannot approve it, and the tabs reflect that honestly.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Coins, FileText, Plus, ScrollText, ShieldAlert, Wallet } from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/Button';
import { CheckboxField, SelectField, TextAreaField, TextField } from '@/components/ui/Field';
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
import { ComponentsPanel } from '@/features/payroll/ComponentsPanel';
import { RunsPanel } from '@/features/payroll/RunsPanel';
import { StatutoryPanel } from '@/features/payroll/StatutoryPanel';
import { cancelAdjustment, createAdjustment, fetchAdjustments } from '@/features/payroll/api';
import { useAuth } from '@/lib/auth';
import { useToast } from '@/lib/toast';
import { cn } from '@/lib/utils';

type Tab = 'runs' | 'statutory' | 'components' | 'adjustments';

function AdjustmentsPanel() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [composing, setComposing] = useState(false);
  const [employeeId, setEmployeeId] = useState('');
  const [adjustmentType, setAdjustmentType] = useState('arrear');
  const [amount, setAmount] = useState('');
  const [label, setLabel] = useState('');
  const [reason, setReason] = useState('');
  const [isTaxable, setIsTaxable] = useState(true);

  const adjustments = useQuery({
    queryKey: ['payroll', 'adjustments'],
    queryFn: fetchAdjustments,
  });

  const create = useMutation({
    mutationFn: () =>
      createAdjustment({
        employee_id: Number(employeeId),
        adjustment_type: adjustmentType,
        amount: amount.trim(),
        label: label.trim(),
        reason: reason.trim(),
        is_taxable: isTaxable,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['payroll'] });
      setComposing(false);
      setAmount('');
      setLabel('');
      setReason('');
      toast.success('Queued. It will be applied in the next calculation.');
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  const cancel = useMutation({
    mutationFn: (id: number) => cancelAdjustment(id, 'Cancelled before payment.'),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['payroll'] });
      toast.success('Cancelled.');
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  const rows = adjustments.data ?? [];

  return (
    <div className="space-y-5">
      <Alert variant="info" title="The only way to correct a finalized run">
        Rewriting a finalized month would falsify the register the bank transfer and the
        statutory filings were made from, so a correction is posted forward. The period the
        money belongs to is recorded separately from the run that pays it, which is what makes
        an arrear explainable as "March's shortfall, paid in May".
      </Alert>

      <Card>
        <CardHeader
          title="Pending adjustments"
          description="Applied in the next calculation for that employee."
          action={
            <Button onClick={() => setComposing(true)}>
              <Plus className="size-4" aria-hidden="true" />
              New adjustment
            </Button>
          }
        />
        <CardBody>
          {adjustments.isPending ? (
            <SkeletonRows rows={3} />
          ) : adjustments.isError ? (
            <ErrorPanel error={adjustments.error} onRetry={() => void adjustments.refetch()} />
          ) : rows.length === 0 ? (
            <EmptyState
              title="Nothing pending"
              description="Arrears, bonuses and recoveries queued here are picked up by the next run."
            />
          ) : (
            <TableShell caption="Pending adjustments">
              <thead>
                <tr>
                  <Th>Employee</Th>
                  <Th>Type</Th>
                  <Th>Label</Th>
                  <Th className="text-right">Amount</Th>
                  <Th>Reason</Th>
                  <Th>Actions</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((item) => (
                  <tr key={item.id}>
                    <Td className="tabular-nums">#{item.employee_id}</Td>
                    <Td>
                      <Badge
                        tone={
                          item.adjustment_type === 'arrear' || item.adjustment_type === 'bonus'
                            ? 'brand'
                            : 'warning'
                        }
                      >
                        {item.adjustment_type}
                      </Badge>
                    </Td>
                    <Td>{item.label}</Td>
                    <Td className="text-right tabular-nums">{item.amount}</Td>
                    <Td className="text-ink-soft max-w-sm text-xs">{item.reason}</Td>
                    <Td>
                      <Button variant="ghost" onClick={() => cancel.mutate(item.id)}>
                        Cancel
                      </Button>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </TableShell>
          )}
        </CardBody>
      </Card>

      <Modal
        open={composing}
        onClose={() => setComposing(false)}
        title="New adjustment"
        description="The amount is always positive; the type decides whether it is paid or recovered."
        footer={
          <>
            <Button variant="ghost" onClick={() => setComposing(false)}>
              Cancel
            </Button>
            <Button
              onClick={() => create.mutate()}
              disabled={
                !employeeId.trim() ||
                !amount.trim() ||
                !label.trim() ||
                reason.trim().length < 3 ||
                create.isPending
              }
            >
              Queue it
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <TextField
              label="Employee id"
              value={employeeId}
              onChange={(event) => setEmployeeId(event.target.value)}
              required
            />
            <SelectField
              label="Type"
              value={adjustmentType}
              onChange={(event) => setAdjustmentType(event.target.value)}
              options={[
                { value: 'arrear', label: 'Arrear (paid)' },
                { value: 'bonus', label: 'Bonus (paid)' },
                { value: 'recovery', label: 'Recovery (deducted)' },
                { value: 'reversal', label: 'Reversal (deducted)' },
              ]}
            />
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <TextField
              label="Amount"
              value={amount}
              onChange={(event) => setAmount(event.target.value)}
              hint="Positive. The type decides the direction."
              required
            />
            <TextField
              label="Label on the payslip"
              value={label}
              onChange={(event) => setLabel(event.target.value)}
              required
            />
          </div>
          <TextAreaField
            label="Reason"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            rows={3}
            required
            hint="Shown as the line's basis, so the employee can see why."
          />
          <CheckboxField
            label="Taxable"
            checked={isTaxable}
            onChange={(event) => setIsTaxable(event.target.checked)}
          />
        </div>
      </Modal>
    </div>
  );
}

export function PayrollPage() {
  const { can, canAny } = useAuth();
  const [tab, setTab] = useState<Tab>('runs');

  const tabs: { id: Tab; label: string; icon: typeof Wallet; visible: boolean }[] = [
    { id: 'runs', label: 'Runs', icon: Wallet, visible: can('payroll.read') },
    {
      id: 'statutory',
      label: 'Statutory rules',
      icon: ShieldAlert,
      visible: canAny('statutory.read', 'statutory.manage'),
    },
    {
      id: 'components',
      label: 'Components & salaries',
      icon: Coins,
      visible: canAny('salary.manage', 'salary.read_all'),
    },
    {
      id: 'adjustments',
      label: 'Adjustments',
      icon: ScrollText,
      visible: canAny('payroll.adjust', 'payroll.read'),
    },
  ];
  const visible = tabs.filter((item) => item.visible);
  const active = visible.some((item) => item.id === tab) ? tab : visible[0]?.id;

  return (
    <div>
      <PageHeader
        title="Payroll"
        description="Salary structures, statutory configuration, and the monthly run from draft to released payslips. No bank transfer or statutory filing is performed by this system."
      />

      <div
        className="border-line mb-5 flex gap-1 overflow-x-auto border-b"
        role="tablist"
        aria-label="Payroll sections"
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

      {active === 'runs' && <RunsPanel />}
      {active === 'statutory' && <StatutoryPanel />}
      {active === 'components' && <ComponentsPanel />}
      {active === 'adjustments' && <AdjustmentsPanel />}
      {visible.length === 0 && (
        <EmptyState
          icon={FileText}
          title="Nothing here for this account"
          description="Payroll needs one of the payroll, salary or statutory permissions."
        />
      )}
    </div>
  );
}
