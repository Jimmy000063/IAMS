/**
 * Month calendar for attendance.
 *
 * A month of attendance is a calendar-shaped thing, so it is rendered as one:
 * the shape of someone's month — a run of absences, a pattern of Friday
 * half-days — is visible at a glance in a grid and essentially invisible in a
 * flat table.
 *
 * The grid is the summary; selecting a day opens the detail, including the
 * individual sessions and any anomalies.
 */

import { AlertTriangle, ChevronLeft, ChevronRight, Clock, Pin } from 'lucide-react';
import { useMemo, useState } from 'react';

import { Badge } from '@/components/ui/Surface';
import {
  STATUS_STYLES,
  type AttendanceDay,
  type AttendanceStatus,
} from '@/features/attendance/api';
import {
  MONTH_NAMES,
  WEEKDAY_LABELS,
  addMonths,
  formatClock,
  formatIsoFull,
  formatMinutes,
  monthGrid,
  todayIso,
  trimGrid,
  type IsoDate,
} from '@/lib/dates';
import { cn } from '@/lib/utils';

export interface AttendanceCalendarProps {
  year: number;
  month: number;
  days: AttendanceDay[];
  onMonthChange: (year: number, month: number) => void;
  isLoading?: boolean | undefined;
  /** Shown on a selected day, for HR actions such as correcting it. */
  renderDayActions?: ((day: AttendanceDay) => React.ReactNode) | undefined;
  /** Offered on a day with no record — the regularization entry point. */
  onRequestCorrection?: (iso: IsoDate) => void;
}

export function AttendanceCalendar({
  year,
  month,
  days,
  onMonthChange,
  isLoading = false,
  renderDayActions,
  onRequestCorrection,
}: AttendanceCalendarProps) {
  const today = todayIso();
  const [selected, setSelected] = useState<IsoDate | null>(null);

  const byDate = useMemo(() => {
    const map = new Map<IsoDate, AttendanceDay>();
    for (const day of days) map.set(day.work_date, day);
    return map;
  }, [days]);

  const cells = useMemo(() => trimGrid(monthGrid(year, month, today)), [year, month, today]);

  // Only statuses actually present this month, so the legend stays honest
  // rather than listing every status the system can produce.
  const legend = useMemo(() => {
    const seen = new Set<AttendanceStatus>();
    for (const day of days) seen.add(day.status);
    return [...seen];
  }, [days]);

  const selectedDay = selected ? byDate.get(selected) : undefined;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => {
              const next = addMonths(year, month, -1);
              onMonthChange(next.year, next.month);
              setSelected(null);
            }}
            aria-label="Previous month"
            className="text-ink-soft hover:bg-sunken hover:text-ink rounded-md p-2 transition-colors"
          >
            <ChevronLeft className="size-4" aria-hidden="true" />
          </button>
          <h2
            className="text-ink min-w-44 text-center text-base font-semibold"
            aria-live="polite"
          >
            {MONTH_NAMES[month - 1]} {year}
          </h2>
          <button
            type="button"
            onClick={() => {
              const next = addMonths(year, month, 1);
              onMonthChange(next.year, next.month);
              setSelected(null);
            }}
            aria-label="Next month"
            className="text-ink-soft hover:bg-sunken hover:text-ink rounded-md p-2 transition-colors"
          >
            <ChevronRight className="size-4" aria-hidden="true" />
          </button>
        </div>

        {legend.length > 0 && (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs">
            {legend.map((status) => (
              <span key={status} className="text-ink-soft flex items-center gap-1.5">
                <span
                  className={cn('size-2.5 rounded-full', STATUS_STYLES[status].dot)}
                  aria-hidden="true"
                />
                {STATUS_STYLES[status].label}
              </span>
            ))}
          </div>
        )}
      </div>

      <div
        className={cn('transition-opacity', isLoading && 'pointer-events-none opacity-50')}
        aria-busy={isLoading}
      >
        <div className="mb-1 grid grid-cols-7 gap-1.5">
          {WEEKDAY_LABELS.map((label) => (
            <div
              key={label}
              className="text-ink-muted py-1 text-center text-[11px] font-semibold uppercase"
            >
              <span className="hidden sm:inline">{label}</span>
              <span className="sm:hidden">{label.slice(0, 1)}</span>
            </div>
          ))}
        </div>

        <div className="grid grid-cols-7 gap-1.5" role="grid" aria-label="Attendance calendar">
          {cells.map((cell) => {
            const day = byDate.get(cell.iso);
            const isSelected = selected === cell.iso;
            const style = day ? STATUS_STYLES[day.status] : null;
            const isFuture = cell.iso > today;

            return (
              <button
                key={cell.iso}
                type="button"
                role="gridcell"
                aria-selected={isSelected}
                aria-current={cell.isToday ? 'date' : undefined}
                aria-label={`${formatIsoFull(cell.iso)}${
                  day ? `: ${style?.label}, ${formatMinutes(day.net_minutes)}` : ': no record'
                }`}
                onClick={() => setSelected(isSelected ? null : cell.iso)}
                className={cn(
                  'relative flex min-h-20 flex-col items-start gap-0.5 rounded-lg border p-2 text-left transition-all',
                  'hover:ring-brand-400 hover:ring-2 hover:ring-inset',
                  !cell.inMonth && 'opacity-35',
                  style
                    ? style.cell
                    : isFuture
                      ? 'border-line border-dashed bg-transparent'
                      : 'border-line bg-transparent',
                  isSelected && 'ring-brand-600 ring-2 ring-inset',
                  cell.isToday && !isSelected && 'ring-brand-500/50 ring-2 ring-inset',
                )}
              >
                <span
                  className={cn(
                    'numeric text-sm font-semibold',
                    cell.isToday && 'text-brand-700',
                  )}
                >
                  {cell.day}
                </span>

                {day && (
                  <>
                    {day.net_minutes > 0 && (
                      <span className="numeric text-[11px] font-medium opacity-80">
                        {formatMinutes(day.net_minutes)}
                      </span>
                    )}
                    <div className="mt-auto flex flex-wrap items-center gap-1">
                      {day.is_late && (
                        <span
                          title={`Late by ${formatMinutes(day.late_minutes)}`}
                          className="bg-warning-600/20 text-warning-900 rounded px-1 text-[10px] font-semibold"
                        >
                          L
                        </span>
                      )}
                      {day.is_early_exit && (
                        <span
                          title={`Left ${formatMinutes(day.early_exit_minutes)} early`}
                          className="bg-warning-600/20 text-warning-900 rounded px-1 text-[10px] font-semibold"
                        >
                          E
                        </span>
                      )}
                      {day.overtime_minutes > 0 && (
                        <span
                          title={`Overtime ${formatMinutes(day.overtime_minutes)}`}
                          className="bg-brand-600/20 text-brand-900 rounded px-1 text-[10px] font-semibold"
                        >
                          OT
                        </span>
                      )}
                      {/* Wrapped: lucide icons take no `title`, and the
                          tooltip belongs on an element anyway. */}
                      {day.has_missing_punch && (
                        <span title="Missing punch" className="inline-flex">
                          <AlertTriangle
                            className="text-warning-700 size-3"
                            aria-hidden="true"
                          />
                        </span>
                      )}
                      {day.is_manual_override && (
                        <span title="Manually set by HR" className="inline-flex">
                          <Pin className="text-ink-muted size-3" aria-hidden="true" />
                        </span>
                      )}
                    </div>
                  </>
                )}

                {!day && !isFuture && cell.inMonth && (
                  <span className="text-ink-muted mt-auto text-[10px]">No record</span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      {selected && (
        <DayDetail
          iso={selected}
          day={selectedDay}
          onClose={() => setSelected(null)}
          actions={selectedDay && renderDayActions ? renderDayActions(selectedDay) : null}
          onRequestCorrection={
            onRequestCorrection && selected <= today
              ? () => onRequestCorrection(selected)
              : undefined
          }
        />
      )}
    </div>
  );
}

function DayDetail({
  iso,
  day,
  onClose,
  actions,
  onRequestCorrection,
}: {
  iso: IsoDate;
  day: AttendanceDay | undefined;
  onClose: () => void;
  actions: React.ReactNode;
  onRequestCorrection?: (() => void) | undefined;
}) {
  const style = day ? STATUS_STYLES[day.status] : null;

  return (
    <div className="border-line bg-raised rounded-lg border p-4" aria-live="polite">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-ink font-semibold">{formatIsoFull(iso)}</p>
          {day ? (
            <div className="mt-1.5 flex flex-wrap items-center gap-2">
              <span
                className={cn(
                  'inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium',
                  style?.cell,
                )}
              >
                <span className={cn('size-2 rounded-full', style?.dot)} aria-hidden="true" />
                {style?.label}
              </span>
              <Badge tone="neutral">
                Counts as {Number(day.payable_day_fraction).toFixed(2)} day
              </Badge>
              {day.is_manual_override && <Badge tone="warning">Set by HR</Badge>}
              {day.shift_id === null && <Badge tone="warning">No shift assigned</Badge>}
            </div>
          ) : (
            <p className="text-ink-soft mt-1 text-sm">No attendance record for this date.</p>
          )}
        </div>
        <div className="flex gap-2">
          {onRequestCorrection && (
            <button
              type="button"
              onClick={onRequestCorrection}
              className="text-brand-600 text-sm font-medium underline-offset-4 hover:underline"
            >
              Request a correction
            </button>
          )}
          <button
            type="button"
            onClick={onClose}
            className="text-ink-muted hover:text-ink text-sm"
          >
            Close
          </button>
        </div>
      </div>

      {day && (
        <div className="space-y-4">
          <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
            <Metric label="Worked" value={formatMinutes(day.net_minutes)} />
            <Metric label="Break" value={formatMinutes(day.break_minutes)} />
            <Metric label="First in" value={formatClock(day.first_in_at)} />
            <Metric label="Last out" value={formatClock(day.last_out_at)} />
            {day.late_minutes > 0 && (
              <Metric
                label="Late by"
                value={formatMinutes(day.late_minutes)}
                tone={day.is_late ? 'warning' : 'muted'}
                note={day.is_late ? undefined : 'within grace'}
              />
            )}
            {day.early_exit_minutes > 0 && (
              <Metric
                label="Left early by"
                value={formatMinutes(day.early_exit_minutes)}
                tone={day.is_early_exit ? 'warning' : 'muted'}
                note={day.is_early_exit ? undefined : 'within grace'}
              />
            )}
            {day.overtime_minutes > 0 && (
              <Metric
                label="Overtime"
                value={formatMinutes(day.overtime_minutes)}
                tone="brand"
              />
            )}
          </dl>

          {day.sessions.length > 0 && (
            <div>
              <p className="text-ink-muted mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase">
                <Clock className="size-3.5" aria-hidden="true" />
                Sessions
              </p>
              <ul className="space-y-1.5">
                {day.sessions.map((session) => (
                  <li
                    key={session.sequence}
                    className="bg-sunken flex items-center justify-between gap-3 rounded-md px-3 py-2 text-sm"
                  >
                    <span className="numeric">
                      {formatClock(session.started_at)} – {formatClock(session.ended_at)}
                    </span>
                    <span className="numeric text-ink-soft font-medium">
                      {formatMinutes(session.duration_minutes)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {day.anomalies && day.anomalies.length > 0 && (
            <div className="border-warning-600/40 bg-warning-50 text-warning-900 rounded-md border p-3">
              <p className="flex items-center gap-1.5 text-sm font-medium">
                <AlertTriangle className="size-4" aria-hidden="true" />
                Needs attention
              </p>
              <ul className="mt-1.5 list-inside list-disc space-y-0.5 text-xs">
                {day.anomalies.map((note, index) => (
                  <li key={index}>{note}</li>
                ))}
              </ul>
            </div>
          )}

          {day.is_manual_override && day.override_reason && (
            <div className="border-line bg-sunken rounded-md border p-3 text-sm">
              <p className="text-ink-muted text-xs font-semibold uppercase">
                HR override reason
              </p>
              <p className="text-ink mt-1">{day.override_reason}</p>
            </div>
          )}

          {actions && <div className="border-line border-t pt-3">{actions}</div>}

          <p className="text-ink-muted text-xs">
            Computed {formatClock(day.computed_at)} from {day.computed_from_event_count} punch
            {day.computed_from_event_count === 1 ? '' : 'es'}.
            {day.policy_snapshot ? ' Policy in force is recorded with this day.' : ''}
          </p>
        </div>
      )}
    </div>
  );
}

function Metric({
  label,
  value,
  tone = 'default',
  note,
}: {
  label: string;
  value: string;
  tone?: 'default' | 'warning' | 'brand' | 'muted' | undefined;
  note?: string | undefined;
}) {
  const valueTone =
    tone === 'warning'
      ? 'text-warning-700'
      : tone === 'brand'
        ? 'text-brand-700'
        : tone === 'muted'
          ? 'text-ink-muted'
          : 'text-ink';
  return (
    <div>
      <dt className="text-ink-muted text-xs font-medium tracking-wide uppercase">{label}</dt>
      <dd className={cn('numeric mt-0.5 font-semibold', valueTone)}>
        {value}
        {note && <span className="ml-1 text-xs font-normal opacity-70">({note})</span>}
      </dd>
    </div>
  );
}
