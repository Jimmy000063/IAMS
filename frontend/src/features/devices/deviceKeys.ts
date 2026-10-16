/** Query keys for the device feature, kept in one place so invalidation is exact. */

import type { RawEventStatus } from '@/features/devices/api';

export const deviceKeys = {
  all: ['devices'] as const,
  list: (includeInactive = false) => ['devices', 'list', includeInactive] as const,
  one: (id: number) => ['devices', 'one', id] as const,
  adapters: () => ['devices', 'adapters'] as const,
  health: () => ['devices', 'health'] as const,
  mappings: (id: number) => ['devices', 'mappings', id] as const,
  syncRuns: (id: number) => ['devices', 'sync-runs', id] as const,
  events: (id: number, status?: RawEventStatus) =>
    ['devices', 'events', id, status ?? 'all'] as const,
  webhook: (id: number) => ['devices', 'webhook', id] as const,
};
