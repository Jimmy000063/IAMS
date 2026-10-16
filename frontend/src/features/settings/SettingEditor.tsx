/**
 * Edit dialog for a single business setting.
 *
 * The control rendered depends on the setting's declared `value_type`, and the
 * values offered come from its `constraints`. Nothing about any individual
 * setting is hardcoded here, so a new setting added to the backend catalogue
 * becomes editable without a frontend change.
 */

import { useState } from 'react';

import { Button } from '@/components/ui/Button';
import { CheckboxField, SelectField, TextAreaField, TextField } from '@/components/ui/Field';
import { Alert } from '@/components/ui/Feedback';
import { Modal } from '@/components/ui/Modal';
import type { SettingRecord, UpdateSettingPayload } from '@/features/settings/api';
import { formatSettingValue } from '@/lib/utils';

interface SettingEditorProps {
  setting: SettingRecord | null;
  isSubmitting: boolean;
  onClose: () => void;
  onSubmit: (payload: UpdateSettingPayload) => void;
}

/** Turn the stored value into something an `<input>` can hold. */
function toEditable(setting: SettingRecord): string {
  const { value, value_type } = setting;
  if (value === null || value === undefined) return '';
  if (value_type === 'json') return JSON.stringify(value, null, 2);
  return String(value);
}

/** Parse the edited text back into the type the API expects. */
function fromEditable(
  setting: SettingRecord,
  text: string,
  checkbox: boolean,
): { ok: true; value: unknown } | { ok: false; message: string } {
  switch (setting.value_type) {
    case 'boolean':
      return { ok: true, value: checkbox };

    case 'integer': {
      if (!/^-?\d+$/.test(text.trim())) {
        return { ok: false, message: 'Enter a whole number.' };
      }
      return { ok: true, value: Number.parseInt(text.trim(), 10) };
    }

    case 'decimal': {
      const parsed = Number(text.trim());
      if (text.trim() === '' || Number.isNaN(parsed)) {
        return { ok: false, message: 'Enter a number.' };
      }
      // Sent as a string so the server parses it as an exact Decimal; a JSON
      // float would round before it ever reached the database.
      return { ok: true, value: text.trim() };
    }

    case 'json': {
      try {
        return { ok: true, value: JSON.parse(text) };
      } catch {
        return { ok: false, message: 'Enter valid JSON (for example ["mon","tue"]).' };
      }
    }

    default:
      return { ok: true, value: text };
  }
}

export function SettingEditor({
  setting,
  isSubmitting,
  onClose,
  onSubmit,
}: SettingEditorProps) {
  if (!setting) return null;

  // Keyed on the setting so React remounts the form when a different setting is
  // opened. That resets the fields from props as part of mounting, instead of
  // an effect that watches `setting` and calls setState - which would cascade
  // an extra render and briefly show the previous setting's values.
  return (
    <SettingEditorForm
      key={setting.key}
      setting={setting}
      isSubmitting={isSubmitting}
      onClose={onClose}
      onSubmit={onSubmit}
    />
  );
}

function SettingEditorForm({
  setting,
  isSubmitting,
  onClose,
  onSubmit,
}: SettingEditorProps & { setting: SettingRecord }) {
  const [text, setText] = useState(() => toEditable(setting));
  const [checkbox, setCheckbox] = useState(setting.value === true);
  const [reason, setReason] = useState('');
  const [sourceReference, setSourceReference] = useState(setting.source_reference ?? '');
  const [confirm, setConfirm] = useState(setting.is_confirmed);
  const [localError, setLocalError] = useState<string | null>(null);

  const constraints = setting.constraints ?? {};
  const unit = constraints.unit ? ` (${constraints.unit})` : '';

  const handleSubmit = () => {
    setLocalError(null);

    const parsed = fromEditable(setting, text, checkbox);
    if (!parsed.ok) {
      setLocalError(parsed.message);
      return;
    }

    // Mirror the server rule so the user is not sent a round trip to be told.
    if (setting.requires_legal_review && confirm && !reason.trim() && !sourceReference.trim()) {
      setLocalError(
        'This setting has statutory consequences. Record the authoritative source before confirming it.',
      );
      return;
    }

    const payload: UpdateSettingPayload = { value: parsed.value, confirm };
    if (reason.trim()) payload.reason = reason.trim();
    if (sourceReference.trim()) payload.source_reference = sourceReference.trim();
    onSubmit(payload);
  };

  return (
    <Modal
      open={setting !== null}
      onClose={onClose}
      title={setting.label}
      description={setting.description}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={isSubmitting}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} isLoading={isSubmitting}>
            Save change
          </Button>
        </>
      }
    >
      <div className="space-y-5">
        {localError && (
          <Alert variant="danger" title="Check this value">
            {localError}
          </Alert>
        )}

        {setting.requires_legal_review && (
          <Alert variant="warning" title="Statutory consequences">
            This value affects statutory calculations. It must be verified against an
            authoritative source before payroll can be finalized.
          </Alert>
        )}

        {setting.value_type === 'boolean' && (
          <CheckboxField
            label={setting.label}
            checked={checkbox}
            onChange={(event) => setCheckbox(event.target.checked)}
          />
        )}

        {setting.value_type === 'enum' && (
          <SelectField
            label={`Value${unit}`}
            value={text}
            onChange={(event) => setText(event.target.value)}
            options={(constraints.choices ?? []).map((choice) => ({
              value: choice,
              label: choice,
            }))}
          />
        )}

        {setting.value_type === 'json' && (
          <TextAreaField
            label="Value (JSON)"
            value={text}
            rows={5}
            className="font-mono text-xs"
            onChange={(event) => setText(event.target.value)}
            hint={
              constraints.item_choices
                ? `Allowed entries: ${constraints.item_choices.join(', ')}`
                : 'Enter a JSON array or object.'
            }
          />
        )}

        {['integer', 'decimal', 'string', 'time', 'date'].includes(setting.value_type) && (
          <TextField
            label={`Value${unit}`}
            value={text}
            onChange={(event) => setText(event.target.value)}
            inputMode={
              setting.value_type === 'integer' || setting.value_type === 'decimal'
                ? 'numeric'
                : undefined
            }
            {...(setting.value_type === 'time'
              ? { placeholder: 'HH:MM' }
              : setting.value_type === 'date'
                ? { placeholder: 'YYYY-MM-DD' }
                : {})}
            hint={
              constraints.min !== undefined || constraints.max !== undefined
                ? `Allowed range: ${constraints.min ?? '-'} to ${constraints.max ?? '-'}`
                : undefined
            }
          />
        )}

        <dl className="bg-sunken grid grid-cols-2 gap-3 rounded-md p-3 text-xs">
          <div>
            <dt className="text-ink-soft font-medium">Current value</dt>
            <dd className="text-ink mt-0.5">{formatSettingValue(setting.value)}</dd>
          </div>
          <div>
            <dt className="text-ink-soft font-medium">Application default</dt>
            <dd className="text-ink mt-0.5">{formatSettingValue(setting.default_value)}</dd>
          </div>
        </dl>

        <TextAreaField
          label="Reason for the change"
          value={reason}
          rows={2}
          onChange={(event) => setReason(event.target.value)}
          hint="Recorded in the change history and the audit log."
        />

        <TextField
          label="Authoritative source"
          value={sourceReference}
          onChange={(event) => setSourceReference(event.target.value)}
          hint="Policy document, board resolution, circular or notification."
        />

        <CheckboxField
          label="This is a confirmed company decision"
          hint="Leave unchecked while the value is still a working placeholder."
          checked={confirm}
          onChange={(event) => setConfirm(event.target.checked)}
        />
      </div>
    </Modal>
  );
}
