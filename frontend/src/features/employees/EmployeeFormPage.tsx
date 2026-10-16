import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Save, Sparkles } from 'lucide-react';
import { useEffect } from 'react';
import { useForm } from 'react-hook-form';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { z } from 'zod';

import { Button } from '@/components/ui/Button';
import { ControlledDate } from '@/components/ui/ControlledDate';
import { SelectField, TextAreaField, TextField } from '@/components/ui/Field';
import { Alert, LoadingPanel } from '@/components/ui/Feedback';
import { Card, CardBody, CardHeader, PageHeader } from '@/components/ui/Surface';
import {
  EMPLOYMENT_STATUSES,
  EMPLOYMENT_TYPES,
  createEmployee,
  employeeKeys,
  fetchDepartments,
  fetchDesignations,
  fetchEmployee,
  fetchEmployees,
  fetchNextCode,
  fetchWorkLocations,
  orgKeys,
  updateEmployee,
} from '@/features/employees/api';
import { ApiError } from '@/lib/api';
import { useToast } from '@/lib/toast';
import { humanise } from '@/lib/utils';

/**
 * Client-side schema. The server validates independently and its verdict wins;
 * this exists so the user gets immediate feedback rather than a round trip.
 *
 * Statuses exclude `exited` on purpose: an exit is recorded through the exit
 * action, which also closes the employment-history period and deactivates the
 * account. Setting the status directly would skip both.
 */
const schema = z.object({
  employee_code: z
    .string()
    .trim()
    .min(2, 'At least 2 characters.')
    .max(30)
    .regex(
      /^[A-Za-z0-9][A-Za-z0-9._-]*$/,
      'Letters, digits, dot, underscore and hyphen only, starting with a letter or digit.',
    ),
  first_name: z.string().trim().min(1, 'Required.').max(80),
  middle_name: z.string().trim().max(80).optional().or(z.literal('')),
  last_name: z.string().trim().max(80).optional().or(z.literal('')),
  work_email: z.string().trim().email('Enter a valid email.').optional().or(z.literal('')),
  personal_email: z.string().trim().email('Enter a valid email.').optional().or(z.literal('')),
  work_phone: z.string().trim().max(25).optional().or(z.literal('')),
  personal_phone: z.string().trim().max(25).optional().or(z.literal('')),
  date_of_birth: z.string().optional().or(z.literal('')),
  gender: z.string().optional().or(z.literal('')),
  marital_status: z.string().optional().or(z.literal('')),
  department_id: z.string().optional().or(z.literal('')),
  designation_id: z.string().optional().or(z.literal('')),
  work_location_id: z.string().optional().or(z.literal('')),
  reporting_manager_id: z.string().optional().or(z.literal('')),
  employment_type: z.enum(['full_time', 'part_time', 'contract', 'intern', 'consultant']),
  employment_status: z.enum(['probation', 'active', 'notice_period', 'suspended']),
  date_of_joining: z.string().min(1, 'Required.'),
  probation_end_date: z.string().optional().or(z.literal('')),
  city: z.string().trim().max(80).optional().or(z.literal('')),
  state: z.string().trim().max(80).optional().or(z.literal('')),
  postal_code: z.string().trim().max(20).optional().or(z.literal('')),
  address_line1: z.string().trim().max(200).optional().or(z.literal('')),
  emergency_contact_name: z.string().trim().max(120).optional().or(z.literal('')),
  emergency_contact_phone: z.string().trim().max(25).optional().or(z.literal('')),
  emergency_contact_relation: z.string().trim().max(50).optional().or(z.literal('')),
  notes: z.string().trim().max(4000).optional().or(z.literal('')),
  change_reason: z.string().trim().max(1000).optional().or(z.literal('')),
  effective_from: z.string().optional().or(z.literal('')),
});

type FormValues = z.infer<typeof schema>;

/** Strip blanks and convert the string-typed selects back to ids. */
function toPayload(values: FormValues, isEdit: boolean): Record<string, unknown> {
  const numeric = new Set([
    'department_id',
    'designation_id',
    'work_location_id',
    'reporting_manager_id',
  ]);
  const payload: Record<string, unknown> = {};

  for (const [key, raw] of Object.entries(values)) {
    if (raw === '' || raw === undefined) {
      // An omitted optional field must not be sent as "", which the server
      // would reject as an invalid email/date rather than treat as absent.
      continue;
    }
    payload[key] = numeric.has(key) ? Number(raw) : raw;
  }

  if (!isEdit) {
    delete payload['change_reason'];
    delete payload['effective_from'];
  }
  return payload;
}

export function EmployeeFormPage() {
  const { employeeId } = useParams<{ employeeId: string }>();
  const isEdit = employeeId !== undefined;
  const id = Number(employeeId);

  const navigate = useNavigate();
  const toast = useToast();
  const queryClient = useQueryClient();

  const existingQuery = useQuery({
    queryKey: employeeKeys.detail(id),
    queryFn: () => fetchEmployee(id),
    enabled: isEdit && Number.isFinite(id),
  });

  const nextCodeQuery = useQuery({
    queryKey: ['employees', 'next-code'],
    queryFn: () => fetchNextCode('ITC'),
    enabled: !isEdit,
  });

  const departmentsQuery = useQuery({
    queryKey: orgKeys.departments(),
    queryFn: () => fetchDepartments(),
  });
  const designationsQuery = useQuery({
    queryKey: orgKeys.designations(),
    queryFn: () => fetchDesignations(),
  });
  const locationsQuery = useQuery({
    queryKey: orgKeys.locations(),
    queryFn: () => fetchWorkLocations(),
  });
  const managersQuery = useQuery({
    queryKey: employeeKeys.list({ purpose: 'manager-picker' }),
    queryFn: () => fetchEmployees({ page_size: 200 }),
  });

  const {
    register,
    control,
    handleSubmit,
    reset,
    setValue,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      employee_code: '',
      first_name: '',
      employment_type: 'full_time',
      employment_status: 'probation',
      date_of_joining: '',
    },
  });

  // Populate from the server once, when editing or when a code is suggested.
  useEffect(() => {
    if (isEdit && existingQuery.data) {
      const e = existingQuery.data;
      reset({
        employee_code: e.employee_code,
        first_name: e.first_name,
        middle_name: e.middle_name ?? '',
        last_name: e.last_name ?? '',
        work_email: e.work_email ?? '',
        personal_email: e.personal_email ?? '',
        work_phone: e.work_phone ?? '',
        personal_phone: e.personal_phone ?? '',
        date_of_birth: e.date_of_birth ?? '',
        gender: e.gender ?? '',
        marital_status: e.marital_status ?? '',
        department_id: e.department_id ? String(e.department_id) : '',
        designation_id: e.designation_id ? String(e.designation_id) : '',
        work_location_id: e.work_location_id ? String(e.work_location_id) : '',
        reporting_manager_id: e.reporting_manager_id ? String(e.reporting_manager_id) : '',
        employment_type: e.employment_type as FormValues['employment_type'],
        employment_status: (e.employment_status === 'exited'
          ? 'active'
          : e.employment_status) as FormValues['employment_status'],
        date_of_joining: e.date_of_joining,
        probation_end_date: e.probation_end_date ?? '',
        address_line1: e.address_line1 ?? '',
        city: e.city ?? '',
        state: e.state ?? '',
        postal_code: e.postal_code ?? '',
        emergency_contact_name: e.emergency_contact_name ?? '',
        emergency_contact_phone: e.emergency_contact_phone ?? '',
        emergency_contact_relation: e.emergency_contact_relation ?? '',
        notes: e.notes ?? '',
      });
    }
  }, [isEdit, existingQuery.data, reset]);

  useEffect(() => {
    if (!isEdit && nextCodeQuery.data) {
      setValue('employee_code', nextCodeQuery.data.suggested_code);
    }
  }, [isEdit, nextCodeQuery.data, setValue]);

  const mutation = useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      isEdit ? updateEmployee(id, payload) : createEmployee(payload),
    onSuccess: async (employee) => {
      toast.success(
        isEdit ? 'Employee updated' : 'Employee onboarded',
        `${employee.employee_code} — ${employee.full_name}`,
      );
      await queryClient.invalidateQueries({ queryKey: employeeKeys.all });
      navigate(`/employees/${employee.id}`);
    },
    onError: (error) => {
      // Map field-level server errors back onto the form so they appear next to
      // the input, rather than only as a toast the user must remember.
      if (error instanceof ApiError) {
        let matched = false;
        for (const [field, message] of Object.entries(error.fieldErrors)) {
          if (field in schema.shape) {
            setError(field as keyof FormValues, { message });
            matched = true;
          }
        }
        if (!matched) toast.apiError(error, 'Could not save the employee');
      } else {
        toast.apiError(error, 'Could not save the employee');
      }
    },
  });

  if (isEdit && existingQuery.isPending) return <LoadingPanel label="Loading employee…" />;

  const managers = (managersQuery.data?.items ?? []).filter(
    (candidate) => !isEdit || candidate.id !== id,
  );

  return (
    <div>
      <Link
        to={isEdit ? `/employees/${id}` : '/employees'}
        className="text-ink-soft hover:text-ink mb-4 inline-flex items-center gap-1.5 text-sm"
      >
        <ArrowLeft className="size-4" aria-hidden="true" />
        {isEdit ? 'Back to employee' : 'Back to directory'}
      </Link>

      <PageHeader
        title={isEdit ? 'Edit employee' : 'Onboard an employee'}
        description={
          isEdit
            ? 'Changes to department, designation, location, manager, type or status open a new employment-history period.'
            : 'Creates the employee record and opens their first employment-history period.'
        }
      />

      <form
        onSubmit={handleSubmit((values) => mutation.mutate(toPayload(values, isEdit)))}
        className="space-y-6"
        noValidate
      >
        <Card>
          <CardHeader title="Identity" />
          <CardBody className="grid gap-5 sm:grid-cols-2">
            <TextField
              label="Employee code"
              required
              hint={
                !isEdit && nextCodeQuery.data
                  ? `Suggested from the highest code in use`
                  : 'Also usable as a sign-in identifier.'
              }
              error={errors.employee_code?.message}
              {...register('employee_code')}
            />
            <div className="flex items-end">
              {!isEdit && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  leadingIcon={<Sparkles className="size-4" aria-hidden="true" />}
                  onClick={() => void nextCodeQuery.refetch()}
                >
                  Re-suggest code
                </Button>
              )}
            </div>
            <TextField
              label="First name"
              required
              error={errors.first_name?.message}
              {...register('first_name')}
            />
            <TextField
              label="Middle name"
              error={errors.middle_name?.message}
              {...register('middle_name')}
            />
            <TextField
              label="Last name"
              error={errors.last_name?.message}
              {...register('last_name')}
            />
            <ControlledDate
              control={control}
              name="date_of_birth"
              label="Date of birth"
              error={errors.date_of_birth?.message}
            />
            <SelectField
              label="Gender"
              error={errors.gender?.message}
              options={[
                { value: '', label: 'Not specified' },
                { value: 'female', label: 'Female' },
                { value: 'male', label: 'Male' },
                { value: 'other', label: 'Other' },
                { value: 'not_disclosed', label: 'Prefer not to say' },
              ]}
              {...register('gender')}
            />
            <SelectField
              label="Marital status"
              error={errors.marital_status?.message}
              options={[
                { value: '', label: 'Not specified' },
                { value: 'single', label: 'Single' },
                { value: 'married', label: 'Married' },
                { value: 'other', label: 'Other' },
                { value: 'not_disclosed', label: 'Prefer not to say' },
              ]}
              {...register('marital_status')}
            />
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Employment" />
          <CardBody className="grid gap-5 sm:grid-cols-2">
            <SelectField
              label="Employment type"
              required
              error={errors.employment_type?.message}
              options={EMPLOYMENT_TYPES.map((value) => ({ value, label: humanise(value) }))}
              {...register('employment_type')}
            />
            <SelectField
              label="Employment status"
              required
              hint="Record an exit through the exit action, not here."
              error={errors.employment_status?.message}
              options={EMPLOYMENT_STATUSES.filter((value) => value !== 'exited').map(
                (value) => ({ value, label: humanise(value) }),
              )}
              {...register('employment_status')}
            />
            <ControlledDate
              control={control}
              name="date_of_joining"
              label="Date of joining"
              required
              clearable={false}
              error={errors.date_of_joining?.message}
            />
            <ControlledDate
              control={control}
              name="probation_end_date"
              label="Probation end date"
              error={errors.probation_end_date?.message}
            />
            <SelectField
              label="Department"
              error={errors.department_id?.message}
              options={[
                { value: '', label: 'Unassigned' },
                ...(departmentsQuery.data ?? []).map((row) => ({
                  value: String(row.id),
                  label: row.name,
                })),
              ]}
              {...register('department_id')}
            />
            <SelectField
              label="Designation"
              error={errors.designation_id?.message}
              options={[
                { value: '', label: 'Unassigned' },
                ...(designationsQuery.data ?? []).map((row) => ({
                  value: String(row.id),
                  label: row.name,
                })),
              ]}
              {...register('designation_id')}
            />
            <SelectField
              label="Work location"
              hint="Determines the timezone used for this employee's attendance dates."
              error={errors.work_location_id?.message}
              options={[
                { value: '', label: 'Unassigned' },
                ...(locationsQuery.data ?? []).map((row) => ({
                  value: String(row.id),
                  label: `${row.name} (${row.timezone})`,
                })),
              ]}
              {...register('work_location_id')}
            />
            <SelectField
              label="Reporting manager"
              hint="Reviews this employee's leave and attendance requests."
              error={errors.reporting_manager_id?.message}
              options={[
                { value: '', label: 'Not assigned' },
                ...managers.map((row) => ({
                  value: String(row.id),
                  label: `${row.full_name} (${row.employee_code})`,
                })),
              ]}
              {...register('reporting_manager_id')}
            />
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Contact" />
          <CardBody className="grid gap-5 sm:grid-cols-2">
            <TextField
              label="Work email"
              type="email"
              error={errors.work_email?.message}
              {...register('work_email')}
            />
            <TextField
              label="Work phone"
              error={errors.work_phone?.message}
              {...register('work_phone')}
            />
            <TextField
              label="Personal email"
              type="email"
              error={errors.personal_email?.message}
              {...register('personal_email')}
            />
            <TextField
              label="Personal phone"
              error={errors.personal_phone?.message}
              {...register('personal_phone')}
            />
            <TextField
              label="Address"
              error={errors.address_line1?.message}
              {...register('address_line1')}
            />
            <TextField label="City" error={errors.city?.message} {...register('city')} />
            <TextField label="State" error={errors.state?.message} {...register('state')} />
            <TextField
              label="Postal code"
              error={errors.postal_code?.message}
              {...register('postal_code')}
            />
            <TextField
              label="Emergency contact name"
              error={errors.emergency_contact_name?.message}
              {...register('emergency_contact_name')}
            />
            <TextField
              label="Emergency contact phone"
              error={errors.emergency_contact_phone?.message}
              {...register('emergency_contact_phone')}
            />
            <TextField
              label="Relationship"
              error={errors.emergency_contact_relation?.message}
              {...register('emergency_contact_relation')}
            />
          </CardBody>
        </Card>

        {isEdit && (
          <Card>
            <CardHeader
              title="Change record"
              description="Recorded against the new employment-history period and in the audit log."
            />
            <CardBody className="grid gap-5 sm:grid-cols-2">
              <ControlledDate
                control={control}
                name="effective_from"
                label="Effective from"
                hint="When the change takes effect. Defaults to today."
                error={errors.effective_from?.message}
              />
              <TextField
                label="Reason"
                error={errors.change_reason?.message}
                {...register('change_reason')}
              />
            </CardBody>
          </Card>
        )}

        <Card>
          <CardHeader title="Notes" />
          <CardBody>
            <TextAreaField
              label="Internal notes"
              rows={3}
              hint="Visible to HR only."
              error={errors.notes?.message}
              {...register('notes')}
            />
          </CardBody>
        </Card>

        <Alert variant="info" title="Statutory and bank details are entered separately">
          They sit behind their own permission and are encrypted at rest, so they are not part
          of this form. Add them from the employee's page after saving.
        </Alert>

        <div className="flex flex-wrap justify-end gap-2">
          <Button
            type="button"
            variant="secondary"
            onClick={() => navigate(isEdit ? `/employees/${id}` : '/employees')}
          >
            Cancel
          </Button>
          <Button
            type="submit"
            isLoading={isSubmitting || mutation.isPending}
            leadingIcon={<Save className="size-4" aria-hidden="true" />}
          >
            {isEdit ? 'Save changes' : 'Create employee'}
          </Button>
        </div>
      </form>
    </div>
  );
}
