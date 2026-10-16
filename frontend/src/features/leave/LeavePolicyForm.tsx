/** Create an effective-dated leave policy. */

import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';

import { Button } from '@/components/ui/Button';
import { DatePicker } from '@/components/ui/DatePicker';
import { CheckboxField, SelectField, TextAreaField, TextField } from '@/components/ui/Field';
import { Alert } from '@/components/ui/Feedback';
import { Modal } from '@/components/ui/Modal';
import { createPolicy, type LeaveType } from '@/features/leave/api';
import { todayIso, type IsoDate } from '@/lib/dates';
import { useToast } from '@/lib/toast';

const FREQUENCIES = [
  { value: 'annual', label: 'Annually (one credit a year)' },
  { value: 'monthly', label: 'Monthly' },
  { value: 'quarterly', label: 'Quarterly' },
  { value: 'half_yearly', label: 'Half-yearly' },
  { value: 'none', label: 'Does not accrue (granted or adjusted manually)' },
];

const ROUNDING = [
  { value: 'nearest_half', label: 'Nearest half day' },
  { value: 'nearest_full', label: 'Nearest whole day' },
  { value: 'down_half', label: 'Down to a half day' },
  { value: 'up_half', label: 'Up to a half day' },
  { value: 'none', label: 'No rounding (exact)' },
];

export function LeavePolicyForm({
  open,
  types,
  onClose,
  onSaved,
}: {
  open: boolean;
  types: LeaveType[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();

  const [typeId, setTypeId] = useState('');
  const [name, setName] = useState('');
  const [effectiveFrom, setEffectiveFrom] = useState<IsoDate>(todayIso());
  const [quota, setQuota] = useState('12');
  const [frequency, setFrequency] = useState('monthly');
  const [rounding, setRounding] = useState('nearest_half');
  const [prorateJoining, setProrateJoining] = useState(true);
  const [prorateExit, setProrateExit] = useState(true);
  const [fromConfirmation, setFromConfirmation] = useState(false);
  const [maxAccumulation, setMaxAccumulation] = useState('');
  const [carryCap, setCarryCap] = useState('');
  const [carryExpiry, setCarryExpiry] = useState('');
  const [encashment, setEncashment] = useState(false);
  const [encashmentMax, setEncashmentMax] = useState('');
  const [notes, setNotes] = useState('');
  const [legalReview, setLegalReview] = useState(false);

  const save = useMutation({
    mutationFn: () =>
      createPolicy({
        leave_type_id: Number(typeId),
        name: name.trim(),
        effective_from: effectiveFrom,
        annual_quota_days: quota || '0',
        accrual_frequency: frequency,
        accrual_rounding: rounding,
        prorate_on_joining: prorateJoining,
        prorate_on_exit: prorateExit,
        accrue_from_confirmation: fromConfirmation,
        max_accumulation_days: maxAccumulation.trim() || null,
        carry_forward_cap_days: carryCap.trim() || null,
        carry_forward_expires_after_months: carryExpiry.trim() ? Number(carryExpiry) : null,
        encashment_allowed: encashment,
        encashment_max_days: encashmentMax.trim() || null,
        notes: notes.trim() || null,
        requires_legal_review: legalReview,
      }),
    onSuccess: () => {
      toast.success('Policy created.');
      onSaved();
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  const fieldErrors =
    save.error && typeof save.error === 'object' && 'fieldErrors' in save.error
      ? (save.error as { fieldErrors: Record<string, string> }).fieldErrors
      : {};

  const selectedType = types.find((item) => String(item.id) === typeId);
  const accrues = frequency !== 'none';

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Add a leave policy"
      description="In force from the date you choose. To change an entitlement later, add another policy from that date and close this one."
      className="max-w-2xl"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={() => save.mutate()}
            disabled={save.isPending || typeId === '' || name.trim() === ''}
          >
            {save.isPending ? 'Saving…' : 'Create policy'}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <SelectField
            label="Leave type"
            value={typeId}
            onChange={(event) => setTypeId(event.target.value)}
            options={[
              { value: '', label: 'Select a leave type…' },
              ...types.map((item) => ({ value: String(item.id), label: item.name })),
            ]}
            error={fieldErrors['leave_type_id']}
            required
          />
          <TextField
            label="Policy name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            hint="Something you will recognise, e.g. Casual leave 2026-27."
            error={fieldErrors['name']}
            required
          />
        </div>

        {selectedType && !selectedType.deducts_balance && (
          <Alert variant="warning" title="This type does not deduct a balance">
            {selectedType.name} is configured with no quota, so a policy will have no effect on
            it. Change the type first if it should draw on an entitlement.
          </Alert>
        )}

        <DatePicker
          label="Effective from"
          value={effectiveFrom}
          onChange={setEffectiveFrom}
          hint="Applications on or after this date are governed by this policy."
          {...(fieldErrors['effective_from'] ? { error: fieldErrors['effective_from'] } : {})}
        />

        <fieldset className="border-line rounded-lg border p-4">
          <legend className="text-ink px-1 text-sm font-semibold">
            Entitlement and accrual
          </legend>
          <div className="grid gap-4 sm:grid-cols-2">
            <TextField
              label="Annual quota (days)"
              value={quota}
              onChange={(event) => setQuota(event.target.value)}
              error={fieldErrors['annual_quota_days']}
              required
            />
            <SelectField
              label="Credited"
              value={frequency}
              onChange={(event) => setFrequency(event.target.value)}
              options={FREQUENCIES}
              hint="Periods follow the leave year, not the calendar year."
              error={fieldErrors['accrual_frequency']}
            />
            {accrues && (
              <SelectField
                label="Rounding"
                value={rounding}
                onChange={(event) => setRounding(event.target.value)}
                options={ROUNDING}
                hint="Applied once per period, never to a running total."
              />
            )}
          </div>

          {accrues && (
            <div className="mt-3 space-y-3">
              <CheckboxField
                label="Prorate for a mid-period joiner"
                checked={prorateJoining}
                onChange={(event) => setProrateJoining(event.target.checked)}
                hint="Without this, someone joining on the 28th receives the same credit as someone who worked the whole month."
              />
              <CheckboxField
                label="Prorate for a mid-period leaver"
                checked={prorateExit}
                onChange={(event) => setProrateExit(event.target.checked)}
              />
              <CheckboxField
                label="Start accruing only after probation ends"
                checked={fromConfirmation}
                onChange={(event) => setFromConfirmation(event.target.checked)}
              />
            </div>
          )}

          {!accrues && (
            <p className="text-ink-soft mt-3 text-sm">
              Nothing will be credited automatically. Grant a balance with an opening balance or
              a manual adjustment, both of which record a reason.
            </p>
          )}
        </fieldset>

        <fieldset className="border-line rounded-lg border p-4">
          <legend className="text-ink px-1 text-sm font-semibold">
            Limits, carry-forward and encashment
          </legend>
          <div className="grid gap-4 sm:grid-cols-2">
            <TextField
              label="Maximum balance held (days)"
              value={maxAccumulation}
              onChange={(event) => setMaxAccumulation(event.target.value)}
              hint="Accrual stops at this ceiling rather than failing. Empty means no ceiling."
            />
            <TextField
              label="Carry-forward cap (days)"
              value={carryCap}
              onChange={(event) => setCarryCap(event.target.value)}
              hint="Maximum carried into the next leave year. 0 means nothing carries."
            />
            <TextField
              label="Carried days expire after (months)"
              type="number"
              min={1}
              value={carryExpiry}
              onChange={(event) => setCarryExpiry(event.target.value)}
              hint="Empty means carried days never expire."
            />
            {encashment && (
              <TextField
                label="Maximum encashable (days)"
                value={encashmentMax}
                onChange={(event) => setEncashmentMax(event.target.value)}
              />
            )}
          </div>
          <div className="mt-3">
            <CheckboxField
              label="Unused days may be encashed"
              checked={encashment}
              onChange={(event) => setEncashment(event.target.checked)}
              hint="Year-end encashment is processed in a later milestone; this records the policy."
            />
          </div>
        </fieldset>

        <TextAreaField
          label="Notes"
          value={notes}
          onChange={(event) => setNotes(event.target.value)}
          rows={2}
          hint="Why this entitlement is what it is. Useful a year from now."
        />

        <CheckboxField
          label="This quota comes from statute and needs legal review"
          checked={legalReview}
          onChange={(event) => setLegalReview(event.target.checked)}
          hint="Flagged to employees and in the configuration health report rather than presented as a settled figure."
        />
      </div>
    </Modal>
  );
}
