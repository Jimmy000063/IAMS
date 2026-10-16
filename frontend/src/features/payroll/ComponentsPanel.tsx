/**
 * Salary components, and a named employee's revisions.
 *
 * The wage-base flags are the point of the component table, so they are
 * columns rather than something buried in a detail view: whether a component
 * counts towards the provident-fund wage, the ESI wage or professional tax
 * changes somebody's deductions, and it is a legal question the company has to
 * answer per component.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Coins, Plus, ShieldQuestion, UserSearch } from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/Button';
import { DatePicker } from '@/components/ui/DatePicker';
import { CheckboxField, SelectField, TextField } from '@/components/ui/Field';
import { Alert, EmptyState, ErrorPanel, SkeletonRows } from '@/components/ui/Feedback';
import { Modal } from '@/components/ui/Modal';
import { Badge, Card, CardBody, CardHeader, TableShell, Td, Th } from '@/components/ui/Surface';
import {
  createComponent,
  createRevision,
  fetchComponents,
  fetchRevisions,
  type CalculationType,
  type ComponentType,
  type SalaryComponent,
} from '@/features/payroll/api';
import { useToast } from '@/lib/toast';

const COMPONENT_TYPES: { value: ComponentType; label: string }[] = [
  { value: 'earning', label: 'Earning' },
  { value: 'deduction', label: 'Deduction' },
  { value: 'employer_contribution', label: 'Employer contribution' },
  { value: 'reimbursement', label: 'Reimbursement' },
];

const CALCULATION_TYPES: { value: CalculationType; label: string }[] = [
  { value: 'fixed', label: 'A fixed amount' },
  { value: 'percent_of_gross', label: 'A percentage of gross' },
  { value: 'percent_of_component', label: 'A percentage of another component' },
  { value: 'balance', label: 'The balance of the gross' },
];

export function ComponentsPanel() {
  const toast = useToast();
  const queryClient = useQueryClient();

  const [composing, setComposing] = useState(false);
  const [employeeId, setEmployeeId] = useState('');
  const [revising, setRevising] = useState(false);

  const [code, setCode] = useState('');
  const [name, setName] = useState('');
  const [componentType, setComponentType] = useState<ComponentType>('earning');
  const [calculationType, setCalculationType] = useState<CalculationType>('percent_of_gross');
  const [percentage, setPercentage] = useState('');
  const [ofComponentId, setOfComponentId] = useState('');
  const [isPfWage, setIsPfWage] = useState(false);
  const [isEsiWage, setIsEsiWage] = useState(false);
  const [isPtWage, setIsPtWage] = useState(false);
  const [isTaxable, setIsTaxable] = useState(true);
  const [prorates, setProrates] = useState(true);
  const [needsReview, setNeedsReview] = useState(false);

  const [effectiveFrom, setEffectiveFrom] = useState('');
  const [gross, setGross] = useState('');
  const [reason, setReason] = useState('');
  const [amounts, setAmounts] = useState<Record<number, string>>({});

  const components = useQuery({
    queryKey: ['payroll', 'components'],
    queryFn: () => fetchComponents(true),
  });

  const revisions = useQuery({
    queryKey: ['payroll', 'revisions', employeeId],
    queryFn: () => fetchRevisions(Number(employeeId)),
    enabled: employeeId.trim() !== '',
    retry: false,
  });

  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ['payroll'] });

  const create = useMutation({
    mutationFn: () =>
      createComponent({
        code: code.trim(),
        name: name.trim(),
        component_type: componentType,
        calculation_type: calculationType,
        percentage: percentage.trim() || null,
        of_component_id: ofComponentId ? Number(ofComponentId) : null,
        is_pf_wage: isPfWage,
        is_esi_wage: isEsiWage,
        is_pt_wage: isPtWage,
        is_taxable: isTaxable,
        prorates_on_payable_days: prorates,
        requires_legal_review: needsReview,
      }),
    onSuccess: (component) => {
      invalidate();
      setComposing(false);
      setCode('');
      setName('');
      toast.success(`${component.code} created.`);
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  const revise = useMutation({
    mutationFn: () =>
      createRevision({
        employee_id: Number(employeeId),
        effective_from: effectiveFrom,
        gross_monthly: gross.trim(),
        revision_reason: reason.trim() || null,
        components: Object.entries(amounts)
          .filter(([, value]) => value.trim() !== '')
          .map(([id, value]) => ({
            component_id: Number(id),
            fixed_amount: value.trim(),
          })),
      }),
    onSuccess: () => {
      invalidate();
      setRevising(false);
      toast.success('Revision recorded. The previous one was closed the day before.');
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  const rows = components.data ?? [];
  const percentOf = rows.filter(
    (item: SalaryComponent) =>
      item.calculation_type !== 'percent_of_component' && item.calculation_type !== 'balance',
  );

  return (
    <div className="space-y-5">
      <Alert variant="info" title="No component is seeded" icon={ShieldQuestion}>
        "Basic" and "HRA" look universal, but their proportions are a company decision with tax
        consequences - and whether each one counts towards the provident-fund or ESI wage is a
        legal question only the company can answer. So the list starts empty.
      </Alert>

      <Card>
        <CardHeader
          title="Salary components"
          description="The lines that can appear on a payslip, and the wage bases each one counts towards."
          action={
            <Button onClick={() => setComposing(true)}>
              <Plus className="size-4" aria-hidden="true" />
              New component
            </Button>
          }
        />
        <CardBody>
          {components.isPending ? (
            <SkeletonRows rows={4} />
          ) : components.isError ? (
            <ErrorPanel error={components.error} onRetry={() => void components.refetch()} />
          ) : rows.length === 0 ? (
            <EmptyState
              icon={Coins}
              title="No salary components yet"
              description="Define the lines your payslips use. A structure built from percentages needs one balancing component so it adds up to the agreed gross exactly."
            />
          ) : (
            <TableShell caption="Salary components">
              <thead>
                <tr>
                  <Th>Code</Th>
                  <Th>Name</Th>
                  <Th>Type</Th>
                  <Th>Calculated as</Th>
                  <Th>PF wage</Th>
                  <Th>ESI wage</Th>
                  <Th>PT wage</Th>
                  <Th>Taxable</Th>
                  <Th>Prorates</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((item) => (
                  <tr key={item.id}>
                    <Td className="font-medium">{item.code}</Td>
                    <Td>
                      {item.name}
                      {item.requires_legal_review && (
                        <Badge tone="warning" className="ml-1">
                          needs review
                        </Badge>
                      )}
                    </Td>
                    <Td className="text-sm">{item.component_type.replace(/_/g, ' ')}</Td>
                    <Td className="text-ink-soft text-xs">
                      {item.calculation_type.replace(/_/g, ' ')}
                      {item.percentage ? ` (${item.percentage}%)` : ''}
                    </Td>
                    {[item.is_pf_wage, item.is_esi_wage, item.is_pt_wage, item.is_taxable].map(
                      (flag, index) => (
                        <Td key={index}>
                          <Badge tone={flag ? 'brand' : 'neutral'}>{flag ? 'yes' : 'no'}</Badge>
                        </Td>
                      ),
                    )}
                    <Td>
                      <Badge tone={item.prorates_on_payable_days ? 'brand' : 'neutral'}>
                        {item.prorates_on_payable_days ? 'yes' : 'no'}
                      </Badge>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </TableShell>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Salary revisions"
          description="Effective-dated and never edited, so a payslip from last March stays explainable."
          action={
            <div className="flex items-end gap-2">
              <div className="w-40">
                <TextField
                  label="Employee id"
                  value={employeeId}
                  onChange={(event) => setEmployeeId(event.target.value)}
                  hint="From the employee directory."
                />
              </div>
              <Button
                variant="secondary"
                onClick={() => setRevising(true)}
                disabled={employeeId.trim() === '' || rows.length === 0}
              >
                <Plus className="size-4" aria-hidden="true" />
                Record a revision
              </Button>
            </div>
          }
        />
        <CardBody>
          {employeeId.trim() === '' ? (
            <EmptyState
              icon={UserSearch}
              title="Choose an employee"
              description="Enter an employee id to see their salary history."
            />
          ) : revisions.isPending ? (
            <SkeletonRows rows={3} />
          ) : revisions.isError ? (
            <ErrorPanel error={revisions.error} onRetry={() => void revisions.refetch()} />
          ) : (revisions.data ?? []).length === 0 ? (
            <EmptyState
              title="No salary recorded"
              description="Payroll skips an employee with no revision in force, and says so in the run's result."
            />
          ) : (
            <TableShell caption="Salary revisions">
              <thead>
                <tr>
                  <Th>From</Th>
                  <Th>To</Th>
                  <Th className="text-right">Monthly gross</Th>
                  <Th>Components</Th>
                  <Th>Reason</Th>
                </tr>
              </thead>
              <tbody>
                {(revisions.data ?? []).map((item) => (
                  <tr key={item.id}>
                    <Td className="tabular-nums">{item.effective_from}</Td>
                    <Td className="tabular-nums">{item.effective_to ?? 'current'}</Td>
                    <Td className="text-right font-semibold tabular-nums">
                      {item.gross_monthly}
                    </Td>
                    <Td className="text-ink-soft text-xs">
                      {item.components
                        .map((part) => {
                          const component = rows.find((row) => row.id === part.component_id);
                          return `${component?.code ?? part.component_id}: ${part.monthly_amount}`;
                        })
                        .join(', ')}
                    </Td>
                    <Td className="text-ink-soft text-sm">{item.revision_reason}</Td>
                  </tr>
                ))}
              </tbody>
            </TableShell>
          )}
        </CardBody>
      </Card>

      <Modal
        open={composing}
        onClose={() => setComposing(false)}
        title="New salary component"
        description="The wage-base flags default to the conservative reading: taxable, and not part of any statutory wage."
        footer={
          <>
            <Button variant="ghost" onClick={() => setComposing(false)}>
              Cancel
            </Button>
            <Button
              onClick={() => create.mutate()}
              disabled={!code.trim() || !name.trim() || create.isPending}
            >
              Create
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <TextField
              label="Code"
              value={code}
              onChange={(event) => setCode(event.target.value.toUpperCase())}
              required
            />
            <TextField
              label="Name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              required
            />
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <SelectField
              label="Type"
              value={componentType}
              onChange={(event) => setComponentType(event.target.value as ComponentType)}
              options={COMPONENT_TYPES}
            />
            <SelectField
              label="Calculated as"
              value={calculationType}
              onChange={(event) => setCalculationType(event.target.value as CalculationType)}
              options={CALCULATION_TYPES}
            />
          </div>

          {(calculationType === 'percent_of_gross' ||
            calculationType === 'percent_of_component') && (
            <TextField
              label="Percentage"
              value={percentage}
              onChange={(event) => setPercentage(event.target.value)}
              hint="Greater than zero, up to 100."
            />
          )}

          {calculationType === 'percent_of_component' && (
            <SelectField
              label="Of which component"
              value={ofComponentId}
              onChange={(event) => setOfComponentId(event.target.value)}
              options={[
                { value: '', label: 'Select one' },
                ...percentOf.map((item) => ({ value: String(item.id), label: item.code })),
              ]}
              hint="A percentage of an already-derived component is refused, because it would make the payslip's arithmetic depend on evaluation order."
            />
          )}

          <div className="border-line rounded-lg border p-3">
            <p className="text-ink mb-2 text-sm font-medium">Wage bases</p>
            <p className="text-ink-soft mb-3 text-xs">
              Whether this component counts towards each statutory wage. Each is a legal
              question the company answers; none is inferred from the name.
            </p>
            <div className="grid gap-2 sm:grid-cols-2">
              <CheckboxField
                label="Counts towards the provident-fund wage"
                checked={isPfWage}
                onChange={(event) => setIsPfWage(event.target.checked)}
              />
              <CheckboxField
                label="Counts towards the ESI wage"
                checked={isEsiWage}
                onChange={(event) => setIsEsiWage(event.target.checked)}
              />
              <CheckboxField
                label="Counts towards professional tax"
                checked={isPtWage}
                onChange={(event) => setIsPtWage(event.target.checked)}
              />
              <CheckboxField
                label="Taxable"
                checked={isTaxable}
                onChange={(event) => setIsTaxable(event.target.checked)}
              />
            </div>
          </div>

          <CheckboxField
            label="Reduces when payable days are short"
            checked={prorates}
            onChange={(event) => setProrates(event.target.checked)}
          />
          <CheckboxField
            label="Flag for legal review"
            checked={needsReview}
            onChange={(event) => setNeedsReview(event.target.checked)}
          />
        </div>
      </Modal>

      <Modal
        open={revising}
        onClose={() => setRevising(false)}
        title="Record a salary revision"
        description="The components must add up to the gross, or the figure on the offer letter is not the figure being paid."
        footer={
          <>
            <Button variant="ghost" onClick={() => setRevising(false)}>
              Cancel
            </Button>
            <Button
              onClick={() => revise.mutate()}
              disabled={!effectiveFrom || !gross.trim() || revise.isPending}
            >
              Record
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <DatePicker
              label="In force from"
              value={effectiveFrom}
              onChange={setEffectiveFrom}
              required
              clearable={false}
            />
            <TextField
              label="Monthly gross"
              value={gross}
              onChange={(event) => setGross(event.target.value)}
              required
            />
          </div>
          <TextField
            label="Reason"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            hint="For example: on joining, annual increment, promotion."
          />
          <div>
            <p className="text-ink mb-2 text-sm font-medium">Component amounts</p>
            <div className="space-y-2">
              {rows
                .filter((item) => item.is_active)
                .map((item) => (
                  <TextField
                    key={item.id}
                    label={`${item.code} - ${item.name}`}
                    value={amounts[item.id] ?? ''}
                    onChange={(event) =>
                      setAmounts((previous) => ({ ...previous, [item.id]: event.target.value }))
                    }
                    hint={
                      item.calculation_type === 'balance'
                        ? 'Leave empty: this one takes the balance of the gross.'
                        : undefined
                    }
                  />
                ))}
            </div>
          </div>
        </div>
      </Modal>
    </div>
  );
}
