/**
 * `DatePicker` wired to react-hook-form.
 *
 * A date picker is not an `<input>`, so `register()` - which hands back an
 * input's props and reads values out of DOM events - cannot drive it. This
 * bridges the two with a `Controller`, and lives in its own file so the picker
 * itself stays free of any form library.
 *
 * Empty is kept as the empty string rather than `undefined`, matching what a
 * registered text input produces, so the Zod schemas that accept
 * `.optional().or(z.literal(''))` keep working untouched.
 */

import { Controller, type Control, type FieldPath, type FieldValues } from 'react-hook-form';

import { DatePicker } from '@/components/ui/DatePicker';
import type { IsoDate } from '@/lib/dates';

export function ControlledDate<T extends FieldValues>({
  control,
  name,
  label,
  hint,
  error,
  required,
  disabled,
  clearable,
  min,
  max,
}: {
  control: Control<T>;
  name: FieldPath<T>;
  label: string;
  hint?: string | undefined;
  error?: string | undefined;
  required?: boolean | undefined;
  disabled?: boolean | undefined;
  clearable?: boolean | undefined;
  min?: IsoDate | undefined;
  max?: IsoDate | undefined;
}) {
  return (
    <Controller
      control={control}
      name={name}
      render={({ field }) => (
        <DatePicker
          label={label}
          value={typeof field.value === 'string' ? field.value : ''}
          onChange={field.onChange}
          {...(hint !== undefined ? { hint } : {})}
          {...(error !== undefined ? { error } : {})}
          {...(required !== undefined ? { required } : {})}
          {...(disabled !== undefined ? { disabled } : {})}
          {...(clearable !== undefined ? { clearable } : {})}
          {...(min !== undefined ? { min } : {})}
          {...(max !== undefined ? { max } : {})}
        />
      )}
    />
  );
}
