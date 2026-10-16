import { api } from '@/lib/api';
import type { IsoDate } from '@/lib/dates';

export type HolidayType = 'public' | 'company' | 'optional' | 'restricted';

export interface HolidayCalendar {
  id: number;
  code: string;
  name: string;
  year: number;
  work_location_id: number | null;
  employee_group_id: number | null;
  description: string | null;
  is_active: boolean;
  /** An unpublished calendar affects nothing. */
  is_published: boolean;
  published_at: string | null;
  holiday_count: number;
  electable_count: number;
}

export interface Holiday {
  id: number;
  calendar_id: number;
  holiday_date: IsoDate;
  name: string;
  holiday_type: string;
  is_paid: boolean;
  region: string | null;
  notes: string | null;
  is_active: boolean;
  requires_election: boolean;
}

export interface EmployeeHoliday {
  holiday_id: number;
  holiday_date: IsoDate;
  name: string;
  holiday_type: string;
  region: string | null;
  is_paid: boolean;
  requires_election: boolean;
  is_elected: boolean;
  /** Whether this is actually a non-working day for this employee. */
  applies: boolean;
}

export interface OptionalHolidayOption {
  holiday_id: number;
  holiday_date: IsoDate;
  name: string;
  holiday_type: string;
  region: string | null;
  is_elected: boolean;
  has_passed: boolean;
}

export interface Election {
  election_id: number;
  status: string;
  elected_at: string;
  holiday_id: number;
  holiday_date: IsoDate;
  name: string;
  holiday_type: string;
  year: number;
}

export interface ElectionLimits {
  year: number;
  max_elections: number;
  elected_count: number;
  remaining: number;
}

export interface ImportResult {
  calendar_code: string;
  rows_received: number;
  created: number;
  updated: number;
  rejected: number;
  already_imported: boolean;
  problems: { row: number | null; message: string }[];
}

export const fetchCalendars = (params: { year?: number; include_inactive?: boolean }) =>
  api.get<HolidayCalendar[]>('/holidays/calendars', { query: params });

export const createCalendar = (input: {
  code: string;
  name: string;
  year: number;
  work_location_id?: number | null;
  employee_group_id?: number | null;
  description?: string | null;
}) => api.post<HolidayCalendar>('/holidays/calendars', input);

export const fetchCalendarHolidays = (calendarId: number) =>
  api.get<Holiday[]>(`/holidays/calendars/${calendarId}/holidays`);

export const addHoliday = (
  calendarId: number,
  input: {
    holiday_date: IsoDate;
    name: string;
    holiday_type?: HolidayType;
    region?: string | null;
    is_paid?: boolean;
  },
) => api.post<Holiday>(`/holidays/calendars/${calendarId}/holidays`, input);

export const removeHoliday = (holidayId: number) =>
  api.delete<void>(`/holidays/holidays/${holidayId}`);

export const importHolidays = (
  calendarId: number,
  rows: { holiday_date: IsoDate; name: string; holiday_type?: HolidayType; region?: string }[],
) => api.post<ImportResult>(`/holidays/calendars/${calendarId}/import`, { rows });

export const publishCalendar = (calendarId: number) =>
  api.post<HolidayCalendar>(`/holidays/calendars/${calendarId}/publish`);

export const unpublishCalendar = (calendarId: number, reason: string) =>
  api.post<HolidayCalendar>(`/holidays/calendars/${calendarId}/unpublish`, { reason });

export const fetchMyHolidays = (params: { start?: IsoDate; end?: IsoDate }) =>
  api.get<EmployeeHoliday[]>('/holidays/me', { query: params });

export const fetchMyOptionalHolidays = (year?: number) =>
  api.get<OptionalHolidayOption[]>('/holidays/me/optional', { query: { year } });

export const fetchMyElectionLimits = (year?: number) =>
  api.get<ElectionLimits>('/holidays/me/optional/limits', { query: { year } });

export const fetchMyElections = (year?: number) =>
  api.get<Election[]>('/holidays/me/elections', { query: { year } });

export const electHoliday = (holidayId: number) =>
  api.post<Election>('/holidays/me/elections', { holiday_id: holidayId });

export const withdrawElection = (holidayId: number) =>
  api.delete<Election>(`/holidays/me/elections/${holidayId}`);

export const holidayKeys = {
  all: ['holidays'] as const,
  calendars: (params: Record<string, unknown>) => ['holidays', 'calendars', params] as const,
  calendarHolidays: (id: number) => ['holidays', 'calendar', id, 'holidays'] as const,
  mine: (params: Record<string, unknown>) => ['holidays', 'me', params] as const,
  myOptional: (year?: number) => ['holidays', 'me', 'optional', year ?? 'current'] as const,
  myLimits: (year?: number) => ['holidays', 'me', 'limits', year ?? 'current'] as const,
  myElections: (year?: number) => ['holidays', 'me', 'elections', year ?? 'all'] as const,
};
