import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CircleCheck, CircleDashed, Lock, Pencil, Scale, Search } from 'lucide-react';
import { useMemo, useState } from 'react';

import { Button } from '@/components/ui/Button';
import { TextField } from '@/components/ui/Field';
import { Alert, EmptyState, ErrorPanel, SkeletonRows } from '@/components/ui/Feedback';
import { Badge, Card, CardBody, PageHeader, Td, TableShell, Th } from '@/components/ui/Surface';
import {
  fetchConfigurationHealth,
  fetchSettings,
  settingsKeys,
  updateSetting,
  type SettingRecord,
  type UpdateSettingPayload,
} from '@/features/settings/api';
import { SettingEditor } from '@/features/settings/SettingEditor';
import { useAuth } from '@/lib/auth';
import { useToast } from '@/lib/toast';
import { formatDateTime, formatSettingValue, humanise } from '@/lib/utils';

export function SettingsPage() {
  const { can } = useAuth();
  const toast = useToast();
  const queryClient = useQueryClient();

  const [category, setCategory] = useState<string>('all');
  const [search, setSearch] = useState('');
  const [editing, setEditing] = useState<SettingRecord | null>(null);

  const canManage = can('settings.manage');

  const settingsQuery = useQuery({
    queryKey: settingsKeys.list(),
    queryFn: () => fetchSettings(),
  });

  const healthQuery = useQuery({
    queryKey: settingsKeys.health(),
    queryFn: fetchConfigurationHealth,
  });

  const mutation = useMutation({
    mutationFn: ({ key, payload }: { key: string; payload: UpdateSettingPayload }) =>
      updateSetting(key, payload),
    onSuccess: async (updated) => {
      toast.success('Setting saved', `${updated.label} updated.`);
      setEditing(null);
      // Both the list and the health summary depend on this row.
      await queryClient.invalidateQueries({ queryKey: settingsKeys.all });
    },
    onError: (error) => toast.apiError(error, 'Could not save the setting'),
  });

  const categories = useMemo(() => {
    const unique = new Set((settingsQuery.data ?? []).map((row) => row.category));
    return ['all', ...[...unique].sort()];
  }, [settingsQuery.data]);

  const visible = useMemo(() => {
    const term = search.trim().toLowerCase();
    return (settingsQuery.data ?? []).filter((row) => {
      if (category !== 'all' && row.category !== category) return false;
      if (!term) return true;
      return (
        row.label.toLowerCase().includes(term) ||
        row.key.toLowerCase().includes(term) ||
        row.description.toLowerCase().includes(term)
      );
    });
  }, [settingsQuery.data, category, search]);

  const health = healthQuery.data;

  return (
    <div>
      <PageHeader
        title="Company settings"
        description="Business policy configuration. These values are stored as data, so changing one takes effect without a redeployment and is recorded in the change history."
      />

      {/* The honesty panel. Unconfirmed values are working defaults, not decisions. */}
      {health && health.unconfirmed > 0 && (
        <div className="mb-6">
          <Alert variant="warning" title={`${health.unconfirmed} settings await confirmation`}>
            <p>
              These are working defaults the application ships with, not decisions Ishvara Tech
              has made. Review and confirm each one.
            </p>
            {health.awaiting_legal_verification > 0 && (
              <p className="mt-2 font-medium">
                {health.awaiting_legal_verification} of them have statutory consequences and
                must be verified against an authoritative source. Payroll finalization is
                blocked until they are.
              </p>
            )}
          </Alert>
        </div>
      )}

      {health && (
        <div className="mb-6 grid gap-3 sm:grid-cols-3">
          <StatTile label="Total settings" value={health.total} />
          <StatTile label="Confirmed" value={health.confirmed} tone="success" />
          <StatTile
            label="Awaiting legal verification"
            value={health.awaiting_legal_verification}
            tone={health.awaiting_legal_verification > 0 ? 'danger' : 'success'}
          />
        </div>
      )}

      <Card>
        <CardBody className="space-y-4">
          <div className="flex flex-wrap items-end gap-3">
            <div className="min-w-56 flex-1">
              <TextField
                label="Search"
                placeholder="Search by name, key or description"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </div>
            <div
              className="flex flex-wrap gap-1.5"
              role="group"
              aria-label="Filter by category"
            >
              {categories.map((name) => (
                <button
                  key={name}
                  type="button"
                  onClick={() => setCategory(name)}
                  aria-pressed={category === name}
                  className={
                    category === name
                      ? 'bg-brand-600 rounded-full px-3 py-1.5 text-xs font-medium text-white'
                      : 'border-line-strong text-ink-soft hover:bg-sunken rounded-full border px-3 py-1.5 text-xs font-medium transition-colors'
                  }
                >
                  {name === 'all' ? 'All' : humanise(name)}
                </button>
              ))}
            </div>
          </div>

          {settingsQuery.isPending && <SkeletonRows rows={8} />}

          {settingsQuery.isError && (
            <ErrorPanel
              error={settingsQuery.error}
              onRetry={() => void settingsQuery.refetch()}
              title="Could not load settings"
            />
          )}

          {settingsQuery.isSuccess && visible.length === 0 && (
            <EmptyState
              icon={Search}
              title="No settings match your filters"
              description="Try a different search term or category."
              action={
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => {
                    setSearch('');
                    setCategory('all');
                  }}
                >
                  Clear filters
                </Button>
              }
            />
          )}

          {settingsQuery.isSuccess && visible.length > 0 && (
            <TableShell caption="Company business policy settings">
              <thead>
                <tr>
                  <Th>Setting</Th>
                  <Th>Value</Th>
                  <Th>Status</Th>
                  <Th className="hidden md:table-cell">Last changed</Th>
                  <Th className="text-right">
                    <span className="sr-only">Actions</span>
                  </Th>
                </tr>
              </thead>
              <tbody>
                {visible.map((setting) => (
                  <tr key={setting.key} className="hover:bg-sunken/60">
                    <Td>
                      <p className="text-ink font-medium">{setting.label}</p>
                      <p className="text-ink-muted mt-0.5 font-mono text-xs">{setting.key}</p>
                      <p className="text-ink-soft mt-1 max-w-md text-xs">
                        {setting.description}
                      </p>
                    </Td>
                    <Td className="numeric whitespace-pre-wrap">
                      {formatSettingValue(setting.value)}
                      {setting.constraints?.unit && (
                        <span className="text-ink-muted ml-1 text-xs">
                          {setting.constraints.unit}
                        </span>
                      )}
                    </Td>
                    <Td>
                      <div className="flex flex-col items-start gap-1">
                        {setting.is_confirmed ? (
                          <Badge tone="success">
                            <CircleCheck className="size-3" aria-hidden="true" />
                            Confirmed
                          </Badge>
                        ) : (
                          <Badge tone="warning">
                            <CircleDashed className="size-3" aria-hidden="true" />
                            Placeholder
                          </Badge>
                        )}
                        {setting.requires_legal_review && (
                          <Badge tone="danger">
                            <Scale className="size-3" aria-hidden="true" />
                            Needs verification
                          </Badge>
                        )}
                        {!setting.is_editable && (
                          <Badge tone="neutral">
                            <Lock className="size-3" aria-hidden="true" />
                            Fixed
                          </Badge>
                        )}
                      </div>
                    </Td>
                    <Td className="text-ink-soft hidden text-xs whitespace-nowrap md:table-cell">
                      {formatDateTime(setting.updated_at)}
                    </Td>
                    <Td className="text-right">
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={!canManage || !setting.is_editable}
                        title={
                          !setting.is_editable
                            ? 'This setting is fixed by the application.'
                            : !canManage
                              ? 'You do not have permission to change settings.'
                              : undefined
                        }
                        onClick={() => setEditing(setting)}
                        leadingIcon={<Pencil className="size-3.5" aria-hidden="true" />}
                      >
                        Edit
                      </Button>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </TableShell>
          )}
        </CardBody>
      </Card>

      <SettingEditor
        setting={editing}
        isSubmitting={mutation.isPending}
        onClose={() => setEditing(null)}
        onSubmit={(payload) => {
          if (editing) mutation.mutate({ key: editing.key, payload });
        }}
      />
    </div>
  );
}

function StatTile({
  label,
  value,
  tone = 'neutral',
}: {
  label: string;
  value: number;
  tone?: 'neutral' | 'success' | 'danger';
}) {
  const valueTone =
    tone === 'success'
      ? 'text-success-700'
      : tone === 'danger'
        ? 'text-danger-700'
        : 'text-ink';
  return (
    <Card>
      <CardBody className="py-4">
        <p className="text-ink-muted text-xs font-medium tracking-wide uppercase">{label}</p>
        <p className={`numeric mt-1 text-2xl font-semibold ${valueTone}`}>{value}</p>
      </CardBody>
    </Card>
  );
}
