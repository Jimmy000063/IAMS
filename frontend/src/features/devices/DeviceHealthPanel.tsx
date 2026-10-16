/**
 * Device health.
 *
 * The important row here is "gone quiet". A device that stops reporting raises
 * no error of its own - it just leaves a slowly growing hole in attendance that
 * surfaces at month end, when it is expensive to fix. Naming those devices
 * while the gap is still small is the whole purpose of this panel.
 */

import { useQuery } from '@tanstack/react-query';
import { Activity, BellRing, CircleHelp, Inbox, WifiOff } from 'lucide-react';

import { Alert, ErrorPanel, SkeletonRows } from '@/components/ui/Feedback';
import { Badge, Card, CardBody, CardHeader } from '@/components/ui/Surface';
import { fetchDeviceHealth } from '@/features/devices/api';
import { deviceKeys } from '@/features/devices/deviceKeys';
import { formatDateTime } from '@/lib/utils';

export function DeviceHealthPanel({ onSelect }: { onSelect?: (deviceId: number) => void }) {
  const health = useQuery({
    queryKey: deviceKeys.health(),
    queryFn: fetchDeviceHealth,
    refetchInterval: 60_000,
  });

  if (health.isPending) return <SkeletonRows rows={3} />;
  if (health.isError) {
    return <ErrorPanel error={health.error} onRetry={() => void health.refetch()} />;
  }

  const data = health.data;
  const attention = data.events_needing_attention;
  const quietHours = Math.round((data.quiet_after_minutes / 60) * 10) / 10;

  return (
    <div className="space-y-5">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="Devices configured"
          value={`${data.devices_active} active`}
          detail={`${data.devices_total} in total`}
          icon={Activity}
        />
        <Stat
          label="Gone quiet"
          value={String(data.quiet_devices.length)}
          detail={`No report for over ${quietHours}h`}
          icon={WifiOff}
          tone={data.quiet_devices.length > 0 ? 'danger' : 'success'}
        />
        <Stat
          label="Never reported"
          value={String(data.never_reported.length)}
          detail="Configured but unproven"
          icon={CircleHelp}
          tone={data.never_reported.length > 0 ? 'warning' : 'success'}
        />
        <Stat
          label="Events needing attention"
          value={String(data.events_needing_attention_total)}
          detail={
            Object.entries(attention)
              .map(([status, count]) => `${count} ${status}`)
              .join(', ') || 'Nothing queued'
          }
          icon={Inbox}
          tone={data.events_needing_attention_total > 0 ? 'warning' : 'success'}
        />
      </div>

      {data.quiet_devices.length > 0 && (
        <Alert variant="danger" title="These devices have stopped reporting" icon={BellRing}>
          <p>
            A silent device produces no error. It produces missing attendance, which is only
            noticed when someone checks a month-end figure.
          </p>
          <ul className="mt-2 space-y-1">
            {data.quiet_devices.map((device) => (
              <li key={device.id}>
                <button
                  type="button"
                  onClick={() => onSelect?.(device.id)}
                  className="font-medium underline underline-offset-2"
                >
                  {device.code}
                </button>{' '}
                — silent for {device.silent_for_hours}h, last seen{' '}
                {formatDateTime(device.last_seen_at)}
              </li>
            ))}
          </ul>
        </Alert>
      )}

      {data.failing_devices.length > 0 && (
        <Card>
          <CardHeader
            title="Devices failing to synchronise"
            description="Consecutive failures since the last successful run."
          />
          <CardBody>
            <ul className="space-y-2 text-sm">
              {data.failing_devices.map((device) => (
                <li key={device.id} className="flex items-center gap-3">
                  <Badge tone="danger">{device.consecutive_failures} failed</Badge>
                  <button
                    type="button"
                    onClick={() => onSelect?.(device.id)}
                    className="text-brand-700 font-medium underline underline-offset-2"
                  >
                    {device.code}
                  </button>
                  <span className="text-ink-soft">last run {device.last_sync_status}</span>
                </li>
              ))}
            </ul>
          </CardBody>
        </Card>
      )}

      {data.never_reported.length > 0 && (
        <Card>
          <CardHeader
            title="Configured but never heard from"
            description="Not the same as gone quiet: these have never delivered an event, so the integration is unproven rather than broken."
          />
          <CardBody>
            <ul className="space-y-1 text-sm">
              {data.never_reported.map((device) => (
                <li key={device.id}>
                  <button
                    type="button"
                    onClick={() => onSelect?.(device.id)}
                    className="text-brand-700 font-medium underline underline-offset-2"
                  >
                    {device.code}
                  </button>{' '}
                  <span className="text-ink-soft">{device.name}</span>
                </li>
              ))}
            </ul>
          </CardBody>
        </Card>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  detail,
  icon: Icon,
  tone = 'neutral',
}: {
  label: string;
  value: string;
  detail: string;
  icon: typeof Activity;
  tone?: 'neutral' | 'success' | 'warning' | 'danger';
}) {
  const toneClass = {
    neutral: 'text-ink-soft',
    success: 'text-success-700',
    warning: 'text-warning-700',
    danger: 'text-danger-700',
  }[tone];

  return (
    <Card>
      <CardBody className="flex items-start gap-3">
        <Icon className={`mt-0.5 size-5 shrink-0 ${toneClass}`} aria-hidden="true" />
        <div className="min-w-0">
          <p className="text-ink-soft text-xs font-medium tracking-wide uppercase">{label}</p>
          <p className="text-ink mt-1 text-xl font-semibold">{value}</p>
          <p className="text-ink-muted mt-0.5 text-xs">{detail}</p>
        </div>
      </CardBody>
    </Card>
  );
}
