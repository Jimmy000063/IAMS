import { api } from '@/lib/api';

export interface SettingRecord {
  key: string;
  category: string;
  label: string;
  description: string;
  value: unknown;
  default_value: unknown;
  value_type: 'string' | 'integer' | 'decimal' | 'boolean' | 'time' | 'date' | 'json' | 'enum';
  constraints: {
    min?: number;
    max?: number;
    unit?: string;
    choices?: string[];
    item_choices?: string[];
    min_items?: number;
    max_items?: number;
  } | null;
  is_editable: boolean;
  is_confirmed: boolean;
  requires_legal_review: boolean;
  source_reference: string | null;
  updated_at: string;
}

export interface ConfigurationHealth {
  total: number;
  confirmed: number;
  unconfirmed: number;
  awaiting_legal_verification: number;
  unconfirmed_keys: string[];
  legal_review_keys: string[];
}

export interface SettingHistoryEntry {
  setting_key: string;
  old_value: unknown;
  new_value: unknown;
  changed_at: string;
  changed_by_user_id: number | null;
  change_reason: string | null;
}

export interface UpdateSettingPayload {
  value: unknown;
  reason?: string;
  confirm?: boolean;
  source_reference?: string;
}

export const settingsKeys = {
  all: ['settings'] as const,
  list: (category?: string) => ['settings', 'list', category ?? 'all'] as const,
  health: () => ['settings', 'health'] as const,
  history: (key: string) => ['settings', 'history', key] as const,
};

export function fetchSettings(category?: string): Promise<SettingRecord[]> {
  return api.get<SettingRecord[]>('/settings', category ? { query: { category } } : {});
}

export function fetchConfigurationHealth(): Promise<ConfigurationHealth> {
  return api.get<ConfigurationHealth>('/settings/health');
}

export function fetchSettingHistory(key: string): Promise<SettingHistoryEntry[]> {
  return api.get<SettingHistoryEntry[]>(`/settings/${encodeURIComponent(key)}/history`);
}

export function updateSetting(
  key: string,
  payload: UpdateSettingPayload,
): Promise<SettingRecord> {
  return api.put<SettingRecord>(`/settings/${encodeURIComponent(key)}`, payload);
}
