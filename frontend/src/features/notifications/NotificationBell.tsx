/**
 * The unread badge and its drop-down, in the top bar.
 *
 * Polled rather than pushed. A WebSocket would be a second transport to secure,
 * deploy and keep alive for something that is not time-critical: a leave
 * decision is no less useful thirty seconds later. Polling is paused while the
 * tab is hidden, so an idle browser is not asking all day.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Bell, CheckCheck } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';

import {
  categoryLabel,
  fetchNotifications,
  fetchUnreadSummary,
  markRead,
  relativeTime,
} from '@/features/notifications/api';
import { useAuth } from '@/lib/auth';
import { cn } from '@/lib/utils';

const POLL_MS = 60_000;

export function NotificationBell() {
  const { canAny } = useAuth();
  const allowed = canAny('notification.read_self');
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const summary = useQuery({
    queryKey: ['notifications', 'unread'],
    queryFn: fetchUnreadSummary,
    enabled: allowed,
    refetchInterval: POLL_MS,
    // Nothing to ask about while nobody is looking at the tab.
    refetchIntervalInBackground: false,
  });

  const recent = useQuery({
    queryKey: ['notifications', 'recent'],
    queryFn: () => fetchNotifications({ limit: 8 }),
    enabled: allowed && open,
  });

  const markAll = useMutation({
    mutationFn: () => markRead(),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['notifications'] });
    },
  });

  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [open]);

  if (!allowed) return null;

  const unread = summary.data?.unread_total ?? 0;
  const critical = summary.data?.has_critical ?? false;
  const rows = recent.data ?? [];

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label={unread === 0 ? 'Notifications' : `Notifications, ${unread} unread`}
        className="text-ink-soft hover:bg-sunken relative rounded-md p-2 transition-colors"
      >
        <Bell className="size-5" aria-hidden="true" />
        {unread > 0 && (
          <span
            aria-hidden="true"
            className={cn(
              'absolute top-0.5 right-0.5 grid min-w-4 place-items-center rounded-full px-1 text-[10px] font-semibold text-white tabular-nums',
              critical ? 'bg-danger-600' : 'bg-brand-600',
            )}
          >
            {unread > 99 ? '99+' : unread}
          </span>
        )}
      </button>

      {open && (
        <div
          role="menu"
          className="border-line bg-raised shadow-overlay absolute right-0 z-20 mt-1 w-80 rounded-md border"
        >
          <div className="border-line flex items-center justify-between border-b px-3 py-2">
            <p className="text-ink text-sm font-medium">
              Notifications{unread > 0 ? ` (${unread} unread)` : ''}
            </p>
            {unread > 0 && (
              <button
                type="button"
                onClick={() => markAll.mutate()}
                disabled={markAll.isPending}
                className="text-ink-soft hover:text-ink flex items-center gap-1 text-xs"
              >
                <CheckCheck className="size-3.5" aria-hidden="true" />
                Mark all read
              </button>
            )}
          </div>

          <div className="max-h-80 overflow-y-auto">
            {recent.isPending ? (
              <p className="text-ink-muted px-3 py-6 text-center text-sm">Loading…</p>
            ) : rows.length === 0 ? (
              <p className="text-ink-muted px-3 py-6 text-center text-sm">
                Nothing yet. Decisions, corrections and announcements appear here.
              </p>
            ) : (
              <ul className="divide-line divide-y">
                {rows.map((row) => {
                  const content = (
                    <>
                      <div className="flex items-start gap-2">
                        {row.read_at === null && (
                          <span
                            aria-label="Unread"
                            className="bg-brand-600 mt-1.5 size-1.5 shrink-0 rounded-full"
                          />
                        )}
                        <div className="min-w-0 flex-1">
                          <p className="text-ink truncate text-sm font-medium">{row.title}</p>
                          {row.body && (
                            <p className="text-ink-soft line-clamp-2 text-xs">{row.body}</p>
                          )}
                          <p className="text-ink-muted mt-0.5 text-[11px]">
                            {categoryLabel(row.category)} ·{' '}
                            <span title={new Date(row.created_at).toLocaleString()}>
                              {relativeTime(row.created_at)}
                            </span>
                          </p>
                        </div>
                      </div>
                    </>
                  );

                  return (
                    <li key={row.id}>
                      {row.link_path ? (
                        <Link
                          to={row.link_path}
                          onClick={() => setOpen(false)}
                          className="hover:bg-sunken block px-3 py-2 transition-colors"
                        >
                          {content}
                        </Link>
                      ) : (
                        <div className="px-3 py-2">{content}</div>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          <div className="border-line border-t px-3 py-2">
            <Link
              to="/notifications"
              onClick={() => setOpen(false)}
              className="text-brand-700 hover:text-brand-800 text-sm font-medium"
            >
              Open the notification centre
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}
