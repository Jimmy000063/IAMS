/**
 * The employee's announcement feed.
 *
 * The audience filter is applied by the server: an announcement meant for
 * another office is not in the response at all, so there is nothing here that
 * hides it. The feed endpoint takes no employee id for the same reason.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Megaphone, Paperclip, Pin } from 'lucide-react';

import { Button } from '@/components/ui/Button';
import { EmptyState, ErrorPanel, SkeletonRows } from '@/components/ui/Feedback';
import { Badge, Card, CardBody, CardHeader, PageHeader } from '@/components/ui/Surface';
import {
  downloadAnnouncementFile,
  fetchFeed,
  markAnnouncementRead,
  priorityTone,
  relativeTime,
  type FeedItem,
} from '@/features/notifications/api';
import { useAuth } from '@/lib/auth';
import { useToast } from '@/lib/toast';

function AnnouncementCard({
  item,
  onRead,
  onAcknowledge,
  busy,
  canRecordRead,
  onDownload,
}: {
  item: FeedItem;
  onRead: (id: number) => void;
  onAcknowledge: (id: number) => void;
  onDownload: (id: number) => void;
  busy: boolean;
  /**
   * A read receipt belongs to an employee, so an account with no employee
   * record behind it - the bootstrap administrator - has nowhere to record one.
   * It still sees the notice; it is simply not counted in the read report.
   */
  canRecordRead: boolean;
}) {
  const needsAcknowledgement = item.priority === 'critical' && !item.is_acknowledged;

  return (
    <Card>
      <CardHeader
        title={
          <span className="flex flex-wrap items-center gap-2">
            {item.is_pinned && <Pin className="text-accent-700 size-4" aria-label="Pinned" />}
            {item.title}
            {item.priority !== 'normal' && (
              <Badge tone={priorityTone(item.priority)}>{item.priority}</Badge>
            )}
            {!item.is_read && <Badge tone="brand">new</Badge>}
          </span>
        }
        description={
          item.publish_at ? (
            <span title={new Date(item.publish_at).toLocaleString()}>
              Published {relativeTime(item.publish_at)}
              {item.expires_at
                ? ` · until ${new Date(item.expires_at).toLocaleDateString()}`
                : ''}
            </span>
          ) : undefined
        }
      />
      <CardBody className="space-y-4">
        <p className="text-ink-soft text-sm whitespace-pre-wrap">{item.body}</p>

        {item.attachment_file_id !== null && (
          <Button variant="secondary" onClick={() => onDownload(item.id)}>
            <Paperclip className="size-4" aria-hidden="true" />
            Download the attachment
          </Button>
        )}

        <div className="flex flex-wrap items-center gap-2">
          {!item.is_read && canRecordRead && (
            <Button variant="secondary" onClick={() => onRead(item.id)} disabled={busy}>
              <Check className="size-4" aria-hidden="true" />
              Mark as read
            </Button>
          )}
          {needsAcknowledgement && canRecordRead && (
            <Button onClick={() => onAcknowledge(item.id)} disabled={busy}>
              I acknowledge this
            </Button>
          )}
          {item.is_acknowledged && (
            <Badge tone="success">
              <Check className="size-3" aria-hidden="true" />
              acknowledged
            </Badge>
          )}
        </div>

        {!canRecordRead && (
          <p className="text-ink-muted text-xs">
            This account is not linked to an employee record, so its reading is not tracked.
          </p>
        )}

        {item.priority === 'critical' && !item.is_acknowledged && canRecordRead && (
          <p className="text-ink-muted text-xs">
            Opening a notice is not the same as confirming you have read it, so a critical one
            asks for an explicit acknowledgement.
          </p>
        )}
      </CardBody>
    </Card>
  );
}

export function AnnouncementsPage() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const canRecordRead = user?.employee_id != null;

  const feed = useQuery({ queryKey: ['announcements', 'feed'], queryFn: fetchFeed });

  const read = useMutation({
    mutationFn: ({ id, acknowledge }: { id: number; acknowledge: boolean }) =>
      markAnnouncementRead(id, acknowledge),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: ['announcements', 'feed'] });
      void queryClient.invalidateQueries({ queryKey: ['notifications'] });
      if (variables.acknowledge) toast.success('Acknowledgement recorded.');
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  const download = useMutation({
    mutationFn: (id: number) => downloadAnnouncementFile(id),
    onError: (error: unknown) => toast.apiError(error),
  });

  const items = feed.data ?? [];
  const unreadCount = items.filter((item) => !item.is_read).length;

  return (
    <div>
      <PageHeader
        title="Announcements"
        description="Company notices that apply to you. Which ones reach you is decided by the server from your department, work location and groups."
      />

      {feed.isPending ? (
        <SkeletonRows rows={4} />
      ) : feed.isError ? (
        <ErrorPanel error={feed.error} onRetry={() => void feed.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState
          icon={Megaphone}
          title="No announcements"
          description="Nothing has been published to you. Notices appear here as soon as they are published."
        />
      ) : (
        <div className="space-y-4">
          {unreadCount > 0 && (
            <p className="text-ink-soft text-sm">
              {unreadCount} unread of {items.length}.
            </p>
          )}
          {items.map((item) => (
            <AnnouncementCard
              key={item.id}
              item={item}
              onRead={(id) => read.mutate({ id, acknowledge: false })}
              onAcknowledge={(id) => read.mutate({ id, acknowledge: true })}
              busy={read.isPending}
              canRecordRead={canRecordRead}
              onDownload={(id) => download.mutate(id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
