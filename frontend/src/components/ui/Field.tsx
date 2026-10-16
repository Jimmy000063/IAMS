/**
 * Form field primitives.
 *
 * Each input is wired to its label, help text and error message via
 * `aria-describedby` / `aria-invalid`, so the error is announced rather than
 * only shown in red.
 *
 * The look lives in `.field-control` in `index.css` rather than in a string of
 * utilities here, for three reasons: the focus halo needs real CSS (a blurred,
 * coloured box-shadow that an outline cannot express), the states compose
 * properly as pseudo-classes, and a field therefore restyles with the active
 * theme without this file knowing a single colour.
 *
 * Fields are a slightly sunken surface that lifts and lights up on focus. That
 * is deliberate: a control that looks recessed invites typing, and the lit edge
 * on focus is the only moving thing on the screen at that moment, so the eye
 * does not have to hunt for where the caret went.
 */

import { AlertCircle, ChevronDown } from 'lucide-react';
import {
  forwardRef,
  useId,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from 'react';

import { cn } from '@/lib/utils';

/*
 * Optional props are declared `?: T | undefined` rather than `?: T`.
 *
 * `exactOptionalPropertyTypes` is on, which otherwise forbids passing an
 * explicit `undefined` - and `hint={maybeUndefined}` is the natural way to
 * write a conditional prop. Spelling out `| undefined` keeps the strictness
 * where it protects data types while letting components be used normally.
 */

interface FieldShellProps {
  label: string;
  hint?: string | undefined;
  error?: string | undefined;
  required?: boolean | undefined;
  /** Shown beside the label for fields that are safe to leave blank. */
  optionalLabel?: boolean | undefined;
  children: (ids: { controlId: string; describedBy: string | undefined }) => ReactNode;
}

export function FieldShell({
  label,
  hint,
  error,
  required,
  optionalLabel,
  children,
}: FieldShellProps) {
  const base = useId();
  const controlId = `${base}-control`;
  const hintId = `${base}-hint`;
  const errorId = `${base}-error`;
  const describedBy = [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(' ');

  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between gap-2">
        <label htmlFor={controlId} className="text-ink block text-sm font-medium">
          {label}
          {required && (
            <span className="text-danger-600 ml-0.5" aria-hidden="true">
              *
            </span>
          )}
          {required && <span className="sr-only"> (required)</span>}
        </label>
        {optionalLabel && !required && (
          <span className="text-ink-muted text-[11px]">Optional</span>
        )}
      </div>

      {children({ controlId, describedBy: describedBy || undefined })}

      {hint && !error && (
        <p id={hintId} className="text-ink-muted text-xs">
          {hint}
        </p>
      )}
      {error && (
        <p
          id={errorId}
          className="text-danger-600 dark:text-danger-400 flex items-start gap-1 text-xs font-medium"
        >
          <AlertCircle className="mt-px size-3.5 shrink-0" aria-hidden="true" />
          {error}
        </p>
      )}
    </div>
  );
}

/** Height and padding shared by the single-line controls. */
const CONTROL_SIZE = 'h-10 px-3 text-sm';

export interface TextFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'id'> {
  label: string;
  hint?: string | undefined;
  error?: string | undefined;
  /** A small icon inside the field, on the leading edge. */
  leadingIcon?: ReactNode;
  /** Static text on the trailing edge: a unit, a currency, a domain. */
  suffix?: ReactNode;
  /**
   * An interactive control on the trailing edge - a password reveal, a clear
   * button. Unlike `suffix` it receives pointer events, so it must be a real
   * button with its own accessible name.
   */
  trailingButton?: ReactNode;
  optionalLabel?: boolean | undefined;
}

export const TextField = forwardRef<HTMLInputElement, TextFieldProps>(function TextField(
  {
    label,
    hint,
    error,
    className,
    required,
    leadingIcon,
    suffix,
    trailingButton,
    optionalLabel,
    ...props
  },
  ref,
) {
  return (
    <FieldShell
      label={label}
      {...(hint !== undefined ? { hint } : {})}
      {...(error !== undefined ? { error } : {})}
      {...(required !== undefined ? { required } : {})}
      {...(optionalLabel !== undefined ? { optionalLabel } : {})}
    >
      {({ controlId, describedBy }) => (
        <div className="relative">
          {leadingIcon && (
            <span
              className="text-ink-muted pointer-events-none absolute top-1/2 left-3 -translate-y-1/2"
              aria-hidden="true"
            >
              {leadingIcon}
            </span>
          )}
          <input
            ref={ref}
            id={controlId}
            aria-invalid={error ? true : undefined}
            aria-describedby={describedBy}
            required={required}
            className={cn(
              'field-control placeholder:text-ink-muted',
              CONTROL_SIZE,
              leadingIcon && 'pl-9.5',
              suffix && 'pr-12',
              trailingButton && 'pr-10',
              className,
            )}
            {...props}
          />
          {suffix && (
            <span
              className="text-ink-muted pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-xs"
              aria-hidden="true"
            >
              {suffix}
            </span>
          )}
          {trailingButton && (
            <span className="absolute top-1/2 right-1.5 -translate-y-1/2">
              {trailingButton}
            </span>
          )}
        </div>
      )}
    </FieldShell>
  );
});

export interface SelectFieldProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'id'> {
  label: string;
  hint?: string | undefined;
  error?: string | undefined;
  options: { value: string; label: string }[];
  optionalLabel?: boolean | undefined;
}

export const SelectField = forwardRef<HTMLSelectElement, SelectFieldProps>(function SelectField(
  { label, hint, error, options, className, required, optionalLabel, ...props },
  ref,
) {
  return (
    <FieldShell
      label={label}
      {...(hint !== undefined ? { hint } : {})}
      {...(error !== undefined ? { error } : {})}
      {...(required !== undefined ? { required } : {})}
      {...(optionalLabel !== undefined ? { optionalLabel } : {})}
    >
      {({ controlId, describedBy }) => (
        <div className="relative">
          <select
            ref={ref}
            id={controlId}
            aria-invalid={error ? true : undefined}
            aria-describedby={describedBy}
            required={required}
            /*
             * `appearance-none` replaces the operating system's dropdown arrow
             * with the one below, so a select matches the rest of the form on
             * every platform instead of looking like a Windows control dropped
             * into the page. The list itself is still the native popup, which
             * is the right trade: it is accessible, searchable by typing, and
             * usable on a touch keyboard - none of which a div-based imitation
             * gets for free.
             */
            className={cn('field-control appearance-none pr-9', CONTROL_SIZE, className)}
            {...props}
          >
            {options.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <ChevronDown
            className="text-ink-muted pointer-events-none absolute top-1/2 right-3 size-4 -translate-y-1/2"
            aria-hidden="true"
          />
        </div>
      )}
    </FieldShell>
  );
});

export interface TextAreaFieldProps extends Omit<
  TextareaHTMLAttributes<HTMLTextAreaElement>,
  'id'
> {
  label: string;
  hint?: string | undefined;
  error?: string | undefined;
  optionalLabel?: boolean | undefined;
}

export const TextAreaField = forwardRef<HTMLTextAreaElement, TextAreaFieldProps>(
  function TextAreaField(
    { label, hint, error, className, required, optionalLabel, ...props },
    ref,
  ) {
    return (
      <FieldShell
        label={label}
        {...(hint !== undefined ? { hint } : {})}
        {...(error !== undefined ? { error } : {})}
        {...(required !== undefined ? { required } : {})}
        {...(optionalLabel !== undefined ? { optionalLabel } : {})}
      >
        {({ controlId, describedBy }) => (
          <textarea
            ref={ref}
            id={controlId}
            aria-invalid={error ? true : undefined}
            aria-describedby={describedBy}
            required={required}
            className={cn(
              'field-control placeholder:text-ink-muted min-h-24 px-3 py-2.5 text-sm',
              className,
            )}
            {...props}
          />
        )}
      </FieldShell>
    );
  },
);

export interface CheckboxFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'id'> {
  label: string;
  hint?: string | undefined;
}

/**
 * A checkbox drawn by us rather than by the operating system.
 *
 * The real `<input>` is still there and still focusable - it is made
 * transparent and stretched over the box, so every keyboard, screen reader and
 * form behaviour is the browser's. Only the paint is ours.
 */
export const CheckboxField = forwardRef<HTMLInputElement, CheckboxFieldProps>(
  function CheckboxField({ label, hint, className, disabled, ...props }, ref) {
    const base = useId();
    const controlId = `${base}-checkbox`;
    const hintId = `${base}-hint`;

    return (
      <div className={cn('flex items-start gap-3', disabled && 'opacity-60')}>
        <span className="relative mt-0.5 grid size-[18px] shrink-0 place-items-center">
          <input
            ref={ref}
            id={controlId}
            type="checkbox"
            disabled={disabled}
            aria-describedby={hint ? hintId : undefined}
            className={cn('peer absolute inset-0 z-10 cursor-pointer opacity-0', className)}
            {...props}
          />
          <span
            aria-hidden="true"
            className={cn(
              'border-line-strong bg-field pointer-events-none absolute inset-0 rounded-[0.3rem] border',
              'transition-[background-color,border-color,box-shadow] duration-150',
              'peer-hover:border-brand-400',
              'peer-checked:border-brand-600 peer-checked:bg-brand-600',
              'peer-focus-visible:shadow-[0_0_0_3.5px_var(--ring-focus)]',
              'peer-disabled:cursor-not-allowed',
            )}
          />
          {/* Drawn as a path rather than a glyph so the stroke scales with the
              box and stays crisp at this size. */}
          <svg
            aria-hidden="true"
            viewBox="0 0 16 16"
            className="pointer-events-none relative size-3 scale-50 text-white opacity-0 transition-[opacity,transform] duration-150 peer-checked:scale-100 peer-checked:opacity-100"
          >
            <path
              d="M3 8.5 L6.2 11.5 L13 4.5"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.25"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </span>
        <div className="min-w-0">
          <label
            htmlFor={controlId}
            className={cn(
              'text-ink text-sm font-medium',
              disabled ? 'cursor-not-allowed' : 'cursor-pointer',
            )}
          >
            {label}
          </label>
          {hint && (
            <p id={hintId} className="text-ink-muted text-xs">
              {hint}
            </p>
          )}
        </div>
      </div>
    );
  },
);

/**
 * A switch, for a setting that takes effect immediately.
 *
 * Distinct from a checkbox on purpose: a checkbox is part of a form you submit,
 * a switch is a thing you flip. Using the right one tells the user whether to
 * look for a Save button.
 */
export function SwitchField({
  checked,
  onChange,
  label,
  hint,
  disabled,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  hint?: string | undefined;
  disabled?: boolean | undefined;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        'group flex w-full items-start gap-3 text-left',
        disabled && 'cursor-not-allowed opacity-60',
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          'relative mt-0.5 h-5 w-9 shrink-0 rounded-full transition-colors duration-200',
          checked ? 'bg-brand-600' : 'bg-line-strong',
        )}
      >
        <span
          className={cn(
            'absolute top-0.5 size-4 rounded-full bg-white shadow-sm',
            'transition-[left] duration-200 ease-[var(--ease-out-soft)]',
            checked ? 'left-4.5' : 'left-0.5',
          )}
        />
      </span>
      <span className="min-w-0">
        <span className="text-ink block text-sm font-medium">{label}</span>
        {hint && <span className="text-ink-muted block text-xs">{hint}</span>}
      </span>
    </button>
  );
}
