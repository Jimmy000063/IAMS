/**
 * Calendar maths for the custom date picker and month grid.
 *
 * Deliberately string-first. Dates travel as `YYYY-MM-DD` and are compared as
 * strings, because a `Date` object carries a time and a timezone — and the
 * moment you build one from `YYYY-MM-DD` the browser interprets it as UTC
 * midnight, which in India renders as the *previous* day. Every calendar bug of
 * that shape comes from treating a calendar date as an instant.
 *
 * Nothing here is derived from the device clock for business purposes; the
 * server supplies "today" where it matters.
 */

export type IsoDate = string;

export const WEEKDAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'] as const;

export const MONTH_NAMES = [
  'January',
  'February',
  'March',
  'April',
  'May',
  'June',
  'July',
  'August',
  'September',
  'October',
  'November',
  'December',
] as const;

/** Today in the viewer's local zone, as `YYYY-MM-DD`. Display only. */
export function todayIso(): IsoDate {
  const now = new Date();
  return toIso(now.getFullYear(), now.getMonth() + 1, now.getDate());
}

export function toIso(year: number, month: number, day: number): IsoDate {
  return `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
}

export function parseIso(value: IsoDate): { year: number; month: number; day: number } | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (month < 1 || month > 12) return null;
  if (day < 1 || day > daysInMonth(year, month)) return null;
  return { year, month, day };
}

export function isValidIso(value: string): boolean {
  return parseIso(value) !== null;
}

export function daysInMonth(year: number, month: number): number {
  // Day 0 of the next month is the last day of this one.
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

/**
 * Monday-first weekday index (0 = Monday), matching Python's `date.weekday()`
 * and the backend's weekly-off pattern.
 */
export function weekdayIndex(value: IsoDate): number {
  const parts = parseIso(value);
  if (!parts) return 0;
  const sundayFirst = new Date(Date.UTC(parts.year, parts.month - 1, parts.day)).getUTCDay();
  return (sundayFirst + 6) % 7;
}

export function addDays(value: IsoDate, delta: number): IsoDate {
  const parts = parseIso(value);
  if (!parts) return value;
  const shifted = new Date(Date.UTC(parts.year, parts.month - 1, parts.day + delta));
  return toIso(shifted.getUTCFullYear(), shifted.getUTCMonth() + 1, shifted.getUTCDate());
}

export function addMonths(
  year: number,
  month: number,
  delta: number,
): { year: number; month: number } {
  const zeroBased = month - 1 + delta;
  return {
    year: year + Math.floor(zeroBased / 12),
    month: (((zeroBased % 12) + 12) % 12) + 1,
  };
}

export function startOfMonth(year: number, month: number): IsoDate {
  return toIso(year, month, 1);
}

export function endOfMonth(year: number, month: number): IsoDate {
  return toIso(year, month, daysInMonth(year, month));
}

export interface MonthCell {
  iso: IsoDate;
  day: number;
  inMonth: boolean;
  isToday: boolean;
  weekday: number;
}

/**
 * Six weeks of cells covering a month, padded with the adjacent months.
 *
 * Always 42 cells, so the grid does not change height between months — a
 * calendar that reflows as you page through it is unpleasant to use and makes
 * the surrounding layout jump.
 */
export function monthGrid(year: number, month: number, today = todayIso()): MonthCell[] {
  const first = startOfMonth(year, month);
  const leading = weekdayIndex(first);
  const gridStart = addDays(first, -leading);

  return Array.from({ length: 42 }, (_, index) => {
    const iso = addDays(gridStart, index);
    const parts = parseIso(iso)!;
    return {
      iso,
      day: parts.day,
      inMonth: parts.month === month && parts.year === year,
      isToday: iso === today,
      weekday: weekdayIndex(iso),
    };
  });
}

/** Trim a 42-cell grid to whole weeks that contain at least one in-month day. */
export function trimGrid(cells: MonthCell[]): MonthCell[] {
  const weeks: MonthCell[][] = [];
  for (let index = 0; index < cells.length; index += 7) {
    weeks.push(cells.slice(index, index + 7));
  }
  const used = weeks.filter((week) => week.some((cell) => cell.inMonth));
  return used.flat();
}

export function compareIso(a: IsoDate, b: IsoDate): number {
  // Lexicographic comparison is correct for zero-padded ISO dates.
  return a < b ? -1 : a > b ? 1 : 0;
}

export function isBefore(a: IsoDate, b: IsoDate): boolean {
  return a < b;
}

export function isAfter(a: IsoDate, b: IsoDate): boolean {
  return a > b;
}

export function clampIso(value: IsoDate, min?: IsoDate, max?: IsoDate): IsoDate {
  if (min && value < min) return min;
  if (max && value > max) return max;
  return value;
}

/** `3 Nov 2026`. Built from the parts, never via `new Date(iso)`. */
export function formatIsoLong(value: IsoDate): string {
  const parts = parseIso(value);
  if (!parts) return value;
  return `${parts.day} ${MONTH_NAMES[parts.month - 1]?.slice(0, 3)} ${parts.year}`;
}

/** `Tuesday, 3 November 2026`, for screen readers and tooltips. */
export function formatIsoFull(value: IsoDate): string {
  const parts = parseIso(value);
  if (!parts) return value;
  const weekdayNames = [
    'Monday',
    'Tuesday',
    'Wednesday',
    'Thursday',
    'Friday',
    'Saturday',
    'Sunday',
  ];
  return `${weekdayNames[weekdayIndex(value)]}, ${parts.day} ${
    MONTH_NAMES[parts.month - 1]
  } ${parts.year}`;
}

/** `7h 30m` from a minute count. `0m` rather than an em dash, so zero reads as a measurement. */
export function formatMinutes(total: number | null | undefined): string {
  if (total === null || total === undefined) return '—';
  if (total === 0) return '0m';
  const sign = total < 0 ? '-' : '';
  const absolute = Math.abs(total);
  const hours = Math.floor(absolute / 60);
  const minutes = absolute % 60;
  if (hours === 0) return `${sign}${minutes}m`;
  if (minutes === 0) return `${sign}${hours}h`;
  return `${sign}${hours}h ${minutes}m`;
}

/** `09:30` from an ISO instant, in the viewer's zone. Display only. */
export function formatClock(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date);
}

/** Trim `09:30:00` to `09:30` for display. */
export function trimSeconds(clock: string): string {
  return /^\d{2}:\d{2}:\d{2}$/.test(clock) ? clock.slice(0, 5) : clock;
}
