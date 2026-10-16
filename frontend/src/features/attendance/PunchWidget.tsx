/**
 * Punch in / out control with a live elapsed-time display.
 *
 * The clock is anchored to the **server's** time, not the device's. On load the
 * component records the offset between `server_time` and the local clock and
 * applies it from then on, so a machine whose clock is wrong by an hour still
 * shows the elapsed time the server will record. The server generates the
 * actual punch timestamp regardless — this only keeps the display honest.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, LogIn, LogOut, Clock3 } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { Button } from '@/components/ui/Button';
import { Alert } from '@/components/ui/Feedback';
import { Badge, Card, CardBody } from '@/components/ui/Surface';
import {
  attendanceKeys,
  fetchPunchState,
  punch,
  type PunchState,
} from '@/features/attendance/api';
import { ApiError } from '@/lib/api';
import { formatClock, formatIsoLong, trimSeconds } from '@/lib/dates';
import { useToast } from '@/lib/toast';
import { cn } from '@/lib/utils';

/** Elapsed seconds since the open punch, measured against the server's clock. */
function useServerElapsed(state: PunchState | undefined): number | null {
  const offsetRef = useRef<number | null>(null);
  const [, forceTick] = useState(0);

  useEffect(() => {
    if (!state) return;
    // Positive when the device clock is behind the server.
    offsetRef.current = new Date(state.server_time).getTime() - Date.now();
  }, [state]);

  useEffect(() => {
    if (!state?.is_punched_in) return;
    const timer = window.setInterval(() => forceTick((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [state?.is_punched_in]);

  if (!state?.is_punched_in) return null;

  const lastIn = [...state.events]
    .filter((event) => event.event_type === 'in')
    .sort((a, b) => (a.occurred_at < b.occurred_at ? 1 : -1))[0];
  if (!lastIn) return null;

  const serverNow = Date.now() + (offsetRef.current ?? 0);
  return Math.max(Math.floor((serverNow - new Date(lastIn.occurred_at).getTime()) / 1000), 0);
}

function formatElapsed(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = seconds % 60;
  return [hours, minutes, secs].map((part) => String(part).padStart(2, '0')).join(':');
}

export function PunchWidget({ compact = false }: { compact?: boolean }) {
  const toast = useToast();
  const queryClient = useQueryClient();

  const stateQuery = useQuery({
    queryKey: attendanceKeys.state(),
    queryFn: fetchPunchState,
    // The open session's elapsed time is interpolated locally; this refetch
    // only reconciles with the server, e.g. after a punch from another device.
    refetchInterval: 60_000,
    retry: false,
  });

  const elapsed = useServerElapsed(stateQuery.data);

  const mutation = useMutation({
    mutationFn: () => punch('auto'),
    onSuccess: async (result) => {
      if (result.recorded) {
        toast.success(
          result.event_type === 'in' ? 'Punched in' : 'Punched out',
          `Recorded for ${formatIsoLong(result.work_date)}.`,
        );
      } else {
        toast.show({ variant: 'info', title: result.message });
      }
      await queryClient.invalidateQueries({ queryKey: attendanceKeys.all });
      await queryClient.invalidateQueries({ queryKey: ['dashboard'] });
    },
    onError: (error) => toast.apiError(error, 'Could not record the punch'),
  });

  if (stateQuery.isError) {
    const unlinked = stateQuery.error instanceof ApiError && stateQuery.error.status === 409;
    return (
      <Card>
        <CardBody>
          <Alert variant={unlinked ? 'info' : 'danger'} title="Punching unavailable">
            {stateQuery.error instanceof ApiError
              ? stateQuery.error.message
              : 'Could not load your punch state.'}
          </Alert>
        </CardBody>
      </Card>
    );
  }

  const state = stateQuery.data;
  const isIn = state?.is_punched_in ?? false;

  return (
    <Card className={cn(isIn && 'ring-success-600/30 ring-2')}>
      <CardBody className="space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-ink-muted text-xs font-semibold tracking-wide uppercase">
              {state ? formatIsoLong(state.work_date) : 'Today'}
            </p>
            <div className="mt-1 flex items-center gap-2">
              <span
                className={cn(
                  'size-2.5 rounded-full',
                  isIn ? 'bg-success-600 animate-pulse' : 'bg-ink-muted',
                )}
                aria-hidden="true"
              />
              <p className="text-ink font-semibold">{isIn ? 'Punched in' : 'Not punched in'}</p>
            </div>
          </div>

          {state && (
            <div className="text-right">
              {state.has_shift_assigned ? (
                <>
                  <p className="text-ink-muted text-xs">Shift {state.shift_code}</p>
                  <p className="numeric text-ink text-sm font-medium">
                    {trimSeconds(state.shift_start)} – {trimSeconds(state.shift_end)}
                  </p>
                </>
              ) : (
                <Badge tone="warning">No shift assigned</Badge>
              )}
              <p className="text-ink-muted mt-0.5 text-[11px]">{state.timezone}</p>
            </div>
          )}
        </div>

        {isIn && elapsed !== null && (
          <div className="bg-success-50 border-success-600/30 rounded-lg border px-4 py-3 text-center">
            <p className="text-success-900/70 text-xs font-semibold tracking-wide uppercase">
              In this session
            </p>
            <p
              className="numeric text-success-900 mt-0.5 text-3xl font-bold tabular-nums"
              aria-live="off"
            >
              {formatElapsed(elapsed)}
            </p>
          </div>
        )}

        <Button
          fullWidth
          size="lg"
          variant={isIn ? 'danger' : 'primary'}
          isLoading={mutation.isPending || stateQuery.isPending}
          leadingIcon={
            isIn ? (
              <LogOut className="size-4" aria-hidden="true" />
            ) : (
              <LogIn className="size-4" aria-hidden="true" />
            )
          }
          onClick={() => mutation.mutate()}
        >
          {isIn ? 'Punch out' : 'Punch in'}
        </Button>

        {state && (
          <div className="grid grid-cols-2 gap-3 text-sm">
            <div>
              <p className="text-ink-muted text-xs font-medium tracking-wide uppercase">
                Worked today
              </p>
              <p className="numeric text-ink mt-0.5 font-semibold">
                {Math.floor(state.worked_minutes_so_far / 60)}h{' '}
                {state.worked_minutes_so_far % 60}m
              </p>
            </div>
            <div>
              <p className="text-ink-muted text-xs font-medium tracking-wide uppercase">
                Sessions
              </p>
              <p className="numeric text-ink mt-0.5 font-semibold">{state.session_count}</p>
            </div>
          </div>
        )}

        {state && state.events.length > 0 && !compact && (
          <div>
            <p className="text-ink-muted mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase">
              <Clock3 className="size-3.5" aria-hidden="true" />
              Today&apos;s punches
            </p>
            {/* A vertical timeline reads more naturally than a list for a
                sequence of events within one day. */}
            <ol className="border-line space-y-2 border-l pl-4">
              {state.events.map((event, index) => (
                <li key={index} className="relative text-sm">
                  <span
                    aria-hidden="true"
                    className={cn(
                      'absolute top-1.5 -left-[21px] size-2.5 rounded-full ring-2',
                      event.event_type === 'in'
                        ? 'bg-success-600 ring-success-50'
                        : 'bg-ink-muted ring-sunken',
                    )}
                  />
                  <span className="text-ink font-medium">
                    {event.event_type === 'in' ? 'In' : 'Out'}
                  </span>
                  <span className="numeric text-ink-soft ml-2">
                    {formatClock(event.occurred_at)}
                  </span>
                </li>
              ))}
            </ol>
          </div>
        )}

        {state && !state.has_shift_assigned && (
          <Alert variant="warning" title="No shift assigned" icon={AlertTriangle}>
            Company default timings are being used to assess your attendance. Ask HR to assign
            you a shift so it is measured against the right hours.
          </Alert>
        )}
      </CardBody>
    </Card>
  );
}
