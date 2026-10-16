/** A list of leave applications, with the actions available on each. */

import { CircleAlert, History } from 'lucide-react';

import { Button } from '@/components/ui/Button';
import { EmptyState } from '@/components/ui/Feedback';
import { Badge, TableShell, Td, Th, type BadgeTone } from '@/components/ui/Surface';
import {
  formatDays,
  isPositive,
  type ApplicationStatus,
  type LeaveApplicationSummary,
} from '@/features/leave/api';
import { formatIsoLong } from '@/lib/dates';

const STATUS_TONES: Record<ApplicationStatus, BadgeTone> = {
  pending_manager: 'warning',
  pending_hr: 'warning',
  approved: 'success',
  rejected: 'danger',
  withdrawn: 'neutral',
  cancelled: 'neutral',
};

const STATUS_LABELS: Record<ApplicationStatus, string> = {
  pending_manager: 'Awaiting manager',
  pending_hr: 'Awaiting HR',
  approved: 'Approved',
  rejected: 'Rejected',
  withdrawn: 'Withdrawn',
  cancelled: 'Cancelled',
};

export interface ApplicationAction {
  label: string;
  onAct: (application: LeaveApplicationSummary) => void;
  /** Which statuses the action applies to. */
  statuses: ApplicationStatus[];
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
}

export function ApplicationTable({
  applications,
  actions = [],
  showEmployee = false,
  emptyTitle = 'No leave applications',
  emptyDescription,
  onOpenHistory,
}: {
  applications: LeaveApplicationSummary[];
  actions?: ApplicationAction[];
  showEmployee?: boolean;
  emptyTitle?: string;
  emptyDescription?: string | undefined;
  onOpenHistory?: (applicationId: number) => void;
}) {
  if (applications.length === 0) {
    return (
      <EmptyState
        title={emptyTitle}
        {...(emptyDescription ? { description: emptyDescription } : {})}
      />
    );
  }

  return (
    <TableShell caption="Leave applications">
      <thead>
        <tr>
          {showEmployee && <Th>Employee</Th>}
          <Th>Type</Th>
          <Th>Dates</Th>
          <Th>Days</Th>
          <Th>Status</Th>
          <Th>Reason</Th>
          <Th className="text-right">Actions</Th>
        </tr>
      </thead>
      <tbody>
        {applications.map((application) => {
          const available = actions.filter((action) =>
            action.statuses.includes(application.status),
          );
          return (
            <tr key={application.id}>
              {showEmployee && (
                <Td>
                  <p className="font-medium">{application.employee_code ?? '—'}</p>
                  <p className="text-ink-soft text-xs">{application.employee_name}</p>
                </Td>
              )}
              <Td>
                <p>{application.leave_type_name ?? application.leave_type_code}</p>
                {application.applied_on_behalf && (
                  <p className="text-ink-muted text-xs">Recorded by HR</p>
                )}
              </Td>
              <Td className="whitespace-nowrap">
                {application.start_date === application.end_date ? (
                  formatIsoLong(application.start_date)
                ) : (
                  <>
                    {formatIsoLong(application.start_date)}
                    <span className="text-ink-muted"> to </span>
                    {formatIsoLong(application.end_date)}
                  </>
                )}
                {application.is_backdated && (
                  <p className="text-ink-muted text-xs">Backdated</p>
                )}
              </Td>
              <Td className="tabular-nums">
                {formatDays(application.total_days)}
                {isPositive(application.unpaid_days) && (
                  <p className="text-warning-700 text-xs">
                    {formatDays(application.unpaid_days)} unpaid
                  </p>
                )}
              </Td>
              <Td>
                <Badge tone={STATUS_TONES[application.status]}>
                  {STATUS_LABELS[application.status]}
                </Badge>
                {application.escalated_from_missing_manager && (
                  <p
                    className="text-ink-muted mt-1 flex items-start gap-1 text-xs"
                    title="There was no active reporting manager, so the manager stage was skipped."
                  >
                    <CircleAlert className="mt-0.5 size-3 shrink-0" aria-hidden="true" />
                    Manager stage skipped
                  </p>
                )}
              </Td>
              <Td className="text-ink-soft max-w-xs text-sm">{application.reason ?? '—'}</Td>
              <Td className="text-right">
                <div className="flex flex-wrap justify-end gap-2">
                  {onOpenHistory && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => onOpenHistory(application.id)}
                    >
                      <History className="size-4" aria-hidden="true" />
                      History
                    </Button>
                  )}
                  {available.map((action) => (
                    <Button
                      key={action.label}
                      variant={action.variant ?? 'secondary'}
                      size="sm"
                      onClick={() => action.onAct(application)}
                    >
                      {action.label}
                    </Button>
                  ))}
                </div>
              </Td>
            </tr>
          );
        })}
      </tbody>
    </TableShell>
  );
}
