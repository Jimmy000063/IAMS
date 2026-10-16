/**
 * The notification centre: everything one person has been told.
 *
 * Archived rows are kept and can be shown again, because "I was never told"
 * is a claim this page should be able to answer. Muting is per category and
 * the default is to notify - a preference row exists only where somebody has
 * asked for silence.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Archive, BellOff, CheckCheck, Inbox } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import { Button } from '@/components/ui/Button';
import { CheckboxField, SelectField } from '@/components/ui/Field';
import { Alert, EmptyState, ErrorPanel, SkeletonRows } from '@/components/ui/Feedback';
import { Badge, Card, CardBody, CardHeader, PageHeader } from '@/components/ui/Surface';
import {
  archiveNotification,
  categoryLabel,
  fetchNotifications,
  fetchPreferences,
  fetchUnreadSummary,
  markRead,
  priorityTone,
  relativeTime,
  setPreference,
  type Notification,
} from '@/features/notifications/api';
import { useToast } from '@/lib/toast';
import { cn } from '@/lib/utils';

/** Every category the backend defines, in the order they are most used. */
const CATEGORY_CODES = [
  'leave',
  'compoff',
  'attendance',
  'regularization',
  'shift',
  'holiday',
  'payroll',
  'announcement',
  'account',
  'system',
];

function NotificationRow({
  row,
  onArchive,
  archiving,
}: {
  row: Notification;
  onArchive: (id: number) => void;
  archiving: boolean;
}) {
  const unread = row.read_at === null;

  return (
    <li
      className={cn(
        'flex items-start gap-3 px-4 py-3',
        unread && 'bg-brand-50/40',
        row.archived_at !== null && 'opacity-60',
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          'mt-2 size-2 shrink-0 rounded-full',
          unread ? 'bg-brand-600' : 'bg-line-strong',
        )}
      />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-ink text-sm font-medium">{row.title}</p>
          <Badge tone="neutral">{categoryLabel(row.category)}</Badge>
          {row.priority !== 'normal' && (
            <Badge tone={priorityTone(row.priority)}>{row.priority}</Badge>
          )}
          {row.archived_at !== null && <Badge tone="neutral">archived</Badge>}
        </div>
        {row.body && <p className="text-ink-soft mt-1 text-sm">{row.body}</p>}
        <p className="text-ink-muted mt-1 text-xs">
          <span title={new Date(row.created_at).toLocaleString()}>
            {relativeTime(row.created_at)}
          </span>
          {row.link_path && (
            <>
              {' · '}
              <Link to={row.link_path} className="text-brand-700 hover:text-brand-800">
                Open
              </Link>
            </>
          )}
        </p>
      </div>
      {row.archived_at === null && (
        <button
          type="button"
          onClick={() => onArchive(row.id)}
          disabled={archiving}
          title="Dismiss. The row is kept, so the record of having been told survives."
          className="text-ink-muted hover:text-ink shrink-0 rounded p-1 transition-colors"
          aria-label={`Dismiss ${row.title}`}
        >
          <Archive className="size-4" aria-hidden="true" />
        </button>
      )}
    </li>
  );
}

export function NotificationsPage() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [category, setCategory] = useState('');
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [includeArchived, setIncludeArchived] = useState(false);
  const [showPreferences, setShowPreferences] = useState(false);

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['notifications'] });

  const summary = useQuery({
    queryKey: ['notifications', 'unread'],
    queryFn: fetchUnreadSummary,
  });

  const listed = useQuery({
    queryKey: ['notifications', 'list', { category, unreadOnly, includeArchived }],
    queryFn: () =>
      fetchNotifications({
        ...(category ? { category } : {}),
        unread_only: unreadOnly,
        include_archived: includeArchived,
        limit: 100,
      }),
  });

  const preferences = useQuery({
    queryKey: ['notifications', 'preferences'],
    queryFn: fetchPreferences,
    enabled: showPreferences,
  });

  const markAll = useMutation({
    mutationFn: () => markRead(),
    onSuccess: (data) => {
      void refresh();
      toast.success(data.marked === 0 ? 'Nothing was unread.' : `${data.marked} marked read.`);
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  const archive = useMutation({
    mutationFn: archiveNotification,
    onSuccess: () => void refresh(),
    onError: (error: unknown) => toast.apiError(error),
  });

  const mute = useMutation({
    mutationFn: ({ code, muted }: { code: string; muted: boolean }) =>
      setPreference(code, muted),
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ['notifications', 'preferences'] });
      toast.success(
        data.muted
          ? `${categoryLabel(data.category)} notifications muted.`
          : `${categoryLabel(data.category)} notifications turned back on.`,
      );
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  const rows = listed.data ?? [];
  const counts = summary.data?.unread_by_category ?? {};
  const categories = Object.keys(counts);

  return (
    <div>
      <PageHeader
        title="Notifications"
        description="Everything you have been told: leave and comp-off decisions, attendance corrections, shift changes, holidays and announcements. Delivery is in-app, so nothing here depends on a paid email or SMS service."
        action={
          <div className="flex flex-wrap gap-2">
            <Button
              variant="secondary"
              onClick={() => markAll.mutate()}
              disabled={markAll.isPending || (summary.data?.unread_total ?? 0) === 0}
            >
              <CheckCheck className="size-4" aria-hidden="true" />
              Mark all read
            </Button>
            <Button variant="ghost" onClick={() => setShowPreferences((value) => !value)}>
              <BellOff className="size-4" aria-hidden="true" />
              {showPreferences ? 'Hide' : 'Manage'} preferences
            </Button>
          </div>
        }
      />

      {summary.data?.has_critical && (
        <Alert variant="warning" title="Something marked critical is unread">
          Critical notices are usually account security or a company-wide announcement that
          needs acknowledging.
        </Alert>
      )}

      {showPreferences && (
        <Card className="mb-5">
          <CardHeader
            title="What you are notified about"
            description="Muting stops the notification; it does not remove you from an announcement's audience, and it never affects the audit trail."
          />
          <CardBody>
            {preferences.isPending ? (
              <SkeletonRows rows={4} />
            ) : preferences.isError ? (
              <ErrorPanel
                error={preferences.error}
                onRetry={() => void preferences.refetch()}
              />
            ) : (
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {(preferences.data ?? []).map((item) => (
                  <CheckboxField
                    key={item.category}
                    label={`${categoryLabel(item.category)} notifications`}
                    checked={!item.muted}
                    disabled={mute.isPending}
                    onChange={(event) =>
                      mute.mutate({ code: item.category, muted: !event.target.checked })
                    }
                  />
                ))}
              </div>
            )}
          </CardBody>
        </Card>
      )}

      <Card>
        <CardHeader
          title={summary.data ? `${summary.data.unread_total} unread` : 'Your notifications'}
          description={
            categories.length > 0
              ? categories.map((code) => `${categoryLabel(code)}: ${counts[code]}`).join(' · ')
              : 'Nothing unread.'
          }
          action={
            <div className="flex flex-wrap items-end gap-3">
              <div className="w-44">
                <SelectField
                  label="Category"
                  value={category}
                  onChange={(event) => setCategory(event.target.value)}
                  options={[
                    { value: '', label: 'All categories' },
                    ...CATEGORY_CODES.map((code) => ({
                      value: code,
                      label: categoryLabel(code),
                    })),
                  ]}
                />
              </div>
              <CheckboxField
                label="Unread only"
                checked={unreadOnly}
                onChange={(event) => setUnreadOnly(event.target.checked)}
              />
              <CheckboxField
                label="Include dismissed"
                checked={includeArchived}
                onChange={(event) => setIncludeArchived(event.target.checked)}
              />
            </div>
          }
        />
        <CardBody className="px-0 py-0">
          {listed.isPending ? (
            <div className="p-4">
              <SkeletonRows rows={5} />
            </div>
          ) : listed.isError ? (
            <div className="p-4">
              <ErrorPanel error={listed.error} onRetry={() => void listed.refetch()} />
            </div>
          ) : rows.length === 0 ? (
            <div className="p-4">
              <EmptyState
                icon={Inbox}
                title="Nothing to show"
                description={
                  unreadOnly
                    ? 'Nothing is unread.'
                    : 'Decisions, corrections and announcements will appear here.'
                }
              />
            </div>
          ) : (
            <ul className="divide-line divide-y">
              {rows.map((row) => (
                <NotificationRow
                  key={row.id}
                  row={row}
                  onArchive={(id) => archive.mutate(id)}
                  archiving={archive.isPending}
                />
              ))}
            </ul>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
