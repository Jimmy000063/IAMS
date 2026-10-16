import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

/** Merge conditional class names, with later Tailwind utilities winning. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/**
 * Format an API timestamp for display in the viewer's local timezone.
 *
 * The backend sends UTC. Rendering is the only place a local timezone is
 * applied; nothing is ever *calculated* from the browser's clock or zone.
 */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date);
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(date);
}

/** Render a setting value of any JSON type for display in a table cell. */
export function formatSettingValue(value: unknown): string {
  if (value === null || value === undefined) return 'Not set';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (Array.isArray(value)) return value.length ? value.join(', ') : 'Empty';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

/** Turn a settings category key into a readable heading. */
export function humanise(text: string): string {
  const spaced = text.replace(/[._-]/g, ' ');
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}
