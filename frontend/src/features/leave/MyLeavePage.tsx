/** Employee self-service: balances, applying, history and the ledger. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Plus } from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/Button';
import { SelectField, TextAreaField } from '@/components/ui/Field';
import { ErrorPanel, SkeletonRows } from '@/components/ui/Feedback';
import { Modal } from '@/components/ui/Modal';
import {
  Card,
  CardBody,
  CardHeader,
  PageHeader,
  TableShell,
  Td,
  Th,
} from '@/components/ui/Surface';
import { ApplicationTable } from '@/features/leave/ApplicationTable';
import { ApplyLeaveModal } from '@/features/leave/ApplyLeaveModal';
import { BalanceCards } from '@/features/leave/BalanceCards';
import { DecisionHistory } from '@/features/leave/DecisionHistory';
import {
  cancelApplication,
  fetchMyApplications,
  fetchMyBalances,
  fetchMyLedger,
  formatDays,
  leaveKeys,
  withdrawApplication,
  type LeaveApplicationSummary,
} from '@/features/leave/api';
import { formatIsoLong } from '@/lib/dates';
import { useToast } from '@/lib/toast';

const TXN_LABELS: Record<string, string> = {
  opening: 'Opening balance',
  accrual: 'Accrued',
  deduction: 'Leave taken',
  reversal: 'Returned on cancellation',
  adjustment: 'Manual adjustment',
  carry_forward_in: 'Carried forward in',
  carry_forward_out: 'Carried forward out',
  expiry: 'Expired',
  encashment: 'Encashed',
  compoff_credit: 'Comp-off credit',
};

export function MyLeavePage() {
  const toast = useToast();
  const queryClient = useQueryClient();

  const [applying, setApplying] = useState(false);
  const [initialTypeId, setInitialTypeId] = useState<number | undefined>(undefined);
  const [historyId, setHistoryId] = useState<number | null>(null);
  const [prompt, setPrompt] = useState<{
    application: LeaveApplicationSummary;
    kind: 'withdraw' | 'cancel';
  } | null>(null);
  const [reason, setReason] = useState('');
  const [ledgerType, setLedgerType] = useState<string>('');

  const balances = useQuery({
    queryKey: leaveKeys.myBalances(),
    queryFn: () => fetchMyBalances(),
  });
  const applications = useQuery({
    queryKey: leaveKeys.myApplications(),
    queryFn: () => fetchMyApplications(),
  });
  const ledger = useQuery({
    queryKey: leaveKeys.myLedger(ledgerType ? Number(ledgerType) : undefined),
    queryFn: () => fetchMyLedger(ledgerType ? Number(ledgerType) : undefined),
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: leaveKeys.all });
  };

  const act = useMutation({
    mutationFn: () => {
      if (!prompt) throw new Error('Nothing to act on');
      return prompt.kind === 'withdraw'
        ? withdrawApplication(prompt.application.id, reason || null)
        : cancelApplication(prompt.application.id, reason);
    },
    onSuccess: () => {
      const kind = prompt?.kind;
      setPrompt(null);
      setReason('');
      invalidate();
      toast.success(
        kind === 'withdraw'
          ? 'Application withdrawn. Nothing was deducted, so the dates are free again.'
          : 'Leave cancelled and the days returned to your balance.',
      );
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  if (balances.isError) {
    return <ErrorPanel error={balances.error} onRetry={() => void balances.refetch()} />;
  }

  return (
    <div>
      <PageHeader
        title="My leave"
        description="Your balances, applications and the arithmetic behind every figure."
        action={
          <Button
            onClick={() => {
              setInitialTypeId(undefined);
              setApplying(true);
            }}
          >
            <Plus className="size-4" aria-hidden="true" />
            Apply for leave
          </Button>
        }
      />

      <div className="space-y-6">
        {balances.isPending ? (
          <SkeletonRows rows={3} />
        ) : (
          <BalanceCards
            balances={balances.data}
            onApply={(leaveTypeId) => {
              setInitialTypeId(leaveTypeId);
              setApplying(true);
            }}
          />
        )}

        <Card>
          <CardHeader
            title="My applications"
            description="Withdraw a request nobody has decided yet; cancel one already approved."
          />
          <CardBody>
            {applications.isPending ? (
              <SkeletonRows rows={4} />
            ) : applications.isError ? (
              <ErrorPanel
                error={applications.error}
                onRetry={() => void applications.refetch()}
              />
            ) : (
              <ApplicationTable
                applications={applications.data}
                onOpenHistory={setHistoryId}
                emptyTitle="You have not applied for any leave"
                emptyDescription="Your balances are above; apply when you need to."
                actions={[
                  {
                    label: 'Withdraw',
                    statuses: ['pending_manager', 'pending_hr'],
                    variant: 'ghost',
                    onAct: (application) => {
                      setReason('');
                      setPrompt({ application, kind: 'withdraw' });
                    },
                  },
                  {
                    label: 'Cancel',
                    statuses: ['approved'],
                    variant: 'ghost',
                    onAct: (application) => {
                      setReason('');
                      setPrompt({ application, kind: 'cancel' });
                    },
                  },
                ]}
              />
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Balance history"
            description="Every movement, in order. Your balance is the sum of this list and nothing else, so it can always be explained."
            action={
              <div className="w-56">
                <SelectField
                  label="Leave type"
                  value={ledgerType}
                  onChange={(event) => setLedgerType(event.target.value)}
                  options={[
                    { value: '', label: 'All types' },
                    ...(balances.data ?? []).map((item) => ({
                      value: String(item.leave_type_id),
                      label: item.leave_type_name,
                    })),
                  ]}
                />
              </div>
            }
          />
          <CardBody>
            {ledger.isPending ? (
              <SkeletonRows rows={4} />
            ) : ledger.isError ? (
              <ErrorPanel error={ledger.error} onRetry={() => void ledger.refetch()} />
            ) : ledger.data.length === 0 ? (
              <p className="text-ink-soft text-sm">
                Nothing has moved yet. Entries appear when leave accrues, is taken, or is
                adjusted.
              </p>
            ) : (
              <TableShell caption="Leave balance movements">
                <thead>
                  <tr>
                    <Th>Date</Th>
                    <Th>Movement</Th>
                    <Th className="text-right">Days</Th>
                    <Th>Why</Th>
                  </tr>
                </thead>
                <tbody>
                  {ledger.data.map((entry) => {
                    const negative = entry.days.startsWith('-');
                    return (
                      <tr key={entry.id}>
                        <Td className="whitespace-nowrap">
                          {formatIsoLong(entry.effective_date)}
                        </Td>
                        <Td>{TXN_LABELS[entry.txn_type] ?? entry.txn_type}</Td>
                        <Td
                          className={`text-right tabular-nums ${
                            negative ? 'text-danger-700' : 'text-success-700'
                          }`}
                        >
                          {negative ? '' : '+'}
                          {formatDays(entry.days)}
                        </Td>
                        <Td className="text-ink-soft max-w-sm text-sm">
                          {entry.reason ?? '—'}
                        </Td>
                      </tr>
                    );
                  })}
                </tbody>
              </TableShell>
            )}
          </CardBody>
        </Card>
      </div>

      <ApplyLeaveModal
        open={applying}
        balances={balances.data ?? []}
        initialTypeId={initialTypeId}
        onClose={() => setApplying(false)}
        onApplied={() => {
          setApplying(false);
          invalidate();
        }}
      />

      <DecisionHistory applicationId={historyId} onClose={() => setHistoryId(null)} />

      <Modal
        open={prompt !== null}
        onClose={() => setPrompt(null)}
        title={prompt?.kind === 'withdraw' ? 'Withdraw this application' : 'Cancel this leave'}
        description={
          prompt?.kind === 'withdraw'
            ? 'Nothing has been deducted, so nothing is reversed and the dates become free again.'
            : 'The days are returned to your balance by a reversal entry, and attendance for those days is recalculated.'
        }
        footer={
          <>
            <Button variant="secondary" onClick={() => setPrompt(null)}>
              Keep it
            </Button>
            <Button
              variant="danger"
              onClick={() => act.mutate()}
              disabled={act.isPending || (prompt?.kind === 'cancel' && reason.trim() === '')}
            >
              {act.isPending
                ? 'Working…'
                : prompt?.kind === 'withdraw'
                  ? 'Withdraw'
                  : 'Cancel leave'}
            </Button>
          </>
        }
      >
        <TextAreaField
          label="Reason"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          rows={3}
          required={prompt?.kind === 'cancel'}
          hint={
            prompt?.kind === 'cancel'
              ? 'Required. Cancelling approved leave changes a balance and attendance, so the record needs to say why.'
              : 'Optional.'
          }
        />
      </Modal>
    </div>
  );
}
