/**
 * The leave application form.
 *
 * It shows what the request will actually cost before submission, and the
 * figure comes from the **backend**, not from arithmetic in the browser: a
 * client-side copy of the calendar, half-day and sandwich rules would drift and
 * tell the applicant a number that turns out to be wrong after approval.
 *
 * Every rule the backend enforces is surfaced here as guidance, but the backend
 * is the control. This form is a convenience, not a gate.
 */

import { useMutation, useQuery } from '@tanstack/react-query';
import { CircleAlert, Info } from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/Button';
import { DatePicker } from '@/components/ui/DatePicker';
import { SelectField, TextAreaField, TextField } from '@/components/ui/Field';
import { Alert } from '@/components/ui/Feedback';
import { Modal } from '@/components/ui/Modal';
import { Badge } from '@/components/ui/Surface';
import {
  applyForLeave,
  applyOnBehalf,
  formatDays,
  isPositive,
  leaveKeys,
  previewLeave,
  type ApplyInput,
  type DayPortion,
  type LeaveBalance,
} from '@/features/leave/api';
import { compareIso, todayIso, type IsoDate } from '@/lib/dates';
import { useToast } from '@/lib/toast';

const PORTIONS: { value: DayPortion; label: string }[] = [
  { value: 'full', label: 'Full day' },
  { value: 'first_half', label: 'First half' },
  { value: 'second_half', label: 'Second half' },
];

export function ApplyLeaveModal({
  open,
  balances,
  initialTypeId,
  onBehalfOfEmployeeId,
  onClose,
  onApplied,
}: {
  open: boolean;
  balances: LeaveBalance[];
  initialTypeId?: number | undefined;
  onBehalfOfEmployeeId?: number | undefined;
  onClose: () => void;
  onApplied: () => void;
}) {
  const toast = useToast();

  const [typeId, setTypeId] = useState<string>(initialTypeId ? String(initialTypeId) : '');
  const [start, setStart] = useState<IsoDate>(todayIso());
  const [end, setEnd] = useState<IsoDate>(todayIso());
  const [startPortion, setStartPortion] = useState<DayPortion>('full');
  const [endPortion, setEndPortion] = useState<DayPortion>('full');
  const [reason, setReason] = useState('');
  const [contact, setContact] = useState('');

  const selected = balances.find((item) => String(item.leave_type_id) === typeId);
  const singleDay = start === end;
  const rangeValid = compareIso(start, end) <= 0;

  const request: ApplyInput | null =
    typeId !== '' && rangeValid
      ? {
          leave_type_id: Number(typeId),
          start_date: start,
          end_date: end,
          start_portion: startPortion,
          ...(singleDay ? {} : { end_portion: endPortion }),
          reason: reason.trim() || null,
        }
      : null;

  const preview = useQuery({
    queryKey: leaveKeys.preview({ ...request, onBehalfOfEmployeeId }),
    queryFn: () =>
      previewLeave(
        onBehalfOfEmployeeId
          ? { ...(request as ApplyInput), employee_id: onBehalfOfEmployeeId }
          : (request as ApplyInput),
      ),
    enabled: open && request !== null,
    // The reason text changes on every keystroke, and only affects whether a
    // "reason required" problem is reported. Keep the result briefly so typing
    // does not fire a request per character.
    staleTime: 2000,
  });

  const submit = useMutation({
    mutationFn: () => {
      const payload = {
        ...(request as ApplyInput),
        contact_during_leave: contact.trim() || null,
      };
      return onBehalfOfEmployeeId
        ? applyOnBehalf({ ...payload, employee_id: onBehalfOfEmployeeId })
        : applyForLeave(payload);
    },
    onSuccess: (application) => {
      setReason('');
      setContact('');
      if (isPositive(application.unpaid_days)) {
        toast.show({
          variant: 'warning',
          title: `Submitted: ${formatDays(application.total_days)} day(s).`,
          description: `${formatDays(application.unpaid_days)} day(s) are unpaid — the balance did not cover the whole request.`,
        });
      } else {
        toast.success(
          `Submitted: ${formatDays(application.total_days)} day(s), awaiting ${
            application.current_stage === 'hr_admin' ? 'HR' : 'your manager'
          }.`,
        );
      }
      onApplied();
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  const fieldErrors =
    submit.error && typeof submit.error === 'object' && 'fieldErrors' in submit.error
      ? (submit.error as { fieldErrors: Record<string, string> }).fieldErrors
      : {};

  const data = preview.data;
  const canSubmit = typeId !== '' && rangeValid && !submit.isPending;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={onBehalfOfEmployeeId ? 'Record leave for an employee' : 'Apply for leave'}
      description={
        onBehalfOfEmployeeId
          ? 'Recorded as raised on their behalf, and still routed through the approval chain.'
          : undefined
      }
      className="max-w-2xl"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => submit.mutate()} disabled={!canSubmit}>
            {submit.isPending ? 'Submitting…' : 'Submit application'}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <SelectField
          label="Leave type"
          value={typeId}
          onChange={(event) => {
            setTypeId(event.target.value);
            const next = balances.find(
              (item) => String(item.leave_type_id) === event.target.value,
            );
            if (next && !next.allows_half_day) {
              setStartPortion('full');
              setEndPortion('full');
            }
          }}
          options={[
            { value: '', label: 'Select a leave type…' },
            ...balances
              .filter((item) => !item.deducts_balance || item.has_policy)
              .map((item) => ({
                value: String(item.leave_type_id),
                label: `${item.leave_type_name} (${formatDays(item.available_days)} available)`,
              })),
          ]}
          error={fieldErrors['leave_type_id']}
          required
        />

        {selected && (
          <div className="flex flex-wrap gap-2">
            {!selected.is_paid && <Badge tone="neutral">Unpaid leave</Badge>}
            {selected.counts_non_working_days && (
              <Badge tone="warning">Counted in calendar days</Badge>
            )}
            {!selected.allows_half_day && <Badge tone="neutral">Full days only</Badge>}
            {selected.requires_legal_review && (
              <Badge tone="warning">Entitlement not legally verified</Badge>
            )}
          </div>
        )}

        <div className="grid gap-4 sm:grid-cols-2">
          <DatePicker
            label="From"
            value={start}
            onChange={(value) => {
              setStart(value);
              if (compareIso(value, end) > 0) setEnd(value);
            }}
            {...(fieldErrors['start_date'] ? { error: fieldErrors['start_date'] } : {})}
          />
          <DatePicker
            label="To"
            value={end}
            onChange={setEnd}
            min={start}
            {...(fieldErrors['end_date'] ? { error: fieldErrors['end_date'] } : {})}
          />
        </div>

        {selected?.allows_half_day && (
          <div className="grid gap-4 sm:grid-cols-2">
            <SelectField
              label={singleDay ? 'Portion of the day' : 'First day'}
              value={startPortion}
              onChange={(event) => setStartPortion(event.target.value as DayPortion)}
              options={PORTIONS}
              hint={
                singleDay ? undefined : 'Half-day portions apply to the ends of the range only.'
              }
            />
            {!singleDay && (
              <SelectField
                label="Last day"
                value={endPortion}
                onChange={(event) => setEndPortion(event.target.value as DayPortion)}
                options={PORTIONS}
              />
            )}
          </div>
        )}

        {data && (
          <Alert
            variant={isPositive(data.total_days) ? 'info' : 'warning'}
            title="What this will cost"
            icon={Info}
          >
            {isPositive(data.total_days) ? (
              <>
                <p>
                  <strong>{formatDays(data.total_days)} day(s)</strong> deducted across{' '}
                  {data.span_days} calendar day{data.span_days === 1 ? '' : 's'}.
                </p>
                {data.notes.map((note) => (
                  <p key={note} className="mt-1">
                    {note}
                  </p>
                ))}
                {isPositive(data.unpaid_days) && (
                  <p className="mt-1">
                    {formatDays(data.paid_days)} paid, {formatDays(data.unpaid_days)} unpaid.{' '}
                    {data.split_reason}
                  </p>
                )}
              </>
            ) : (
              <p>
                None of the selected dates are working days, so there is nothing to apply for.
              </p>
            )}
          </Alert>
        )}

        {data && data.problems.length > 0 && (
          <Alert variant="warning" title="This cannot be submitted yet" icon={CircleAlert}>
            <ul className="list-inside list-disc space-y-1">
              {data.problems.map((problem, index) => (
                <li key={index}>{problem.message}</li>
              ))}
            </ul>
          </Alert>
        )}

        <TextAreaField
          label="Reason"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          rows={3}
          required={selected?.requires_reason ?? false}
          error={fieldErrors['reason']}
          hint={
            selected?.requires_reason
              ? 'Required for this leave type.'
              : 'Optional, but it helps whoever reviews the request.'
          }
        />

        <TextField
          label="Contact while away"
          value={contact}
          onChange={(event) => setContact(event.target.value)}
          hint="Optional. Somewhere you could be reached if something is urgent."
        />
      </div>
    </Modal>
  );
}
