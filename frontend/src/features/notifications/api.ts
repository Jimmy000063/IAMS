/**
 * Announcements and the notification centre.
 *
 * Two separate things on purpose: an announcement is a company notice with an
 * audience and a read report; a notification is one line in one person's
 * centre. The feed endpoint takes no employee id - the audience comes from the
 * token, so there is nothing here for the browser to tamper with.
 */

import { api } from '@/lib/api';
import type { IsoDate } from '@/lib/dates';

export type Priority = 'normal' | 'important' | 'critical';

export type AnnouncementStatus = 'draft' | 'scheduled' | 'published' | 'expired' | 'withdrawn';

export type TargetType = 'all' | 'department' | 'work_location' | 'employee_group' | 'employee';

export interface TargetSpec {
  target_type: TargetType;
  target_id: number | null;
}

export interface Announcement {
  id: number;
  title: string;
  body: string;
  summary: string | null;
  priority: Priority;
  status: AnnouncementStatus;
  publish_at: string | null;
  expires_at: string | null;
  published_at: string | null;
  withdrawn_at: string | null;
  withdrawal_reason: string | null;
  pin_until: IsoDate | null;
  notify_on_publish: boolean;
  notifications_sent_at: string | null;
  recipient_count: number | null;
  attachment_file_id: number | null;
  image_file_id: number | null;
  targets: TargetSpec[];
  created_at: string;
}

export interface FeedItem {
  id: number;
  title: string;
  body: string;
  summary: string | null;
  priority: Priority;
  publish_at: string | null;
  expires_at: string | null;
  is_pinned: boolean;
  is_read: boolean;
  is_acknowledged: boolean;
  attachment_file_id: number | null;
  image_file_id: number | null;
}

export interface PublishResult {
  status: 'published' | 'scheduled';
  publish_at: string;
  recipients: number;
  /** Lower than `recipients` when some have no account or have muted the category. */
  notifications_created: number;
}

export interface ReadReport {
  announcement_id: number;
  title: string;
  audience_size: number;
  read_count: number;
  acknowledged_count: number;
  unread: { employee_id: number; employee_code: string | null; employee_name: string | null }[];
}

export interface Notification {
  id: number;
  category: string;
  priority: Priority;
  title: string;
  body: string | null;
  /** An application path, not a URL: the host differs per deployment. */
  link_path: string | null;
  entity_type: string | null;
  entity_id: string | null;
  read_at: string | null;
  archived_at: string | null;
  created_at: string;
  payload: Record<string, unknown> | null;
}

export interface UnreadSummary {
  unread_total: number;
  unread_by_category: Record<string, number>;
  has_critical: boolean;
}

export interface Preference {
  category: string;
  muted: boolean;
}

// --------------------------------------------------------------- my feed

export const fetchFeed = () => api.get<FeedItem[]>('/announcements/feed');

export const markAnnouncementRead = (id: number, acknowledge = false) =>
  api.post<void>(`/announcements/${id}/read`, { acknowledge });

// --------------------------------------------------------- notifications

export const fetchNotifications = (params: {
  unread_only?: boolean;
  category?: string;
  include_archived?: boolean;
  limit?: number;
}) =>
  api.get<Notification[]>('/notifications', {
    query: {
      unread_only: params.unread_only ?? false,
      category: params.category,
      include_archived: params.include_archived ?? false,
      limit: params.limit ?? 50,
    },
  });

export const fetchUnreadSummary = () => api.get<UnreadSummary>('/notifications/unread-count');

export const markRead = (notificationIds?: number[]) =>
  api.post<{ marked: number }>(
    '/notifications/mark-read',
    notificationIds ? { notification_ids: notificationIds } : {},
  );

export const archiveNotification = (id: number) =>
  api.post<Notification>(`/notifications/${id}/archive`);

export const fetchPreferences = () => api.get<Preference[]>('/notifications/preferences');

export const setPreference = (category: string, muted: boolean) =>
  api.put<Preference>('/notifications/preferences', { category, muted });

// ------------------------------------------------------------- authoring

/**
 * Every announcement, drafts included.
 *
 * Unfiltered on purpose: the admin list is small and the status filter is a
 * client-side toggle, which keeps the drafts and the published notices on one
 * screen where they can be compared.
 */
export const fetchAnnouncements = () => api.get<Announcement[]>('/admin/announcements');

export interface AnnouncementDraft {
  title: string;
  body: string;
  summary?: string | null;
  priority: Priority;
  targets: TargetSpec[];
  publish_at?: string | null;
  expires_at?: string | null;
  pin_until?: IsoDate | null;
  notify_on_publish: boolean;
}

export const createAnnouncement = (draft: AnnouncementDraft) =>
  api.post<Announcement>('/admin/announcements', draft);

export const updateAnnouncement = (id: number, patch: Partial<AnnouncementDraft>) =>
  api.patch<Announcement>(`/admin/announcements/${id}`, patch);

export const publishAnnouncement = (id: number, publishAt?: string | null) =>
  api.post<PublishResult>(`/admin/announcements/${id}/publish`, {
    publish_at: publishAt ?? null,
  });

export const withdrawAnnouncement = (id: number, reason: string) =>
  api.post<Announcement>(`/admin/announcements/${id}/withdraw`, { reason });

/**
 * Attach a file, or set the banner image.
 *
 * `fetch` directly rather than the JSON client: the body is FormData and the
 * browser has to set the multipart boundary itself.
 */
export async function uploadAnnouncementFile(
  announcementId: number,
  file: File,
  asImage: boolean,
): Promise<Announcement> {
  const { tokenStore, ApiError } = await import('@/lib/api');
  const form = new FormData();
  form.append('file', file);

  const tokens = tokenStore.get();
  const base = import.meta.env.VITE_API_BASE_URL ?? '/api/v1';
  const response = await fetch(
    `${base}/admin/announcements/${announcementId}/files?as_image=${asImage}`,
    {
      method: 'POST',
      headers: tokens ? { Authorization: `Bearer ${tokens.accessToken}` } : {},
      body: form,
    },
  );

  const body = await response.json();
  if (!response.ok) {
    throw new ApiError({
      status: response.status,
      code: body?.error?.code ?? 'http_error',
      message: body?.error?.message ?? 'The file could not be uploaded.',
      details: body?.error?.details ?? [],
      requestId: response.headers.get('X-Request-ID'),
    });
  }
  return body as Announcement;
}

/** The attachment, fetched with the bearer token and saved by the browser. */
export const downloadAnnouncementFile = async (
  announcementId: number,
  asImage = false,
): Promise<void> => {
  const { downloadAuthenticatedFile } = await import('@/features/employees/api');
  await downloadAuthenticatedFile(
    `/announcements/${announcementId}/file?as_image=${asImage}`,
    asImage ? 'announcement-image' : 'announcement-attachment',
  );
};

export const fetchReadReport = (id: number) =>
  api.get<ReadReport>(`/admin/announcements/${id}/read-report`);

export const runAnnouncementSchedule = () =>
  api.post<{
    as_of: string;
    published: number;
    notifications_created: number;
    expired: number;
  }>('/admin/announcements/run-schedule');

// ----------------------------------------------------------------- shared

/** How a category reads in the interface. */
export const CATEGORY_LABELS: Record<string, string> = {
  leave: 'Leave',
  compoff: 'Comp-off',
  attendance: 'Attendance',
  regularization: 'Corrections',
  shift: 'Shifts',
  holiday: 'Holidays',
  payroll: 'Payroll',
  announcement: 'Announcements',
  account: 'Account',
  system: 'System',
};

export const categoryLabel = (code: string) => CATEGORY_LABELS[code] ?? code;

export const priorityTone = (priority: Priority): 'danger' | 'warning' | 'neutral' =>
  priority === 'critical' ? 'danger' : priority === 'important' ? 'warning' : 'neutral';

/**
 * A short, readable age for a notification.
 *
 * Relative rather than absolute because what matters in a centre is "is this
 * new"; the exact instant is in the title attribute for anyone who needs it.
 */
export function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '';
  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 60) return 'just now';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}
