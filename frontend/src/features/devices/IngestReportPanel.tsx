/**
 * The outcome of one ingestion, broken down by what happened to each event.
 *
 * Deliberately not a single total. "Fetched 200, accepted 3, unmapped 197" is a
 * broken configuration; "fetched 200, accepted 200" is a healthy sync. One
 * number cannot tell them apart, so none is offered.
 */

import { X } from 'lucide-react';

import { Button } from '@/components/ui/Button';
import { Badge, Card, CardBody, CardHeader, type BadgeTone } from '@/components/ui/Surface';
import type { IngestReport } from '@/features/devices/api';

const OUTCOMES: {
  key: keyof Pick<IngestReport, 'accepted' | 'duplicate' | 'unmapped' | 'failed' | 'ignored'>;
  label: string;
  explanation: string;
  tone: BadgeTone;
}[] = [
  {
    key: 'accepted',
    label: 'Applied',
    explanation: 'Became attendance punches.',
    tone: 'success',
  },
  {
    key: 'duplicate',
    label: 'Already known',
    explanation: 'Seen before and skipped. Re-sending is harmless.',
    tone: 'neutral',
  },
  {
    key: 'unmapped',
    label: 'No employee',
    explanation: 'The device identifier is not mapped to anyone.',
    tone: 'warning',
  },
  {
    key: 'failed',
    label: 'Unusable',
    explanation: 'Stored for inspection, not applied.',
    tone: 'danger',
  },
  {
    key: 'ignored',
    label: 'Not a punch',
    explanation: 'Matched an ignore rule, such as a heartbeat.',
    tone: 'neutral',
  },
];

export function IngestReportPanel({
  report,
  onDismiss,
}: {
  report: IngestReport;
  onDismiss: () => void;
}) {
  const needsAttention = report.unmapped + report.failed;

  return (
    <Card>
      <CardHeader
        title={
          <span className="flex flex-wrap items-center gap-2">
            {report.device_code}
            <Badge
              tone={
                report.status === 'succeeded'
                  ? 'success'
                  : report.status === 'partial'
                    ? 'warning'
                    : 'danger'
              }
            >
              {report.status}
            </Badge>
          </span>
        }
        description={
          report.status === 'failed'
            ? 'The run is recorded in this device’s synchronisation log so the failure is not lost.'
            : `${report.fetched} event(s) received, ${report.days_recomputed} attendance day(s) recalculated.`
        }
        action={
          <Button variant="ghost" onClick={onDismiss} aria-label="Dismiss this report">
            <X className="size-4" aria-hidden="true" />
          </Button>
        }
      />
      <CardBody className="space-y-4">
        <dl className="grid gap-3 sm:grid-cols-3 lg:grid-cols-5">
          {OUTCOMES.map((outcome) => (
            <div key={outcome.key} className="border-line rounded-lg border p-3">
              <dt className="text-ink-soft text-xs font-medium tracking-wide uppercase">
                {outcome.label}
              </dt>
              <dd className="text-ink mt-1 text-lg font-semibold">{report[outcome.key]}</dd>
              <dd className="text-ink-muted mt-0.5 text-xs">{outcome.explanation}</dd>
            </div>
          ))}
        </dl>

        {needsAttention > 0 && (
          <p className="text-ink-soft text-sm">
            {needsAttention} event(s) were stored but not applied. Nothing the device sent has
            been discarded — fix the mapping or the configuration, then reprocess them from the
            Reconciliation tab.
          </p>
        )}

        {report.problems.length > 0 && (
          <div>
            <h3 className="text-ink text-sm font-semibold">
              Problems ({report.problems.length})
            </h3>
            <ul className="mt-2 space-y-2">
              {report.problems.map((problem, index) => (
                <li
                  key={index}
                  className="border-warning-600/30 bg-warning-50 text-warning-900 rounded-md border p-3 text-sm"
                >
                  <p>{problem.reason}</p>
                  {(problem.employee_ref || problem.raw_timestamp) && (
                    <p className="mt-1 font-mono text-xs">
                      {problem.employee_ref && <>identifier {problem.employee_ref}</>}
                      {problem.employee_ref && problem.raw_timestamp && ' · '}
                      {problem.raw_timestamp && <>timestamp {problem.raw_timestamp}</>}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardBody>
    </Card>
  );
}
