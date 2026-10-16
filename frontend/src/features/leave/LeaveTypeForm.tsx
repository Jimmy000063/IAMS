/** Create or edit a leave type. */

import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';

import { Button } from '@/components/ui/Button';
import { CheckboxField, SelectField, TextAreaField, TextField } from '@/components/ui/Field';
import { Alert } from '@/components/ui/Feedback';
import { Modal } from '@/components/ui/Modal';
import { createLeaveType, updateLeaveType, type LeaveType } from '@/features/leave/api';
import { useToast } from '@/lib/toast';

interface FormState {
  code: string;
  name: string;
  description: string;
  is_paid: boolean;
  deducts_balance: boolean;
  allows_half_day: boolean;
  counts_non_working_days: boolean;
  requires_reason: boolean;
  attachment_required_above_days: string;
  min_notice_days: string;
  max_consecutive_days: string;
  min_service_days: string;
  gender_restriction: 'any' | 'female' | 'male';
  requires_legal_review: boolean;
  colour: string;
  is_active: boolean;
}

const EMPTY: FormState = {
  code: '',
  name: '',
  description: '',
  is_paid: true,
  deducts_balance: true,
  allows_half_day: true,
  counts_non_working_days: false,
  requires_reason: true,
  attachment_required_above_days: '',
  min_notice_days: '0',
  max_consecutive_days: '',
  min_service_days: '0',
  gender_restriction: 'any',
  requires_legal_review: false,
  colour: '',
  is_active: true,
};

function initialState(existing: LeaveType | null): FormState {
  if (!existing) return EMPTY;
  return {
    code: existing.code,
    name: existing.name,
    description: existing.description ?? '',
    is_paid: existing.is_paid,
    deducts_balance: existing.deducts_balance,
    allows_half_day: existing.allows_half_day,
    counts_non_working_days: existing.counts_non_working_days,
    requires_reason: existing.requires_reason,
    attachment_required_above_days: existing.attachment_required_above_days ?? '',
    min_notice_days: String(existing.min_notice_days),
    max_consecutive_days:
      existing.max_consecutive_days === null ? '' : String(existing.max_consecutive_days),
    min_service_days: String(existing.min_service_days),
    gender_restriction: existing.gender_restriction as FormState['gender_restriction'],
    requires_legal_review: existing.requires_legal_review,
    colour: existing.colour ?? '',
    is_active: existing.is_active,
  };
}

/**
 * Keyed on what is being edited so the inner form re-initialises from props on
 * mount, rather than syncing props into state in an effect - which causes a
 * cascading render and leaves the two sources of truth able to drift.
 */
export function LeaveTypeForm(props: {
  open: boolean;
  existing: LeaveType | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  return <LeaveTypeFormFields key={props.existing?.id ?? 'new'} {...props} />;
}

function LeaveTypeFormFields({
  open,
  existing,
  onClose,
  onSaved,
}: {
  open: boolean;
  existing: LeaveType | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [form, setForm] = useState<FormState>(() => initialState(existing));

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((previous) => ({ ...previous, [key]: value }));

  const save = useMutation({
    mutationFn: () => {
      const payload: Record<string, unknown> = {
        name: form.name.trim(),
        description: form.description.trim() || null,
        is_paid: form.is_paid,
        deducts_balance: form.deducts_balance,
        allows_half_day: form.allows_half_day,
        counts_non_working_days: form.counts_non_working_days,
        requires_reason: form.requires_reason,
        attachment_required_above_days: form.attachment_required_above_days.trim() || null,
        min_notice_days: Number(form.min_notice_days || 0),
        max_consecutive_days: form.max_consecutive_days.trim()
          ? Number(form.max_consecutive_days)
          : null,
        min_service_days: Number(form.min_service_days || 0),
        gender_restriction: form.gender_restriction,
        requires_legal_review: form.requires_legal_review,
        colour: form.colour.trim() || null,
        is_active: form.is_active,
      };
      return existing
        ? updateLeaveType(existing.id, payload)
        : createLeaveType({ ...payload, code: form.code.trim().toUpperCase() });
    },
    onSuccess: () => {
      toast.success(existing ? 'Leave type updated.' : 'Leave type created.');
      onSaved();
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  const fieldErrors =
    save.error && typeof save.error === 'object' && 'fieldErrors' in save.error
      ? (save.error as { fieldErrors: Record<string, string> }).fieldErrors
      : {};

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={existing ? `Edit ${existing.name}` : 'Add a leave type'}
      description={
        existing
          ? 'Changes affect future applications. Ones already decided keep the rules they were decided under.'
          : undefined
      }
      className="max-w-2xl"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={() => save.mutate()}
            disabled={
              save.isPending || form.name.trim() === '' || (!existing && !form.code.trim())
            }
          >
            {save.isPending ? 'Saving…' : existing ? 'Save changes' : 'Create type'}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          {!existing && (
            <TextField
              label="Code"
              value={form.code}
              onChange={(event) => set('code', event.target.value.toUpperCase())}
              hint="Short, e.g. CL or SL. Cannot be changed later."
              error={fieldErrors['code']}
              required
            />
          )}
          <TextField
            label="Name"
            value={form.name}
            onChange={(event) => set('name', event.target.value)}
            error={fieldErrors['name']}
            required
          />
        </div>

        <TextAreaField
          label="Description"
          value={form.description}
          onChange={(event) => set('description', event.target.value)}
          rows={2}
        />

        <fieldset className="border-line rounded-lg border p-4">
          <legend className="text-ink px-1 text-sm font-semibold">Pay and balance</legend>
          <div className="space-y-3">
            <CheckboxField
              label="Paid leave"
              checked={form.is_paid}
              onChange={(event) => set('is_paid', event.target.checked)}
              hint="A paid day of this leave is a payable day for payroll."
            />
            <CheckboxField
              label="Deducts from a balance"
              checked={form.deducts_balance}
              onChange={(event) => set('deducts_balance', event.target.checked)}
              hint="Turn off for leave without pay, which is recorded and approved but has no quota."
            />
            <CheckboxField
              label="Half days allowed"
              checked={form.allows_half_day}
              onChange={(event) => set('allows_half_day', event.target.checked)}
            />
          </div>
        </fieldset>

        <fieldset className="border-line rounded-lg border p-4">
          <legend className="text-ink px-1 text-sm font-semibold">Day counting</legend>
          <CheckboxField
            label="Count weekly offs and holidays inside the range"
            checked={form.counts_non_working_days}
            onChange={(event) => set('counts_non_working_days', event.target.checked)}
            hint="Some statutory types are defined in calendar days. This has a direct payroll consequence, so it is off unless you say otherwise."
          />
          {form.counts_non_working_days && (
            <div className="mt-3">
              <Alert variant="warning" title="This changes what employees are charged">
                A Friday-to-Monday absence will consume four days rather than two. Only turn
                this on for a type whose entitlement is genuinely defined in calendar days.
              </Alert>
            </div>
          )}
        </fieldset>

        <fieldset className="border-line rounded-lg border p-4">
          <legend className="text-ink px-1 text-sm font-semibold">
            Eligibility and evidence
          </legend>
          <div className="grid gap-4 sm:grid-cols-2">
            <TextField
              label="Minimum notice (days)"
              type="number"
              min={0}
              value={form.min_notice_days}
              onChange={(event) => set('min_notice_days', event.target.value)}
              hint="0 allows same-day application."
            />
            <TextField
              label="Maximum consecutive days"
              type="number"
              min={1}
              value={form.max_consecutive_days}
              onChange={(event) => set('max_consecutive_days', event.target.value)}
              hint="Leave empty for no limit."
            />
            <TextField
              label="Minimum service (days)"
              type="number"
              min={0}
              value={form.min_service_days}
              onChange={(event) => set('min_service_days', event.target.value)}
              hint="Service required before an employee becomes eligible."
            />
            <TextField
              label="Document required above (days)"
              value={form.attachment_required_above_days}
              onChange={(event) => set('attachment_required_above_days', event.target.value)}
              hint="Leave empty if a document is never required."
            />
            <SelectField
              label="Restricted to"
              value={form.gender_restriction}
              onChange={(event) =>
                set('gender_restriction', event.target.value as FormState['gender_restriction'])
              }
              options={[
                { value: 'any', label: 'Everyone' },
                { value: 'female', label: 'Female employees' },
                { value: 'male', label: 'Male employees' },
              ]}
              hint="Only for types whose statutory basis is gender-specific."
            />
            <TextField
              label="Colour"
              value={form.colour}
              onChange={(event) => set('colour', event.target.value)}
              hint="Optional, e.g. #2563eb. Used on calendars."
            />
          </div>
          <div className="mt-3 space-y-3">
            <CheckboxField
              label="A reason is required"
              checked={form.requires_reason}
              onChange={(event) => set('requires_reason', event.target.checked)}
            />
            <CheckboxField
              label="Entitlement needs legal review"
              checked={form.requires_legal_review}
              onChange={(event) => set('requires_legal_review', event.target.checked)}
              hint="Set this when the entitlement comes from statute and has not been checked against a verified reference. It is shown to employees and in the configuration health report rather than being quietly trusted."
            />
            <CheckboxField
              label="Active"
              checked={form.is_active}
              onChange={(event) => set('is_active', event.target.checked)}
              hint="An inactive type cannot be applied for, but existing applications are untouched."
            />
          </div>
        </fieldset>
      </div>
    </Modal>
  );
}
