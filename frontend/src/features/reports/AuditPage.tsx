/**
 * The audit log viewer, job history and the retention position.
 *
 * Reading this page writes its own audit entry, and the page says so: somebody
 * checking what a colleague did is exactly the thing an organisation may later
 * need to account for, and being told that up front is fairer than discovering
 * it afterwards.
 */

import { useQuery } from '@tanstack/react-query';
import {
  CircleAlert,
  Clock,
  Eye,
  History,
  RefreshCw,
  Search,
  ShieldCheck,
  Trash2,
} from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/Button';
import { DatePicker } from '@/components/ui/DatePicker';
import { SelectField, TextField } from '@/components/ui/Field';
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
  fetchAuditActions,
  fetchAuditLogs,
  fetchJobRuns,
  fetchRetention,
  type AuditEntry,
} from '@/features/reports/api';
import { cn } from '@/lib/utils';

type Tab = 'log' | 'jobs' | 'retention';

function EntryDetail({ entry }: { entry: AuditEntry }) {
  const blocks: [string, unknown][] = [
    ['Before', entry.before_value],
    ['After', entry.after_value],
    ['Context', entry.context],
  ];
  const present = blocks.filter(([, value]) => value !== null && value !== undefined);
  if (present.length === 0) {
    return <p className="text-ink-muted text-xs">No values were recorded for this entry.</p>;
  }
  return (
    <div className="grid gap-3 sm:grid-cols-3">
      {present.map(([label, value]) => (
        <div key={label}>
          <p className="text-ink-soft text-xs font-medium tracking-wide uppercase">{label}</p>
          <pre className="bg-sunken text-ink-soft mt-1 max-h-48 overflow-auto rounded p-2 text-xs">
            {JSON.stringify(value, null, 2)}
          </pre>
        </div>
      ))}
    </div>
  );
}

export function AuditPage() {
  const [tab, setTab] = useState<Tab>('log');
  const [action, setAction] = useState('');
  const [requestId, setRequestId] = useState('');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [succeeded, setSucceeded] = useState('');
  const [page, setPage] = useState(1);
  const [openId, setOpenId] = useState<number | null>(null);

  const filters = {
    ...(action ? { action } : {}),
    ...(requestId ? { request_id: requestId } : {}),
    ...(start ? { start } : {}),
    ...(end ? { end } : {}),
    ...(succeeded ? { succeeded: succeeded === 'yes' } : {}),
    page,
    page_size: 25,
  };

  const logs = useQuery({
    queryKey: ['audit', 'logs', filters],
    queryFn: () => fetchAuditLogs(filters),
    enabled: tab === 'log',
  });

  const actions = useQuery({
    queryKey: ['audit', 'actions'],
    queryFn: fetchAuditActions,
    enabled: tab === 'log',
  });

  const jobs = useQuery({
    queryKey: ['audit', 'jobs'],
    queryFn: fetchJobRuns,
    enabled: tab === 'jobs',
  });

  const retention = useQuery({
    queryKey: ['audit', 'retention'],
    queryFn: fetchRetention,
    enabled: tab === 'retention',
  });

  const tabs: { id: Tab; label: string; icon: typeof Eye }[] = [
    { id: 'log', label: 'Audit log', icon: Eye },
    { id: 'jobs', label: 'Scheduled jobs', icon: History },
    { id: 'retention', label: 'Retention', icon: Trash2 },
  ];

  return (
    <div>
      <PageHeader
        title="Audit and retention"
        description="Who changed what, what the scheduled jobs did, and how much data is being held beyond its stated period."
      />

      <div
        className="border-line mb-5 flex gap-1 overflow-x-auto border-b"
        role="tablist"
        aria-label="Audit sections"
      >
        {tabs.map((item) => {
          const Icon = item.icon;
          const isActive = tab === item.id;
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

      {tab === 'log' && (
        <div className="space-y-5">
          <Alert
            variant="info"
            title="Searching this log is itself recorded"
            icon={ShieldCheck}
          >
            The trail includes who changed permissions and whose bank details were opened, so
            looking through it is a privileged act with its own entry. The filters are recorded,
            not the results.
          </Alert>

          <Card>
            <CardHeader
              title="Search"
              description="An action matches as a prefix, so 'leave.' returns everything the leave module recorded."
            />
            <CardBody>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
                <SelectField
                  label="Action"
                  value={action}
                  onChange={(event) => {
                    setAction(event.target.value);
                    setPage(1);
                  }}
                  options={[
                    { value: '', label: 'Everything' },
                    ...Array.from(
                      new Set(
                        (actions.data ?? []).map((item) => `${item.action.split('.')[0]}.`),
                      ),
                    ).map((prefix) => ({ value: prefix, label: prefix })),
                    ...(actions.data ?? []).map((item) => ({
                      value: item.action,
                      label: `${item.action} (${item.count})`,
                    })),
                  ]}
                />
                <TextField
                  label="Request id"
                  value={requestId}
                  onChange={(event) => {
                    setRequestId(event.target.value);
                    setPage(1);
                  }}
                  hint="The reference a user may have quoted from an error."
                />
                <DatePicker
                  label="From"
                  value={start}
                  onChange={(next) => {
                    setStart(next);
                    setPage(1);
                  }}
                />
                <DatePicker
                  label="To"
                  value={end}
                  min={start}
                  onChange={(next) => {
                    setEnd(next);
                    setPage(1);
                  }}
                />
                <SelectField
                  label="Outcome"
                  value={succeeded}
                  onChange={(event) => {
                    setSucceeded(event.target.value);
                    setPage(1);
                  }}
                  options={[
                    { value: '', label: 'Any' },
                    { value: 'yes', label: 'Succeeded' },
                    { value: 'no', label: 'Failed' },
                  ]}
                />
              </div>
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title={logs.data ? `${logs.data.total} entr(ies)` : 'Entries'}
              description="Newest first."
              action={
                logs.data && logs.data.pages > 1 ? (
                  <div className="flex items-center gap-2">
                    <Button
                      variant="secondary"
                      onClick={() => setPage((value) => Math.max(1, value - 1))}
                      disabled={page <= 1}
                    >
                      Previous
                    </Button>
                    <span className="text-ink-soft text-sm">
                      {logs.data.page} of {logs.data.pages}
                    </span>
                    <Button
                      variant="secondary"
                      onClick={() => setPage((value) => value + 1)}
                      disabled={logs.data.page >= logs.data.pages}
                    >
                      Next
                    </Button>
                  </div>
                ) : undefined
              }
            />
            <CardBody className="px-0">
              {logs.isPending ? (
                <div className="px-5">
                  <SkeletonRows rows={6} />
                </div>
              ) : logs.isError ? (
                <div className="px-5">
                  <ErrorPanel error={logs.error} onRetry={() => void logs.refetch()} />
                </div>
              ) : (logs.data?.items ?? []).length === 0 ? (
                <div className="px-5">
                  <EmptyState
                    icon={Search}
                    title="Nothing matched"
                    description="No entries for those filters."
                  />
                </div>
              ) : (
                <TableShell caption="Audit entries">
                  <thead>
                    <tr>
                      <Th>When</Th>
                      <Th>Action</Th>
                      <Th>Actor</Th>
                      <Th>Entity</Th>
                      <Th>Outcome</Th>
                      <Th>Request</Th>
                      <Th />
                    </tr>
                  </thead>
                  <tbody>
                    {(logs.data?.items ?? []).map((entry) => (
                      <>
                        <tr key={entry.id}>
                          <Td className="text-xs whitespace-nowrap">
                            {new Date(entry.created_at).toLocaleString()}
                          </Td>
                          <Td className="font-medium">{entry.action}</Td>
                          <Td className="text-sm">
                            {entry.actor_label ?? '-'}
                            {entry.actor_ip && (
                              <p className="text-ink-muted text-xs">{entry.actor_ip}</p>
                            )}
                          </Td>
                          <Td className="text-ink-soft text-xs">
                            {entry.entity_type}
                            {entry.entity_id ? ` #${entry.entity_id}` : ''}
                          </Td>
                          <Td>
                            <Badge tone={entry.succeeded ? 'success' : 'danger'}>
                              {entry.succeeded ? 'ok' : 'failed'}
                            </Badge>
                          </Td>
                          <Td className="text-ink-muted font-mono text-xs">
                            {entry.request_id}
                          </Td>
                          <Td>
                            <Button
                              variant="ghost"
                              onClick={() => setOpenId(openId === entry.id ? null : entry.id)}
                            >
                              {openId === entry.id ? 'Hide' : 'Values'}
                            </Button>
                          </Td>
                        </tr>
                        {openId === entry.id && (
                          <tr key={`${entry.id}-detail`}>
                            <Td colSpan={7} className="bg-sunken">
                              <EntryDetail entry={entry} />
                            </Td>
                          </tr>
                        )}
                      </>
                    ))}
                  </tbody>
                </TableShell>
              )}
            </CardBody>
          </Card>
        </div>
      )}

      {tab === 'jobs' && (
        <Card>
          <CardHeader
            title="Scheduled job history"
            description="Every job is keyed, so a re-run shows as having written nothing - which is the evidence the idempotency works in practice."
          />
          <CardBody>
            {jobs.isPending ? (
              <SkeletonRows rows={4} />
            ) : jobs.isError ? (
              <ErrorPanel error={jobs.error} onRetry={() => void jobs.refetch()} />
            ) : (jobs.data ?? []).length === 0 ? (
              <EmptyState
                icon={Clock}
                title="No job runs recorded"
                description="Accrual, comp-off, expiry and the notification jobs record a run here."
              />
            ) : (
              <TableShell caption="Job runs">
                <thead>
                  <tr>
                    <Th>Job</Th>
                    <Th>Period</Th>
                    <Th>Status</Th>
                    <Th>Started</Th>
                    <Th className="text-right">Processed</Th>
                    <Th className="text-right">Skipped</Th>
                    <Th className="text-right">Failed</Th>
                  </tr>
                </thead>
                <tbody>
                  {(jobs.data ?? []).map((job) => (
                    <tr key={job.id}>
                      <Td className="font-medium">{job.job_name}</Td>
                      <Td className="text-sm">{job.period_key ?? '-'}</Td>
                      <Td>
                        <Badge
                          tone={
                            job.status === 'succeeded'
                              ? 'success'
                              : job.status === 'failed'
                                ? 'danger'
                                : 'neutral'
                          }
                        >
                          {job.status}
                        </Badge>
                      </Td>
                      <Td className="text-xs">
                        {job.started_at ? new Date(job.started_at).toLocaleString() : '-'}
                      </Td>
                      <Td className="text-right tabular-nums">{job.records_processed}</Td>
                      <Td className="text-right tabular-nums">{job.records_skipped}</Td>
                      <Td className="text-right tabular-nums">{job.records_failed}</Td>
                    </tr>
                  ))}
                </tbody>
              </TableShell>
            )}
          </CardBody>
        </Card>
      )}

      {tab === 'retention' && (
        <div className="space-y-5">
          {retention.isPending ? (
            <SkeletonRows rows={4} />
          ) : retention.isError ? (
            <ErrorPanel error={retention.error} onRetry={() => void retention.refetch()} />
          ) : retention.data ? (
            <>
              <Alert
                variant="warning"
                title="Nothing is purged automatically"
                icon={CircleAlert}
              >
                {retention.data.note}
              </Alert>

              {retention.data.unconfirmed.length > 0 && (
                <Alert variant="info" title="Unconfirmed retention periods">
                  {retention.data.unconfirmed.join(', ')} - a retention period destroys evidence
                  as well as data, so it should be a decision rather than a default nobody
                  looked at.
                </Alert>
              )}

              <Card>
                <CardHeader
                  title="What a purge would remove"
                  description="A dry run, always. Looking at this page deletes nothing."
                  action={
                    <Button variant="ghost" onClick={() => void retention.refetch()}>
                      <RefreshCw className="size-4" aria-hidden="true" />
                      Refresh
                    </Button>
                  }
                />
                <CardBody>
                  <TableShell caption="Retention position">
                    <thead>
                      <tr>
                        <Th>Category</Th>
                        <Th className="text-right">Keep for</Th>
                        <Th>Confirmed</Th>
                        <Th className="text-right">Rows held</Th>
                        <Th className="text-right">Beyond retention</Th>
                        <Th>Note</Th>
                      </tr>
                    </thead>
                    <tbody>
                      {retention.data.categories.map((item) => (
                        <tr key={item.category}>
                          <Td className="font-medium">{item.category.replace(/_/g, ' ')}</Td>
                          <Td className="text-right tabular-nums">
                            {item.retention_days} days
                          </Td>
                          <Td>
                            <Badge tone={item.confirmed ? 'success' : 'warning'}>
                              {item.confirmed ? 'yes' : 'no'}
                            </Badge>
                          </Td>
                          <Td className="text-right tabular-nums">
                            {item.rows_total.toLocaleString()}
                          </Td>
                          <Td className="text-right tabular-nums">
                            {item.rows_beyond_retention.toLocaleString()}
                          </Td>
                          <Td className="text-ink-soft max-w-sm text-xs">{item.note}</Td>
                        </tr>
                      ))}
                    </tbody>
                  </TableShell>
                  <p className="text-ink-muted mt-3 text-xs">
                    Purging is a command, not a button:{' '}
                    <code className="bg-sunken rounded px-1">iams purge-expired-data</code>{' '}
                    reports what it would delete, and only deletes with{' '}
                    <code className="bg-sunken rounded px-1">--apply</code>.
                  </p>
                </CardBody>
              </Card>
            </>
          ) : null}
        </div>
      )}
    </div>
  );
}
