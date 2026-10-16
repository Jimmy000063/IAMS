/**
 * Custom date picker.
 *
 * Replaces `<input type="date">`, whose popover differs between Chrome,
 * Firefox and Safari and cannot be styled or annotated. This one is consistent,
 * fully keyboard operable, and — the reason it exists for this application —
 * can shade the work calendar's non-working days, so choosing a date for a
 * leave or regularization request shows which days are weekends and holidays.
 *
 * Keyboard model follows the ARIA grid pattern: arrows move by day, PageUp and
 * PageDown by month, Home and End to the ends of the week, Enter or Space to
 * select, Escape to dismiss.
 */

import { AlertCircle, Calendar, ChevronLeft, ChevronRight, X } from 'lucide-react';
import { useEffect, useId, useMemo, useRef, useState } from 'react';

import {
  MONTH_NAMES,
  WEEKDAY_LABELS,
  addDays,
  addMonths,
  clampIso,
  formatIsoFull,
  formatIsoLong,
  isValidIso,
  monthGrid,
  parseIso,
  todayIso,
  trimGrid,
  type IsoDate,
} from '@/lib/dates';
import { cn } from '@/lib/utils';

export type DayMarker = 'working' | 'weekly_off' | 'holiday';

export interface DatePickerProps {
  label: string;
  value: IsoDate | '';
  onChange: (value: IsoDate | '') => void;
  /** Day types from the work calendar, so non-working days are visibly shaded. */
  markers?: Record<IsoDate, DayMarker>;
  min?: IsoDate;
  max?: IsoDate;
  hint?: string;
  error?: string;
  required?: boolean;
  disabled?: boolean;
  clearable?: boolean;
  /** Called when the visible month changes, so the caller can fetch markers. */
  onMonthChange?: (year: number, month: number) => void;
}

export function DatePicker({
  label,
  value,
  onChange,
  markers,
  min,
  max,
  hint,
  error,
  required,
  disabled,
  clearable = true,
  onMonthChange,
}: DatePickerProps) {
  const baseId = useId();
  const inputId = `${baseId}-input`;
  const hintId = `${baseId}-hint`;
  const errorId = `${baseId}-error`;
  const gridId = `${baseId}-grid`;

  const containerRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);

  const today = todayIso();
  const anchor = parseIso(value || today) ?? parseIso(today)!;
  const [view, setView] = useState({ year: anchor.year, month: anchor.month });
  const [focused, setFocused] = useState<IsoDate>(value || today);

  const cells = useMemo(
    () => trimGrid(monthGrid(view.year, view.month, today)),
    [view.year, view.month, today],
  );

  /*
   * Years offered by the jump control. Bounded by `min` and `max` where they
   * are given, and otherwise a wide window around the year in view - wide
   * enough for a date of birth looking back and a contract end looking
   * forward, without becoming a thousand-row list.
   */
  const yearChoices = useMemo(() => {
    const current = parseIso(today)!.year;
    const lower = min ? parseIso(min)!.year : Math.min(current, view.year) - 70;
    const upper = max ? parseIso(max)!.year : Math.max(current, view.year) + 10;
    const years: number[] = [];
    for (let year = lower; year <= upper; year += 1) years.push(year);
    // The year in view must always be selectable, even if it sits outside the
    // bounds because the caller changed them after a value was chosen.
    if (!years.includes(view.year)) years.push(view.year);
    return years.sort((a, b) => b - a);
  }, [min, max, today, view.year]);

  // Close on an outside click or Escape. Registered only while open, so the
  // page is not carrying listeners for every closed picker on a form.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  const goToMonth = (year: number, month: number) => {
    setView({ year, month });
    onMonthChange?.(year, month);
  };

  const isDisabledDay = (iso: IsoDate) =>
    (min !== undefined && iso < min) || (max !== undefined && iso > max);

  const select = (iso: IsoDate) => {
    if (isDisabledDay(iso)) return;
    onChange(iso);
    setFocused(iso);
    setOpen(false);
  };

  const moveFocus = (next: IsoDate) => {
    const clamped = clampIso(next, min, max);
    setFocused(clamped);
    const parts = parseIso(clamped);
    if (parts && (parts.year !== view.year || parts.month !== view.month)) {
      goToMonth(parts.year, parts.month);
    }
  };

  const onGridKeyDown = (event: React.KeyboardEvent) => {
    const handlers: Record<string, () => void> = {
      ArrowLeft: () => moveFocus(addDays(focused, -1)),
      ArrowRight: () => moveFocus(addDays(focused, 1)),
      ArrowUp: () => moveFocus(addDays(focused, -7)),
      ArrowDown: () => moveFocus(addDays(focused, 7)),
      Home: () => {
        const parts = parseIso(focused);
        if (parts)
          moveFocus(
            addDays(
              focused,
              -(
                (new Date(Date.UTC(parts.year, parts.month - 1, parts.day)).getUTCDay() + 6) %
                7
              ),
            ),
          );
      },
      End: () => {
        const parts = parseIso(focused);
        if (parts) {
          const weekday =
            (new Date(Date.UTC(parts.year, parts.month - 1, parts.day)).getUTCDay() + 6) % 7;
          moveFocus(addDays(focused, 6 - weekday));
        }
      },
      PageUp: () => {
        const next = addMonths(view.year, view.month, -1);
        goToMonth(next.year, next.month);
        moveFocus(addDays(focused, -28));
      },
      PageDown: () => {
        const next = addMonths(view.year, view.month, 1);
        goToMonth(next.year, next.month);
        moveFocus(addDays(focused, 28));
      },
      Enter: () => select(focused),
      ' ': () => select(focused),
    };

    const handler = handlers[event.key];
    if (handler) {
      event.preventDefault();
      handler();
    }
  };

  const markerClass = (iso: IsoDate, inMonth: boolean): string => {
    const marker = markers?.[iso];
    if (!inMonth) return 'text-ink-muted/50';
    if (marker === 'holiday')
      return 'text-danger-700 bg-danger-50/60 dark:text-danger-400 dark:bg-danger-900/30';
    if (marker === 'weekly_off') return 'text-ink-muted bg-sunken';
    return 'text-ink';
  };

  return (
    <div className="space-y-1.5" ref={containerRef}>
      <label htmlFor={inputId} className="text-ink block text-sm font-medium">
        {label}
        {required && (
          <span className="text-danger-600 ml-0.5" aria-hidden="true">
            *
          </span>
        )}
        {required && <span className="sr-only"> (required)</span>}
      </label>

      <div className="relative">
        {/* One control, not three boxes in a row: the field draws the frame and
            the buttons sit inside it, so a date field reads as a single thing
            the way a native one does. */}
        <div
          className="field-group h-10 gap-1 pr-1 pl-3"
          data-invalid={error ? 'true' : undefined}
          data-disabled={disabled ? 'true' : undefined}
        >
          {/* Typing stays available: a keyboard user entering a date they
              already know should not have to navigate a grid to it. */}
          <input
            id={inputId}
            type="text"
            inputMode="numeric"
            placeholder="YYYY-MM-DD"
            value={value}
            disabled={disabled}
            required={required}
            aria-invalid={error ? true : undefined}
            aria-describedby={
              [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(' ') ||
              undefined
            }
            onChange={(event) => {
              const next = event.target.value.trim();
              onChange(next as IsoDate | '');
              if (isValidIso(next)) {
                const parts = parseIso(next)!;
                setFocused(next);
                goToMonth(parts.year, parts.month);
              }
            }}
            className="field-bare placeholder:text-ink-muted font-mono text-sm"
          />
          {clearable && value && !disabled && (
            <button
              type="button"
              onClick={() => onChange('')}
              aria-label="Clear date"
              className="text-ink-muted hover:bg-sunken hover:text-ink grid size-7 shrink-0 place-items-center rounded-md transition-colors"
            >
              <X className="size-3.5" aria-hidden="true" />
            </button>
          )}
          <button
            type="button"
            disabled={disabled}
            onClick={() => setOpen((current) => !current)}
            aria-expanded={open}
            aria-controls={gridId}
            aria-label={open ? 'Close calendar' : 'Open calendar'}
            className={cn(
              'grid size-8 shrink-0 place-items-center rounded-md transition-colors',
              'disabled:text-ink-muted disabled:cursor-not-allowed',
              open ? 'bg-brand-500 text-white' : 'text-ink-soft hover:bg-sunken hover:text-ink',
            )}
          >
            <Calendar className="size-4" aria-hidden="true" />
          </button>
        </div>

        {open && (
          <div
            className="border-line glass-strong shadow-overlay edge-light absolute left-0 z-30 mt-2 w-[19.5rem] rounded-[var(--radius-panel)] border p-3"
            style={{ animation: 'scale-in var(--duration-fast) var(--ease-out-soft)' }}
            role="dialog"
            aria-label={`Choose a date, ${MONTH_NAMES[view.month - 1]} ${view.year}`}
          >
            <div className="mb-2 flex items-center justify-between gap-2">
              <button
                type="button"
                onClick={() => {
                  const next = addMonths(view.year, view.month, -1);
                  goToMonth(next.year, next.month);
                }}
                aria-label="Previous month"
                className="text-ink-soft hover:bg-sunken hover:text-ink rounded p-1.5 transition-colors"
              >
                <ChevronLeft className="size-4" aria-hidden="true" />
              </button>
              <div className="flex items-center gap-1">
                <select
                  aria-label="Month"
                  value={view.month}
                  onChange={(event) => goToMonth(view.year, Number(event.target.value))}
                  className="text-ink hover:bg-sunken cursor-pointer appearance-none rounded-md bg-transparent px-1.5 py-1 text-sm font-semibold transition-colors"
                >
                  {MONTH_NAMES.map((name, index) => (
                    <option key={name} value={index + 1}>
                      {name}
                    </option>
                  ))}
                </select>
                <select
                  aria-label="Year"
                  value={view.year}
                  onChange={(event) => goToMonth(Number(event.target.value), view.month)}
                  className="text-ink hover:bg-sunken cursor-pointer appearance-none rounded-md bg-transparent px-1.5 py-1 text-sm font-semibold tabular-nums transition-colors"
                >
                  {yearChoices.map((year) => (
                    <option key={year} value={year}>
                      {year}
                    </option>
                  ))}
                </select>
                <span className="sr-only" aria-live="polite">
                  {MONTH_NAMES[view.month - 1]} {view.year}
                </span>
              </div>
              <button
                type="button"
                onClick={() => {
                  const next = addMonths(view.year, view.month, 1);
                  goToMonth(next.year, next.month);
                }}
                aria-label="Next month"
                className="text-ink-soft hover:bg-sunken hover:text-ink rounded p-1.5 transition-colors"
              >
                <ChevronRight className="size-4" aria-hidden="true" />
              </button>
            </div>

            <div
              id={gridId}
              role="grid"
              aria-label="Calendar"
              tabIndex={0}
              onKeyDown={onGridKeyDown}
              className="focus-visible:outline-brand-500 rounded"
            >
              <div role="row" className="mb-1 grid grid-cols-7 gap-0.5">
                {WEEKDAY_LABELS.map((day) => (
                  <div
                    key={day}
                    role="columnheader"
                    aria-label={day}
                    className="text-ink-muted py-1 text-center text-[11px] font-semibold uppercase"
                  >
                    {day.slice(0, 2)}
                  </div>
                ))}
              </div>

              <div className="grid grid-cols-7 gap-0.5">
                {cells.map((cell) => {
                  const selected = cell.iso === value;
                  const isFocused = cell.iso === focused;
                  const unavailable = isDisabledDay(cell.iso);
                  return (
                    <button
                      key={cell.iso}
                      type="button"
                      role="gridcell"
                      aria-selected={selected}
                      aria-current={cell.isToday ? 'date' : undefined}
                      aria-disabled={unavailable}
                      disabled={unavailable}
                      tabIndex={-1}
                      title={formatIsoFull(cell.iso)}
                      onClick={() => select(cell.iso)}
                      onFocus={() => setFocused(cell.iso)}
                      className={cn(
                        'relative h-9 rounded-lg text-sm transition-[background-color,color]',
                        markerClass(cell.iso, cell.inMonth),
                        selected &&
                          'from-brand-500 to-brand-600 bg-linear-to-b font-semibold text-white shadow-sm',
                        !selected &&
                          !unavailable &&
                          'hover:bg-brand-50 hover:text-brand-700 dark:hover:bg-brand-950/60 dark:hover:text-brand-200',
                        isFocused && !selected && 'ring-brand-400 ring-2 ring-inset',
                        unavailable && 'cursor-not-allowed opacity-40',
                      )}
                    >
                      {cell.day}
                      {cell.isToday && !selected && (
                        <span
                          aria-hidden="true"
                          className="bg-brand-500 absolute bottom-1 left-1/2 size-1 -translate-x-1/2 rounded-full"
                        />
                      )}
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="border-line mt-2 flex items-center justify-between border-t pt-2">
              <button
                type="button"
                onClick={() => select(clampIso(today, min, max))}
                className="text-brand-600 dark:text-brand-300 text-xs font-medium underline-offset-4 hover:underline"
              >
                Today
              </button>
              {markers && (
                <div className="text-ink-muted flex items-center gap-2.5 text-[11px]">
                  <span className="flex items-center gap-1">
                    <span className="bg-sunken border-line size-2.5 rounded-sm border" />
                    Weekly off
                  </span>
                  <span className="flex items-center gap-1">
                    <span className="bg-danger-50 border-danger-600/30 dark:bg-danger-900/40 size-2.5 rounded-sm border" />
                    Holiday
                  </span>
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {hint && !error && (
        <p id={hintId} className="text-ink-muted text-xs">
          {hint}
        </p>
      )}
      {error && (
        <p
          id={errorId}
          className="text-danger-600 dark:text-danger-400 flex items-start gap-1 text-xs font-medium"
        >
          <AlertCircle className="mt-px size-3.5 shrink-0" aria-hidden="true" />
          {error}
        </p>
      )}
      {value && !error && (
        <p className="text-ink-muted text-xs" aria-live="polite">
          {formatIsoLong(value)}
        </p>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Date and time                                                              */
/* -------------------------------------------------------------------------- */

/**
 * A date with a time of day, for scheduling.
 *
 * Composed from the picker above plus a time input rather than wrapping
 * `<input type="datetime-local">`, so the calendar half looks and behaves like
 * every other calendar in the application.
 *
 * The value is the same `YYYY-MM-DDTHH:mm` string the native control produces,
 * which keeps callers - and `new Date(value)` - unchanged. A date with no time
 * is not a half-filled value: it means midnight, and saying so in the hint is
 * clearer than refusing to accept it.
 */
export function DateTimeField({
  label,
  value,
  onChange,
  hint,
  error,
  required,
  disabled,
  min,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  hint?: string;
  error?: string;
  required?: boolean;
  disabled?: boolean;
  min?: IsoDate;
}) {
  const [datePart = '', timePart = ''] = value.split('T');

  const compose = (date: string, time: string) => {
    if (!date) return '';
    return `${date}T${time || '00:00'}`;
  };

  return (
    <div className="space-y-1.5">
      <div className="grid gap-2 sm:grid-cols-[1fr_auto]">
        <DatePicker
          label={label}
          value={datePart}
          onChange={(next) => onChange(compose(next, timePart))}
          {...(min !== undefined ? { min } : {})}
          {...(error !== undefined ? { error } : {})}
          {...(required !== undefined ? { required } : {})}
          {...(disabled !== undefined ? { disabled } : {})}
        />
        <div className="space-y-1.5">
          <label className="text-ink block text-sm font-medium" htmlFor={`${label}-time`}>
            Time
          </label>
          <input
            id={`${label}-time`}
            type="time"
            value={timePart}
            disabled={disabled || !datePart}
            onChange={(event) => onChange(compose(datePart, event.target.value))}
            className="field-control h-10 px-3 text-sm sm:w-32"
          />
        </div>
      </div>
      {hint && !error && <p className="text-ink-muted text-xs">{hint}</p>}
    </div>
  );
}
