/**
 * Statutory rule sets: the figures payroll computes from, and their provenance.
 *
 * The honest part of this screen is the empty state. Nothing is seeded, because
 * a rate entered without a verified source produces a wrong deduction that
 * looks right - the employee loses money and nobody notices. So the company
 * enters each rate, cites where it came from, and verifies it; until then
 * payroll can be inspected but not finalized.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { BadgeCheck, CircleAlert, FileWarning, Plus, ShieldAlert } from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/Button';
import { DatePicker } from '@/components/ui/DatePicker';
import { SelectField, TextAreaField, TextField } from '@/components/ui/Field';
import { Alert, EmptyState, ErrorPanel, SkeletonRows } from '@/components/ui/Feedback';
import { Modal } from '@/components/ui/Modal';
import { Badge, Card, CardBody, CardHeader, TableShell, Td, Th } from '@/components/ui/Surface';
import {
  createRuleSet,
  domainLabel,
  fetchReadiness,
  fetchRuleSets,
  STATUS_TONES,
  supersedeRuleSet,
  verifyRuleSet,
  type StatutoryRuleSet,
} from '@/features/payroll/api';
import { useToast } from '@/lib/toast';

const DOMAINS = ['epf', 'eps', 'edli', 'esi', 'pt', 'tds', 'gratuity', 'lwf'] as const;

interface DraftParameter {
  key: string;
  label: string;
  value: string;
  isTable: boolean;
}

export function StatutoryPanel() {
  const toast = useToast();
  const queryClient = useQueryClient();

  const [composing, setComposing] = useState(false);
  const [verifying, setVerifying] = useState<StatutoryRuleSet | null>(null);
  const [source, setSource] = useState('');
  const [sourceUrl, setSourceUrl] = useState('');
  const [note, setNote] = useState('');

  const [domain, setDomain] = useState<string>('epf');
  const [versionLabel, setVersionLabel] = useState('');
  const [effectiveFrom, setEffectiveFrom] = useState('');
  const [stateCode, setStateCode] = useState('');
  const [parameters, setParameters] = useState<DraftParameter[]>([
    {
      key: 'employee_rate_percent',
      label: 'Employee contribution rate',
      value: '',
      isTable: false,
    },
  ]);

  const ruleSets = useQuery({
    queryKey: ['payroll', 'rule-sets'],
    queryFn: () => fetchRuleSets(),
  });

  const readiness = useQuery({
    queryKey: ['payroll', 'readiness'],
    queryFn: fetchReadiness,
  });

  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ['payroll'] });

  const create = useMutation({
    mutationFn: () =>
      createRuleSet({
        domain,
        version_label: versionLabel.trim(),
        effective_from: effectiveFrom,
        state_code: stateCode.trim() || null,
        parameters: parameters
          .filter((item) => item.key.trim() && item.value.trim())
          .map((item) =>
            item.isTable
              ? {
                  key: item.key.trim(),
                  label: item.label.trim() || item.key.trim(),
                  json_value: JSON.parse(item.value),
                }
              : {
                  key: item.key.trim(),
                  label: item.label.trim() || item.key.trim(),
                  numeric_value: item.value.trim(),
                },
          ),
      }),
    onSuccess: () => {
      invalidate();
      setComposing(false);
      setVersionLabel('');
      setEffectiveFrom('');
      toast.success(
        'Created as a draft. Verify it against its source before finalizing payroll.',
      );
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  const verify = useMutation({
    mutationFn: (ruleSet: StatutoryRuleSet) =>
      verifyRuleSet(ruleSet.id, {
        source_reference: source.trim(),
        source_url: sourceUrl.trim() || null,
        note: note.trim() || null,
      }),
    onSuccess: () => {
      invalidate();
      setVerifying(null);
      setSource('');
      setSourceUrl('');
      setNote('');
      toast.success('Verified. The parameters are now frozen; a change means a new version.');
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  const supersede = useMutation({
    mutationFn: (id: number) => supersedeRuleSet(id, 'Replaced by a newer version.'),
    onSuccess: () => {
      invalidate();
      toast.success('Superseded. The row is kept, because past payslips cite it.');
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  const rows = ruleSets.data ?? [];

  return (
    <div className="space-y-5">
      <Alert
        variant="warning"
        title="No statutory figure in this system was provided by its authors"
        icon={ShieldAlert}
      >
        Every rate, ceiling and slab is entered by the company and must cite the published
        source it came from. A figure that has not been verified still computes, so its effect
        can be seen, but payroll using it cannot be finalized.
      </Alert>

      {readiness.data && !readiness.data.ready && (
        <Card>
          <CardHeader
            title="Before payroll can be run"
            description="Listed plainly rather than hidden behind a green tick."
          />
          <CardBody>
            <ul className="space-y-2">
              {readiness.data.blockers.map((blocker) => (
                <li key={blocker} className="text-ink-soft flex gap-2 text-sm">
                  <CircleAlert
                    className="text-warning-700 mt-0.5 size-4 shrink-0"
                    aria-hidden="true"
                  />
                  {blocker}
                </li>
              ))}
            </ul>
          </CardBody>
        </Card>
      )}

      <Card>
        <CardHeader
          title="Statutory rule sets"
          description="Versioned and effective-dated, so a past payroll stays reproducible after a rate changes."
          action={
            <Button onClick={() => setComposing(true)}>
              <Plus className="size-4" aria-hidden="true" />
              New rule set
            </Button>
          }
        />
        <CardBody>
          {ruleSets.isPending ? (
            <SkeletonRows rows={4} />
          ) : ruleSets.isError ? (
            <ErrorPanel error={ruleSets.error} onRetry={() => void ruleSets.refetch()} />
          ) : rows.length === 0 ? (
            <EmptyState
              icon={FileWarning}
              title="No statutory rules are configured"
              description="Nothing is seeded. Enter the rates your company is registered for, cite the source, and verify them - payroll will refuse to finalize until you do."
            />
          ) : (
            <TableShell caption="Statutory rule sets">
              <thead>
                <tr>
                  <Th>Domain</Th>
                  <Th>Version</Th>
                  <Th>In force from</Th>
                  <Th>Status</Th>
                  <Th>Source</Th>
                  <Th className="text-right">Parameters</Th>
                  <Th>Actions</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((item) => (
                  <tr key={item.id}>
                    <Td>
                      <p className="font-medium">{domainLabel(item.domain)}</p>
                      {item.state_code && (
                        <p className="text-ink-soft text-xs">{item.state_code}</p>
                      )}
                    </Td>
                    <Td className="text-sm">{item.version_label}</Td>
                    <Td className="text-sm tabular-nums">{item.effective_from}</Td>
                    <Td>
                      <Badge tone={STATUS_TONES[item.status] ?? 'neutral'}>{item.status}</Badge>
                    </Td>
                    <Td className="text-ink-soft max-w-xs text-xs">
                      {item.source_reference ?? (
                        <span className="text-warning-700">not cited</span>
                      )}
                      {item.verified_at && (
                        <p className="text-ink-muted mt-0.5">
                          verified {new Date(item.verified_at).toLocaleDateString()}
                        </p>
                      )}
                    </Td>
                    <Td className="text-right tabular-nums">{item.parameters.length}</Td>
                    <Td>
                      <div className="flex flex-wrap gap-1">
                        {item.status === 'draft' && (
                          <Button variant="secondary" onClick={() => setVerifying(item)}>
                            <BadgeCheck className="size-3.5" aria-hidden="true" />
                            Verify
                          </Button>
                        )}
                        {item.status !== 'superseded' && (
                          <Button variant="ghost" onClick={() => supersede.mutate(item.id)}>
                            Supersede
                          </Button>
                        )}
                      </div>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </TableShell>
          )}
        </CardBody>
      </Card>

      {rows.length > 0 && (
        <Card>
          <CardHeader
            title="Parameters"
            description="The figures themselves, with the units they are expressed in."
          />
          <CardBody className="space-y-4">
            {rows.map((item) => (
              <div key={item.id} className="border-line rounded-lg border p-3">
                <p className="text-ink text-sm font-medium">
                  {domainLabel(item.domain)} - {item.version_label}{' '}
                  <Badge tone={STATUS_TONES[item.status] ?? 'neutral'}>{item.status}</Badge>
                </p>
                {item.parameters.length === 0 ? (
                  <p className="text-ink-muted mt-1 text-xs">
                    No parameters, so there is nothing to verify yet.
                  </p>
                ) : (
                  <ul className="mt-2 space-y-1">
                    {item.parameters.map((parameter) => (
                      <li key={parameter.key} className="text-ink-soft text-xs">
                        <span className="font-medium">{parameter.label}</span>{' '}
                        <code className="bg-sunken rounded px-1">{parameter.key}</code> ={' '}
                        <span className="tabular-nums">
                          {parameter.numeric_value ?? JSON.stringify(parameter.json_value)}
                        </span>
                        {parameter.unit ? ` ${parameter.unit}` : ''}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
          </CardBody>
        </Card>
      )}

      <Modal
        open={composing}
        onClose={() => setComposing(false)}
        title="New statutory rule set"
        description="Created as a draft. Verification is a separate act that records the source."
        footer={
          <>
            <Button variant="ghost" onClick={() => setComposing(false)}>
              Cancel
            </Button>
            <Button
              onClick={() => create.mutate()}
              disabled={!versionLabel.trim() || !effectiveFrom || create.isPending}
            >
              Save draft
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <SelectField
            label="Domain"
            value={domain}
            onChange={(event) => setDomain(event.target.value)}
            options={DOMAINS.map((item) => ({ value: item, label: domainLabel(item) }))}
          />
          <TextField
            label="Version label"
            value={versionLabel}
            onChange={(event) => setVersionLabel(event.target.value)}
            hint="How this version is referred to, for example 'FY 2026-27'."
            required
          />
          <div className="grid gap-4 sm:grid-cols-2">
            <DatePicker
              label="In force from"
              value={effectiveFrom}
              onChange={setEffectiveFrom}
              required
              clearable={false}
            />
            <TextField
              label="State"
              value={stateCode}
              onChange={(event) => setStateCode(event.target.value)}
              hint="Only for per-state domains such as professional tax."
            />
          </div>

          <div>
            <p className="text-ink mb-2 text-sm font-medium">Parameters</p>
            <div className="space-y-2">
              {parameters.map((parameter, index) => (
                <div key={index} className="grid gap-2 sm:grid-cols-3">
                  <TextField
                    label="Key"
                    value={parameter.key}
                    onChange={(event) =>
                      setParameters((previous) =>
                        previous.map((item, position) =>
                          position === index ? { ...item, key: event.target.value } : item,
                        ),
                      )
                    }
                  />
                  <TextField
                    label="Label"
                    value={parameter.label}
                    onChange={(event) =>
                      setParameters((previous) =>
                        previous.map((item, position) =>
                          position === index ? { ...item, label: event.target.value } : item,
                        ),
                      )
                    }
                  />
                  <TextField
                    label={parameter.isTable ? 'Table (JSON)' : 'Value'}
                    value={parameter.value}
                    onChange={(event) =>
                      setParameters((previous) =>
                        previous.map((item, position) =>
                          position === index ? { ...item, value: event.target.value } : item,
                        ),
                      )
                    }
                  />
                </div>
              ))}
            </div>
            <div className="mt-2 flex gap-2">
              <Button
                variant="ghost"
                onClick={() =>
                  setParameters((previous) => [
                    ...previous,
                    { key: '', label: '', value: '', isTable: false },
                  ])
                }
              >
                Add a rate
              </Button>
              <Button
                variant="ghost"
                onClick={() =>
                  setParameters((previous) => [
                    ...previous,
                    {
                      key: 'slabs',
                      label: 'Slabs',
                      value: '[{"up_to": 10000, "amount": 0}, {"up_to": null, "amount": 200}]',
                      isTable: true,
                    },
                  ])
                }
              >
                Add a slab table
              </Button>
            </div>
            <p className="text-ink-muted mt-2 text-xs">
              A slab table is a list of {'{'}up_to, amount{'}'} in ascending order, with the
              last entry's <code>up_to</code> set to null so the highest earners are not charged
              nothing.
            </p>
          </div>
        </div>
      </Modal>

      <Modal
        open={verifying !== null}
        onClose={() => setVerifying(null)}
        title="Verify this rule set"
        description="Record that these figures were checked against a published source. The parameters are frozen afterwards; a change means a new effective-dated version."
        footer={
          <>
            <Button variant="ghost" onClick={() => setVerifying(null)}>
              Cancel
            </Button>
            <Button
              onClick={() => verifying && verify.mutate(verifying)}
              disabled={source.trim().length < 5 || verify.isPending}
            >
              Verify
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <TextAreaField
            label="Published source"
            value={source}
            onChange={(event) => setSource(event.target.value)}
            rows={3}
            required
            hint="The document, circular or notification these figures were taken from."
          />
          <TextField
            label="Source link"
            value={sourceUrl}
            onChange={(event) => setSourceUrl(event.target.value)}
          />
          <TextField
            label="Note"
            value={note}
            onChange={(event) => setNote(event.target.value)}
            hint="Anything a later reader should know about this check."
          />
        </div>
      </Modal>
    </div>
  );
}
