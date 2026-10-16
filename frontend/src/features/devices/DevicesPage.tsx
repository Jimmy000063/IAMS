/**
 * Attendance device administration.
 *
 * Four tabs, each answering a question an operator actually has:
 *
 *  - **Health**: is anything broken or silent right now?
 *  - **Devices**: what is configured, and how do I feed it data?
 *  - **Reconciliation**: which punches arrived but could not be used?
 *  - **Adapters**: will this work with my machine? (answered honestly)
 *
 * Web punching does not depend on any of this. A device is an extra source of
 * punches, never a prerequisite.
 */

import { useState } from 'react';
import { HeartPulse, Inbox, Plug, Router } from 'lucide-react';

import { Alert } from '@/components/ui/Feedback';
import { PageHeader } from '@/components/ui/Surface';
import { AdapterCatalogue } from '@/features/devices/AdapterCatalogue';
import { DeviceHealthPanel } from '@/features/devices/DeviceHealthPanel';
import { DeviceList } from '@/features/devices/DeviceList';
import { ReconciliationQueue } from '@/features/devices/ReconciliationQueue';
import { useAuth } from '@/lib/auth';
import { cn } from '@/lib/utils';

type Tab = 'health' | 'devices' | 'reconciliation' | 'adapters';

const TABS: { id: Tab; label: string; icon: typeof Router; permission: string }[] = [
  { id: 'health', label: 'Health', icon: HeartPulse, permission: 'device.read' },
  { id: 'devices', label: 'Devices', icon: Router, permission: 'device.read' },
  { id: 'reconciliation', label: 'Reconciliation', icon: Inbox, permission: 'device.read' },
  { id: 'adapters', label: 'Adapters', icon: Plug, permission: 'device.read' },
];

export function DevicesPage() {
  const { can } = useAuth();
  const [tab, setTab] = useState<Tab>('health');
  const [focusedDeviceId, setFocusedDeviceId] = useState<number | null>(null);

  const visible = TABS.filter((item) => can(item.permission));

  return (
    <div>
      <PageHeader
        title="Attendance devices"
        description="Biometric readers, turnstiles and exported punch logs. Every source feeds the same ingestion pipeline, so a device punch is treated exactly like a web punch once it arrives."
      />

      <Alert variant="info" title="Web punching does not depend on any device">
        Employees can punch in from the browser whether or not a device is configured, reachable
        or even purchased. A device adds a second source of punches; it is never a prerequisite.
      </Alert>

      <div
        className="border-line mt-6 mb-5 flex gap-1 overflow-x-auto border-b"
        role="tablist"
        aria-label="Device sections"
      >
        {visible.map((item) => {
          const Icon = item.icon;
          const active = tab === item.id;
          return (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => setTab(item.id)}
              className={cn(
                'flex items-center gap-2 border-b-2 px-4 py-2.5 text-sm font-medium whitespace-nowrap transition',
                active
                  ? 'border-brand-600 text-brand-700'
                  : 'text-ink-soft hover:text-ink border-transparent',
              )}
            >
              <Icon className="size-4" aria-hidden="true" />
              {item.label}
            </button>
          );
        })}
      </div>

      {tab === 'health' && (
        <DeviceHealthPanel
          onSelect={(deviceId) => {
            setFocusedDeviceId(deviceId);
            setTab('devices');
          }}
        />
      )}
      {tab === 'devices' && (
        <DeviceList
          focusedDeviceId={focusedDeviceId}
          onFocusChange={setFocusedDeviceId}
          onReconcile={(deviceId) => {
            setFocusedDeviceId(deviceId);
            setTab('reconciliation');
          }}
        />
      )}
      {tab === 'reconciliation' && (
        <ReconciliationQueue
          focusedDeviceId={focusedDeviceId}
          onFocusChange={setFocusedDeviceId}
        />
      )}
      {tab === 'adapters' && <AdapterCatalogue />}
    </div>
  );
}
