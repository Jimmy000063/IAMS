/**
 * The adapter catalogue, with each adapter's maturity shown rather than hidden.
 *
 * This screen exists to answer one question honestly: "will this work with my
 * machine?" An operator looking for a named biometric product should find out
 * here, before configuring a device, that no vendor-specific adapter exists -
 * and what to use instead.
 */

import { useQuery } from '@tanstack/react-query';
import { CircleCheck, FlaskConical, Info, Wrench } from 'lucide-react';

import { Alert, ErrorPanel, SkeletonRows } from '@/components/ui/Feedback';
import { Badge, Card, CardBody, CardHeader, type BadgeTone } from '@/components/ui/Surface';
import { fetchAdapterCatalogue, type AdapterMaturity } from '@/features/devices/api';
import { deviceKeys } from '@/features/devices/deviceKeys';

const MATURITY: Record<
  AdapterMaturity,
  { label: string; tone: BadgeTone; icon: typeof CircleCheck }
> = {
  tested: { label: 'Tested', tone: 'success', icon: CircleCheck },
  generic: { label: 'Generic — unverified on hardware', tone: 'warning', icon: Wrench },
  development_only: { label: 'Development only', tone: 'neutral', icon: FlaskConical },
};

export function AdapterCatalogue() {
  const catalogue = useQuery({
    queryKey: deviceKeys.adapters(),
    queryFn: fetchAdapterCatalogue,
    staleTime: 10 * 60 * 1000,
  });

  if (catalogue.isPending) return <SkeletonRows rows={4} />;
  if (catalogue.isError) {
    return <ErrorPanel error={catalogue.error} onRetry={() => void catalogue.refetch()} />;
  }

  const { adapters, vendor_integration_notes, timestamp_formats } = catalogue.data;

  return (
    <div className="space-y-5">
      <Alert variant="info" title="What is actually integrated" icon={Info}>
        <ul className="list-inside list-disc space-y-1">
          {vendor_integration_notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      </Alert>

      <div className="grid gap-4 lg:grid-cols-2">
        {adapters.map((adapter) => {
          const maturity = MATURITY[adapter.maturity];
          const MaturityIcon = maturity.icon;

          return (
            <Card key={adapter.adapter_type}>
              <CardHeader
                title={
                  <span className="flex flex-wrap items-center gap-2">
                    {adapter.name}
                    <Badge tone={adapter.mode === 'push' ? 'brand' : 'accent'}>
                      {adapter.mode === 'push' ? 'Device pushes to us' : 'We poll the device'}
                    </Badge>
                  </span>
                }
                description={adapter.summary}
                action={
                  <Badge tone={maturity.tone}>
                    <MaturityIcon className="size-3.5" aria-hidden="true" />
                    {maturity.label}
                  </Badge>
                }
              />
              <CardBody className="space-y-4">
                <p className="text-ink-soft text-sm">{adapter.maturity_meaning}</p>

                {adapter.caveats.length > 0 && (
                  <ul className="text-ink-soft list-inside list-disc space-y-1 text-sm">
                    {adapter.caveats.map((caveat) => (
                      <li key={caveat}>{caveat}</li>
                    ))}
                  </ul>
                )}

                {(adapter.requires_api_secret || adapter.requires_webhook_secret) && (
                  <p className="text-ink-soft text-sm">
                    Requires{' '}
                    {adapter.requires_webhook_secret
                      ? 'a webhook signing secret'
                      : 'an API credential'}
                    , which is stored encrypted and never shown again.
                  </p>
                )}

                {adapter.config_fields.length > 0 && (
                  <details className="group">
                    <summary className="text-brand-700 cursor-pointer text-sm font-medium">
                      Settings this adapter accepts ({adapter.config_fields.length})
                    </summary>
                    <dl className="mt-3 space-y-2.5">
                      {adapter.config_fields.map((field) => (
                        <div key={field.key}>
                          <dt className="text-ink text-sm font-medium">
                            {field.label}
                            {field.required && (
                              <span className="text-danger-600 ml-1" aria-hidden="true">
                                *
                              </span>
                            )}
                            <code className="text-ink-muted ml-2 text-xs font-normal">
                              {field.key}
                            </code>
                          </dt>
                          {field.help_text && (
                            <dd className="text-ink-soft text-xs">{field.help_text}</dd>
                          )}
                          {field.example && (
                            <dd className="text-ink-muted text-xs">
                              Example: <code>{field.example}</code>
                            </dd>
                          )}
                        </div>
                      ))}
                    </dl>
                  </details>
                )}
              </CardBody>
            </Card>
          );
        })}
      </div>

      <Card>
        <CardHeader
          title="Timestamp formats"
          description="Chosen explicitly, never guessed: 03/04/2026 is a different day under a day-first and a month-first reading, and the wrong choice stays invisible until a month-end report."
        />
        <CardBody>
          <div className="flex flex-wrap gap-2">
            {timestamp_formats.map((format) => (
              <code
                key={format}
                className="bg-sunken text-ink-soft rounded px-2 py-1 font-mono text-xs"
              >
                {format}
              </code>
            ))}
          </div>
        </CardBody>
      </Card>
    </div>
  );
}
