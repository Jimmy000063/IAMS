import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Lock, Save } from 'lucide-react';
import { useEffect } from 'react';
import { useForm } from 'react-hook-form';
import { z } from 'zod';

import { Button } from '@/components/ui/Button';
import { SelectField, TextField } from '@/components/ui/Field';
import { Alert, ErrorPanel, LoadingPanel } from '@/components/ui/Feedback';
import { Card, CardBody, CardHeader, PageHeader } from '@/components/ui/Surface';
import { employeeKeys, fetchMyProfile, updateMyProfile } from '@/features/employees/api';
import { DefinitionList, EmploymentStatusBadge, Value } from '@/features/employees/shared';
import { ApiError } from '@/lib/api';
import { useToast } from '@/lib/toast';
import { formatDate, humanise } from '@/lib/utils';

/**
 * Only the fields an employee may change about themselves.
 *
 * Mirrors ``SELF_EDITABLE_FIELDS`` on the server, which rejects anything else
 * outright rather than ignoring it. The read-only fields are still shown below,
 * so the employee can see what HR holds and ask for a correction.
 */
const schema = z.object({
  personal_email: z.string().trim().email('Enter a valid email.').optional().or(z.literal('')),
  personal_phone: z.string().trim().max(25).optional().or(z.literal('')),
  address_line1: z.string().trim().max(200).optional().or(z.literal('')),
  address_line2: z.string().trim().max(200).optional().or(z.literal('')),
  city: z.string().trim().max(80).optional().or(z.literal('')),
  state: z.string().trim().max(80).optional().or(z.literal('')),
  postal_code: z.string().trim().max(20).optional().or(z.literal('')),
  marital_status: z.string().optional().or(z.literal('')),
  emergency_contact_name: z.string().trim().max(120).optional().or(z.literal('')),
  emergency_contact_phone: z.string().trim().max(25).optional().or(z.literal('')),
  emergency_contact_relation: z.string().trim().max(50).optional().or(z.literal('')),
});

type FormValues = z.infer<typeof schema>;

export function MyProfilePage() {
  const toast = useToast();
  const queryClient = useQueryClient();

  const profileQuery = useQuery({
    queryKey: employeeKeys.me(),
    queryFn: fetchMyProfile,
    // 409 means the account has no employee record; retrying cannot fix it.
    retry: false,
  });

  const {
    register,
    handleSubmit,
    reset,
    setError,
    formState: { errors, isSubmitting, isDirty },
  } = useForm<FormValues>({ resolver: zodResolver(schema) });

  useEffect(() => {
    if (!profileQuery.data) return;
    const e = profileQuery.data;
    reset({
      personal_email: e.personal_email ?? '',
      personal_phone: e.personal_phone ?? '',
      address_line1: e.address_line1 ?? '',
      address_line2: e.address_line2 ?? '',
      city: e.city ?? '',
      state: e.state ?? '',
      postal_code: e.postal_code ?? '',
      marital_status: e.marital_status ?? '',
      emergency_contact_name: e.emergency_contact_name ?? '',
      emergency_contact_phone: e.emergency_contact_phone ?? '',
      emergency_contact_relation: e.emergency_contact_relation ?? '',
    });
  }, [profileQuery.data, reset]);

  const mutation = useMutation({
    mutationFn: (payload: Record<string, unknown>) => updateMyProfile(payload),
    onSuccess: async () => {
      toast.success('Profile updated');
      await queryClient.invalidateQueries({ queryKey: employeeKeys.me() });
    },
    onError: (error) => {
      if (error instanceof ApiError) {
        let matched = false;
        for (const [field, message] of Object.entries(error.fieldErrors)) {
          if (field in schema.shape) {
            setError(field as keyof FormValues, { message });
            matched = true;
          }
        }
        if (!matched) toast.apiError(error, 'Could not update your profile');
      } else {
        toast.apiError(error, 'Could not update your profile');
      }
    },
  });

  if (profileQuery.isPending) return <LoadingPanel label="Loading your profile…" />;

  if (profileQuery.isError) {
    const isUnlinked =
      profileQuery.error instanceof ApiError && profileQuery.error.status === 409;
    return (
      <div>
        <PageHeader title="My profile" />
        {isUnlinked ? (
          <Alert variant="info" title="No employee record yet">
            {profileQuery.error instanceof ApiError
              ? profileQuery.error.message
              : 'Your account is not linked to an employee record.'}
          </Alert>
        ) : (
          <ErrorPanel
            error={profileQuery.error}
            onRetry={() => void profileQuery.refetch()}
            title="Could not load your profile"
          />
        )}
      </div>
    );
  }

  const employee = profileQuery.data;

  return (
    <div>
      <PageHeader
        title="My profile"
        description="Keep your contact and emergency details up to date."
      />

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader
            title="Your details"
            description="These are the only fields you can change yourself."
          />
          <CardBody>
            <form
              onSubmit={handleSubmit((values) => {
                // Send only what changed; an empty string means "clear it", so
                // it is passed through rather than dropped.
                mutation.mutate(values as Record<string, unknown>);
              })}
              className="space-y-5"
              noValidate
            >
              <div className="grid gap-5 sm:grid-cols-2">
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
                <div className="hidden sm:block" />
                <TextField
                  label="Address line 1"
                  error={errors.address_line1?.message}
                  {...register('address_line1')}
                />
                <TextField
                  label="Address line 2"
                  error={errors.address_line2?.message}
                  {...register('address_line2')}
                />
                <TextField label="City" error={errors.city?.message} {...register('city')} />
                <TextField label="State" error={errors.state?.message} {...register('state')} />
                <TextField
                  label="Postal code"
                  error={errors.postal_code?.message}
                  {...register('postal_code')}
                />
              </div>

              <div className="border-line border-t pt-5">
                <p className="text-ink mb-4 text-sm font-medium">Emergency contact</p>
                <div className="grid gap-5 sm:grid-cols-3">
                  <TextField
                    label="Name"
                    error={errors.emergency_contact_name?.message}
                    {...register('emergency_contact_name')}
                  />
                  <TextField
                    label="Phone"
                    error={errors.emergency_contact_phone?.message}
                    {...register('emergency_contact_phone')}
                  />
                  <TextField
                    label="Relationship"
                    error={errors.emergency_contact_relation?.message}
                    {...register('emergency_contact_relation')}
                  />
                </div>
              </div>

              <div className="flex justify-end">
                <Button
                  type="submit"
                  disabled={!isDirty}
                  isLoading={isSubmitting || mutation.isPending}
                  leadingIcon={<Save className="size-4" aria-hidden="true" />}
                >
                  Save changes
                </Button>
              </div>
            </form>
          </CardBody>
        </Card>

        <div className="space-y-6">
          <Card>
            <CardHeader title="Held by HR" />
            <CardBody className="space-y-4">
              <Alert variant="info" title="Ask HR to change these" icon={Lock}>
                Position, pay and reporting details are maintained by HR so that history stays
                accurate.
              </Alert>
              <DefinitionList
                columns={1}
                items={[
                  { label: 'Employee code', value: employee.employee_code },
                  { label: 'Name', value: employee.full_name },
                  { label: 'Work email', value: <Value>{employee.work_email}</Value> },
                  { label: 'Department', value: <Value>{employee.department_name}</Value> },
                  {
                    label: 'Designation',
                    value: <Value>{employee.designation_name}</Value>,
                  },
                  {
                    label: 'Work location',
                    value: <Value>{employee.work_location_name}</Value>,
                  },
                  {
                    label: 'Reporting manager',
                    value: <Value>{employee.reporting_manager_name}</Value>,
                  },
                  {
                    label: 'Employment type',
                    value: humanise(employee.employment_type),
                  },
                  {
                    label: 'Status',
                    value: <EmploymentStatusBadge status={employee.employment_status} />,
                  },
                  { label: 'Date of joining', value: formatDate(employee.date_of_joining) },
                ]}
              />
            </CardBody>
          </Card>
        </div>
      </div>
    </div>
  );
}
