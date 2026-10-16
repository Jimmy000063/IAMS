/**
 * Authoring announcements: draft, target, schedule, publish, withdraw.
 *
 * Drafting is separate from publishing on purpose, and the panel keeps the two
 * visibly apart: publishing reaches everybody at once, so it is a deliberate
 * second action on a notice that has already been read back.
 *
 * Withdrawing keeps the row and demands a reason, because people have already
 * read it. There is no delete.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Image, Megaphone, Paperclip, Pin, Send, Undo2, Users } from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/Button';
import { DatePicker, DateTimeField } from '@/components/ui/DatePicker';
import { CheckboxField, SelectField, TextAreaField, TextField } from '@/components/ui/Field';
import { Alert, EmptyState, ErrorPanel, SkeletonRows } from '@/components/ui/Feedback';
import { Modal } from '@/components/ui/Modal';
import { Badge, Card, CardBody, CardHeader, TableShell, Td, Th } from '@/components/ui/Surface';
import { fetchDepartments, fetchWorkLocations } from '@/features/employees/api';
import {
  createAnnouncement,
  fetchAnnouncements,
  fetchReadReport,
  publishAnnouncement,
  runAnnouncementSchedule,
  uploadAnnouncementFile,
  withdrawAnnouncement,
  type Announcement,
  type Priority,
  type TargetSpec,
  type TargetType,
} from '@/features/notifications/api';
import { useToast } from '@/lib/toast';

const STATUS_TONES: Record<string, 'neutral' | 'brand' | 'success' | 'warning' | 'danger'> = {
  draft: 'neutral',
  scheduled: 'warning',
  published: 'success',
  expired: 'neutral',
  withdrawn: 'danger',
};

function describeTargets(targets: TargetSpec[]): string {
  if (targets.length === 0) return 'nobody';
  return targets
    .map((target) =>
      target.target_type === 'all'
        ? 'everyone'
        : `${target.target_type.replace(/_/g, ' ')} #${target.target_id}`,
    )
    .join(', ');
}

export function AnnouncementAdminPanel() {
  const toast = useToast();
  const queryClient = useQueryClient();

  const [composing, setComposing] = useState(false);
  const [withdrawingId, setWithdrawingId] = useState<number | null>(null);
  const [reason, setReason] = useState('');
  const [reportFor, setReportFor] = useState<number | null>(null);

  // The draft being composed.
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [summary, setSummary] = useState('');
  const [priority, setPriority] = useState<Priority>('normal');
  const [targetType, setTargetType] = useState<TargetType>('all');
  const [targetId, setTargetId] = useState('');
  const [publishAt, setPublishAt] = useState('');
  const [pinUntil, setPinUntil] = useState('');
  const [notifyOnPublish, setNotifyOnPublish] = useState(true);

  const announcements = useQuery({
    queryKey: ['announcements', 'admin'],
    queryFn: fetchAnnouncements,
  });

  const departments = useQuery({
    queryKey: ['org', 'departments'],
    queryFn: () => fetchDepartments(),
    enabled: targetType === 'department',
  });

  const locations = useQuery({
    queryKey: ['org', 'work-locations'],
    queryFn: () => fetchWorkLocations(),
    enabled: targetType === 'work_location',
  });

  const report = useQuery({
    queryKey: ['announcements', 'read-report', reportFor],
    queryFn: () => fetchReadReport(reportFor as number),
    enabled: reportFor !== null,
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['announcements'] });
    void queryClient.invalidateQueries({ queryKey: ['notifications'] });
  };

  const resetDraft = () => {
    setTitle('');
    setBody('');
    setSummary('');
    setPriority('normal');
    setTargetType('all');
    setTargetId('');
    setPublishAt('');
    setPinUntil('');
    setNotifyOnPublish(true);
  };

  const create = useMutation({
    mutationFn: () =>
      createAnnouncement({
        title: title.trim(),
        body: body.trim(),
        summary: summary.trim() || null,
        priority,
        targets: [
          {
            target_type: targetType,
            target_id: targetType === 'all' ? null : Number(targetId),
          },
        ],
        publish_at: publishAt ? new Date(publishAt).toISOString() : null,
        pin_until: pinUntil || null,
        notify_on_publish: notifyOnPublish,
      }),
    onSuccess: () => {
      invalidate();
      setComposing(false);
      resetDraft();
      toast.success('Saved as a draft. Nobody can see it until it is published.');
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  const publish = useMutation({
    mutationFn: (item: Announcement) => publishAnnouncement(item.id),
    onSuccess: (result) => {
      invalidate();
      if (result.status === 'scheduled') {
        toast.show({
          variant: 'info',
          title: 'Scheduled.',
          description: `It becomes visible at ${new Date(result.publish_at).toLocaleString()}.`,
        });
      } else {
        toast.success(
          `Published to ${result.recipients} employee(s); ${result.notifications_created} notification(s) created.`,
        );
      }
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  const withdraw = useMutation({
    mutationFn: ({ id, why }: { id: number; why: string }) => withdrawAnnouncement(id, why),
    onSuccess: () => {
      invalidate();
      setWithdrawingId(null);
      setReason('');
      toast.success('Withdrawn. The notice and the reason stay on the record.');
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  const upload = useMutation({
    mutationFn: ({ id, file, asImage }: { id: number; file: File; asImage: boolean }) =>
      uploadAnnouncementFile(id, file, asImage),
    onSuccess: (_data, variables) => {
      invalidate();
      toast.success(variables.asImage ? 'Banner image set.' : 'Attachment added.');
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  const runSchedule = useMutation({
    mutationFn: runAnnouncementSchedule,
    onSuccess: (result) => {
      invalidate();
      toast.success(
        result.published === 0 && result.expired === 0
          ? 'Nothing was due.'
          : `${result.published} published, ${result.notifications_created} notification(s) created, ${result.expired} expired.`,
      );
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  const rows = announcements.data ?? [];
  const targetOptions =
    targetType === 'department'
      ? (departments.data ?? []).map((item) => ({
          value: String(item.id),
          label: item.name,
        }))
      : targetType === 'work_location'
        ? (locations.data ?? []).map((item) => ({
            value: String(item.id),
            label: item.name,
          }))
        : [];

  const canSubmit =
    title.trim().length >= 3 &&
    body.trim().length > 0 &&
    (targetType === 'all' || targetId !== '');

  return (
    <div className="space-y-5">
      <Alert variant="info" title="A draft reaches nobody">
        Publishing is a separate action, so a notice can be written and read back first. A
        published announcement can still be corrected, and the edit is audited because people
        may already have read the earlier text.
      </Alert>

      <Card>
        <CardHeader
          title="Announcements"
          description="Drafts, scheduled notices and what has been published."
          action={
            <div className="flex flex-wrap gap-2">
              <Button onClick={() => setComposing(true)}>
                <Megaphone className="size-4" aria-hidden="true" />
                New announcement
              </Button>
              <Button
                variant="secondary"
                onClick={() => runSchedule.mutate()}
                disabled={runSchedule.isPending}
                title="Sends notifications for anything whose publication time has passed. Safe to re-run."
              >
                {runSchedule.isPending ? 'Working…' : 'Run schedule now'}
              </Button>
            </div>
          }
        />
        <CardBody>
          {announcements.isPending ? (
            <SkeletonRows rows={4} />
          ) : announcements.isError ? (
            <ErrorPanel
              error={announcements.error}
              onRetry={() => void announcements.refetch()}
            />
          ) : rows.length === 0 ? (
            <EmptyState
              icon={Megaphone}
              title="No announcements yet"
              description="Nothing is seeded: a company notice is not something to invent."
            />
          ) : (
            <TableShell caption="Announcements">
              <thead>
                <tr>
                  <Th>Title</Th>
                  <Th>Status</Th>
                  <Th>Audience</Th>
                  <Th className="text-right">Recipients</Th>
                  <Th>When</Th>
                  <Th>Actions</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((item) => (
                  <tr key={item.id}>
                    <Td>
                      <p className="font-medium">
                        {item.pin_until && (
                          <Pin
                            className="text-accent-700 mr-1 inline size-3.5"
                            aria-label="Pinned"
                          />
                        )}
                        {item.title}
                      </p>
                      {item.priority !== 'normal' && (
                        <Badge tone={item.priority === 'critical' ? 'danger' : 'warning'}>
                          {item.priority}
                        </Badge>
                      )}
                    </Td>
                    <Td>
                      <Badge tone={STATUS_TONES[item.status] ?? 'neutral'}>{item.status}</Badge>
                      {item.withdrawal_reason && (
                        <p className="text-ink-muted mt-1 max-w-48 text-xs">
                          {item.withdrawal_reason}
                        </p>
                      )}
                    </Td>
                    <Td className="text-ink-soft text-sm">{describeTargets(item.targets)}</Td>
                    <Td className="text-right tabular-nums">{item.recipient_count ?? '-'}</Td>
                    <Td className="text-ink-soft text-xs">
                      {item.publish_at
                        ? new Date(item.publish_at).toLocaleString()
                        : 'not scheduled'}
                    </Td>
                    <Td>
                      <div className="flex flex-wrap gap-1">
                        {(item.status === 'draft' || item.status === 'scheduled') && (
                          <Button
                            variant="secondary"
                            onClick={() => publish.mutate(item)}
                            disabled={publish.isPending}
                            title={
                              item.publish_at
                                ? 'Publishes now, or schedules it if its publication time is still ahead.'
                                : 'Publishes it immediately.'
                            }
                          >
                            <Send className="size-3.5" aria-hidden="true" />
                            Publish
                          </Button>
                        )}
                        {(item.status === 'published' || item.status === 'scheduled') && (
                          <Button variant="ghost" onClick={() => setWithdrawingId(item.id)}>
                            <Undo2 className="size-3.5" aria-hidden="true" />
                            Withdraw
                          </Button>
                        )}
                        {(item.status === 'draft' || item.status === 'scheduled') && (
                          <>
                            <label
                              className="text-ink-soft hover:bg-sunken flex cursor-pointer items-center gap-1 rounded px-2 py-1 text-xs"
                              title="PDF, JPEG, PNG, DOCX or XLSX. One attachment per announcement."
                            >
                              <Paperclip className="size-3.5" aria-hidden="true" />
                              {item.attachment_file_id ? 'Replace file' : 'Attach'}
                              <input
                                type="file"
                                className="sr-only"
                                accept=".pdf,.jpg,.jpeg,.png,.docx,.xlsx"
                                onChange={(event) => {
                                  const file = event.target.files?.[0];
                                  if (file) {
                                    upload.mutate({ id: item.id, file, asImage: false });
                                  }
                                  event.target.value = '';
                                }}
                              />
                            </label>
                            <label
                              className="text-ink-soft hover:bg-sunken flex cursor-pointer items-center gap-1 rounded px-2 py-1 text-xs"
                              title="JPEG or PNG only."
                            >
                              <Image className="size-3.5" aria-hidden="true" />
                              {item.image_file_id ? 'Replace image' : 'Image'}
                              <input
                                type="file"
                                className="sr-only"
                                accept=".jpg,.jpeg,.png"
                                onChange={(event) => {
                                  const file = event.target.files?.[0];
                                  if (file) {
                                    upload.mutate({ id: item.id, file, asImage: true });
                                  }
                                  event.target.value = '';
                                }}
                              />
                            </label>
                          </>
                        )}
                        {item.status !== 'draft' && (
                          <Button variant="ghost" onClick={() => setReportFor(item.id)}>
                            <Users className="size-3.5" aria-hidden="true" />
                            Who read it
                          </Button>
                        )}
                      </div>
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
        title="New announcement"
        description="Saved as a draft. Publishing is a separate step."
        footer={
          <>
            <Button variant="ghost" onClick={() => setComposing(false)}>
              Cancel
            </Button>
            <Button onClick={() => create.mutate()} disabled={!canSubmit || create.isPending}>
              {create.isPending ? 'Saving…' : 'Save draft'}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <TextField
            label="Title"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            required
            hint="At least three characters. It is what people see in their notification."
          />
          <TextAreaField
            label="Body"
            value={body}
            onChange={(event) => setBody(event.target.value)}
            rows={6}
            required
          />
          <TextField
            label="Summary"
            value={summary}
            onChange={(event) => setSummary(event.target.value)}
            hint="Optional. Used in the notification instead of the opening of the body."
          />
          <div className="grid gap-4 sm:grid-cols-2">
            <SelectField
              label="Priority"
              value={priority}
              onChange={(event) => setPriority(event.target.value as Priority)}
              options={[
                { value: 'normal', label: 'Normal' },
                { value: 'important', label: 'Important' },
                { value: 'critical', label: 'Critical - asks for acknowledgement' },
              ]}
            />
            <SelectField
              label="Audience"
              value={targetType}
              onChange={(event) => {
                setTargetType(event.target.value as TargetType);
                setTargetId('');
              }}
              options={[
                { value: 'all', label: 'Everyone' },
                { value: 'department', label: 'A department' },
                { value: 'work_location', label: 'A work location' },
              ]}
              hint="The server resolves this; an announcement never reaches anybody outside it."
            />
          </div>

          {targetType !== 'all' && (
            <SelectField
              label={targetType === 'department' ? 'Department' : 'Work location'}
              value={targetId}
              onChange={(event) => setTargetId(event.target.value)}
              options={[{ value: '', label: 'Select one' }, ...targetOptions]}
              required
            />
          )}

          <div className="grid gap-4 sm:grid-cols-2">
            <DateTimeField
              label="Publish at"
              value={publishAt}
              onChange={setPublishAt}
              hint="Leave empty to publish immediately when you choose to. A date with no time means midnight."
            />
            <DatePicker
              label="Pin until"
              value={pinUntil}
              onChange={setPinUntil}
              hint="Keeps it at the top of the feed."
            />
          </div>

          <CheckboxField
            label="Put it in each recipient's notification centre when published"
            checked={notifyOnPublish}
            onChange={(event) => setNotifyOnPublish(event.target.checked)}
          />
        </div>
      </Modal>

      <Modal
        open={withdrawingId !== null}
        onClose={() => {
          setWithdrawingId(null);
          setReason('');
        }}
        title="Withdraw this announcement"
        description="It leaves the feed. The notice and your reason stay on the record, because people have already read it."
        footer={
          <>
            <Button
              variant="ghost"
              onClick={() => {
                setWithdrawingId(null);
                setReason('');
              }}
            >
              Cancel
            </Button>
            <Button
              variant="danger"
              onClick={() =>
                withdrawingId !== null && withdraw.mutate({ id: withdrawingId, why: reason })
              }
              disabled={reason.trim().length < 3 || withdraw.isPending}
            >
              Withdraw
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
          hint="Required. Why it came down is part of the record."
        />
      </Modal>

      <Modal
        open={reportFor !== null}
        onClose={() => setReportFor(null)}
        title="Who has read it"
        description="Reading and acknowledging are counted separately."
        footer={
          <Button variant="ghost" onClick={() => setReportFor(null)}>
            Close
          </Button>
        }
      >
        {report.isPending ? (
          <SkeletonRows rows={3} />
        ) : report.isError ? (
          <ErrorPanel error={report.error} onRetry={() => void report.refetch()} />
        ) : report.data ? (
          <div className="space-y-4">
            <dl className="grid gap-3 sm:grid-cols-3">
              {[
                ['Audience', report.data.audience_size],
                ['Read', report.data.read_count],
                ['Acknowledged', report.data.acknowledged_count],
              ].map(([label, value]) => (
                <div key={String(label)} className="border-line rounded-lg border p-3">
                  <dt className="text-ink-soft text-xs font-medium tracking-wide uppercase">
                    {label}
                  </dt>
                  <dd className="text-ink mt-1 text-lg font-semibold tabular-nums">{value}</dd>
                </div>
              ))}
            </dl>

            {report.data.unread.length > 0 && (
              <div>
                <p className="text-ink mb-2 text-sm font-medium">
                  Not read by {report.data.unread.length}
                  {report.data.unread.length === 200 ? ' (first 200)' : ''}
                </p>
                <ul className="text-ink-soft max-h-48 space-y-1 overflow-y-auto text-sm">
                  {report.data.unread.map((person) => (
                    <li key={person.employee_id}>
                      {person.employee_code} - {person.employee_name}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        ) : null}
      </Modal>
    </div>
  );
}
