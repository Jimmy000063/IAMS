import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowLeft,
  Building2,
  Clock,
  CreditCard,
  Eye,
  FileText,
  Landmark,
  LogOut,
  Pencil,
  ShieldAlert,
} from 'lucide-react';
import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';

import { Button } from '@/components/ui/Button';
import { DatePicker } from '@/components/ui/DatePicker';
import { SelectField, TextAreaField } from '@/components/ui/Field';
import { Alert, ErrorPanel, LoadingPanel, SkeletonRows } from '@/components/ui/Feedback';
import { Modal } from '@/components/ui/Modal';
import {
  Badge,
  Card,
  CardBody,
  CardHeader,
  PageHeader,
  TableShell,
  Td,
  Th,
} from '@/components/ui/Surface';
import {
  EXIT_TYPES,
  employeeKeys,
  exitEmployee,
  fetchBankAccounts,
  fetchEmployee,
  fetchEmploymentHistory,
  fetchStatutoryDetails,
} from '@/features/employees/api';
import {
  DefinitionList,
  EmploymentStatusBadge,
  EmploymentTypeBadge,
  Value,
} from '@/features/employees/shared';
import { useAuth } from '@/lib/auth';
import { useToast } from '@/lib/toast';
import { formatDate, formatDateTime, humanise } from '@/lib/utils';

export function EmployeeDetailPage() {
  const { employeeId } = useParams<{ employeeId: string }>();
  const id = Number(employeeId);
  const { can } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [showExit, setShowExit] = useState(false);
  const [revealRestricted, setRevealRestricted] = useState(false);

  const canManage = can('employee.manage');
  const canSeeRestricted = can('employee.read_sensitive');

  const employeeQuery = useQuery({
    queryKey: employeeKeys.detail(id),
    queryFn: () => fetchEmployee(id),
    enabled: Number.isFinite(id),
  });

  const historyQuery = useQuery({
    queryKey: employeeKeys.history(id),
    queryFn: () => fetchEmploymentHistory(id),
    enabled: Number.isFinite(id),
  });

  /*
   * Restricted data is fetched only when explicitly revealed, not on page load.
   * Every read is audited server-side, so loading it eagerly would fill the
   * audit trail with accesses nobody intended and make a real investigation
   * harder to read.
   */
  const statutoryQuery = useQuery({
    queryKey: employeeKeys.statutory(id),
    queryFn: () => fetchStatutoryDetails(id),
    enabled: Number.isFinite(id) && canSeeRestricted && revealRestricted,
  });

  const bankQuery = useQuery({
    queryKey: employeeKeys.bank(id),
    queryFn: () => fetchBankAccounts(id),
    enabled: Number.isFinite(id) && canSeeRestricted && revealRestricted,
  });

  const exitMutation = useMutation({
    mutationFn: (body: Record<string, unknown>) => exitEmployee(id, body),
    onSuccess: async () => {
      toast.success('Exit recorded', 'The record is retained and the account deactivated.');
      setShowExit(false);
      await queryClient.invalidateQueries({ queryKey: employeeKeys.all });
    },
    onError: (error) => toast.apiError(error, 'Could not record the exit'),
  });

  if (employeeQuery.isPending) return <LoadingPanel label="Loading employee…" />;

  if (employeeQuery.isError) {
    return (
      <div>
        <PageHeader title="Employee" />
        <ErrorPanel
          error={employeeQuery.error}
          onRetry={() => void employeeQuery.refetch()}
          title="Could not load this employee"
        />
        <Button variant="secondary" className="mt-4" onClick={() => navigate('/employees')}>
          Back to directory
        </Button>
      </div>
    );
  }

  const employee = employeeQuery.data;
  const hasExited = employee.employment_status === 'exited';

  return (
    <div>
      <Link
        to="/employees"
        className="text-ink-soft hover:text-ink mb-4 inline-flex items-center gap-1.5 text-sm"
      >
        <ArrowLeft className="size-4" aria-hidden="true" />
        Back to directory
      </Link>

      <PageHeader
        title={employee.full_name}
        description={`${employee.employee_code}${
          employee.designation_name ? ` · ${employee.designation_name}` : ''
        }`}
        action={
          canManage && !hasExited ? (
            <div className="flex flex-wrap gap-2">
              <Link to={`/employees/${employee.id}/edit`}>
                <Button
                  variant="secondary"
                  leadingIcon={<Pencil className="size-4" aria-hidden="true" />}
                >
                  Edit
                </Button>
              </Link>
              <Button
                variant="danger"
                leadingIcon={<LogOut className="size-4" aria-hidden="true" />}
                onClick={() => setShowExit(true)}
              >
                Record exit
              </Button>
            </div>
          ) : undefined
        }
      />

      {hasExited && (
        <div className="mb-6">
          <Alert variant="info" title="This employee has left">
            Exited on {formatDate(employee.date_of_exit)}
            {employee.exit_type ? ` (${humanise(employee.exit_type)})` : ''}. The record and all
            attendance, leave and payroll history are retained.
          </Alert>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <EmploymentStatusBadge status={employee.employment_status} />
        <EmploymentTypeBadge type={employee.employment_type} />
        {employee.direct_report_count > 0 && (
          <Badge tone="brand">
            {employee.direct_report_count} direct report
            {employee.direct_report_count === 1 ? '' : 's'}
          </Badge>
        )}
        {employee.linked_user_id === null && <Badge tone="warning">No sign-in account</Badge>}
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <Card>
            <CardHeader title="Employment" />
            <CardBody>
              <DefinitionList
                items={[
                  { label: 'Employee code', value: employee.employee_code },
                  { label: 'Department', value: <Value>{employee.department_name}</Value> },
                  {
                    label: 'Designation',
                    value: <Value>{employee.designation_name}</Value>,
                  },
                  {
                    label: 'Work location',
                    value: (
                      <Value>
                        {employee.work_location_name
                          ? `${employee.work_location_name} (${employee.work_location_timezone})`
                          : null}
                      </Value>
                    ),
                  },
                  {
                    label: 'Reporting manager',
                    value: employee.reporting_manager_id ? (
                      <Link
                        to={`/employees/${employee.reporting_manager_id}`}
                        className="text-brand-600 underline-offset-4 hover:underline"
                      >
                        {employee.reporting_manager_name} ({employee.reporting_manager_code})
                      </Link>
                    ) : (
                      <span className="text-warning-700">Not assigned</span>
                    ),
                  },
                  { label: 'Date of joining', value: formatDate(employee.date_of_joining) },
                  {
                    label: 'Probation ends',
                    value: employee.probation_end_date
                      ? formatDate(employee.probation_end_date)
                      : '—',
                  },
                  {
                    label: 'Confirmed on',
                    value: employee.date_of_confirmation
                      ? formatDate(employee.date_of_confirmation)
                      : '—',
                  },
                ]}
              />
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Contact" />
            <CardBody>
              <DefinitionList
                items={[
                  { label: 'Work email', value: <Value>{employee.work_email}</Value> },
                  { label: 'Work phone', value: <Value>{employee.work_phone}</Value> },
                  {
                    label: 'Personal email',
                    value: <Value>{employee.personal_email}</Value>,
                  },
                  {
                    label: 'Personal phone',
                    value: <Value>{employee.personal_phone}</Value>,
                  },
                  {
                    label: 'Address',
                    value: (
                      <Value>
                        {[
                          employee.address_line1,
                          employee.address_line2,
                          employee.city,
                          employee.state,
                          employee.postal_code,
                        ]
                          .filter(Boolean)
                          .join(', ') || null}
                      </Value>
                    ),
                  },
                  {
                    label: 'Emergency contact',
                    value: (
                      <Value>
                        {employee.emergency_contact_name
                          ? `${employee.emergency_contact_name}${
                              employee.emergency_contact_relation
                                ? ` (${employee.emergency_contact_relation})`
                                : ''
                            }${
                              employee.emergency_contact_phone
                                ? ` · ${employee.emergency_contact_phone}`
                                : ''
                            }`
                          : null}
                      </Value>
                    ),
                  },
                ]}
              />
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title="Employment history"
              description="Effective-dated periods. A transfer opens a new period rather than overwriting the old one."
            />
            <CardBody>
              {historyQuery.isPending && <SkeletonRows rows={3} />}
              {historyQuery.isError && (
                <ErrorPanel error={historyQuery.error} title="Could not load history" />
              )}
              {historyQuery.isSuccess && (
                <TableShell caption="Employment history">
                  <thead>
                    <tr>
                      <Th>Period</Th>
                      <Th>Change</Th>
                      <Th className="hidden sm:table-cell">Reason</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {historyQuery.data.map((entry) => (
                      <tr key={entry.id}>
                        <Td className="numeric whitespace-nowrap">
                          {formatDate(entry.effective_from)} –{' '}
                          {entry.effective_to ? (
                            formatDate(entry.effective_to)
                          ) : (
                            <Badge tone="success">Current</Badge>
                          )}
                        </Td>
                        <Td>
                          <Badge tone="neutral">{humanise(entry.change_type)}</Badge>
                        </Td>
                        <Td className="text-ink-soft hidden text-sm sm:table-cell">
                          <Value>{entry.change_reason}</Value>
                        </Td>
                      </tr>
                    ))}
                  </tbody>
                </TableShell>
              )}
            </CardBody>
          </Card>
        </div>

        <div className="space-y-6">
          <Card>
            <CardHeader title="Personal" />
            <CardBody>
              <DefinitionList
                columns={1}
                items={[
                  {
                    label: 'Date of birth',
                    value: employee.date_of_birth ? formatDate(employee.date_of_birth) : '—',
                  },
                  {
                    label: 'Gender',
                    value: <Value>{employee.gender ? humanise(employee.gender) : null}</Value>,
                  },
                  {
                    label: 'Marital status',
                    value: (
                      <Value>
                        {employee.marital_status ? humanise(employee.marital_status) : null}
                      </Value>
                    ),
                  },
                ]}
              />
            </CardBody>
          </Card>

          {canSeeRestricted && (
            <Card>
              <CardHeader
                title="Restricted details"
                description="Statutory identifiers and bank details."
              />
              <CardBody className="space-y-4">
                {!revealRestricted ? (
                  <>
                    <Alert variant="warning" title="Access is recorded" icon={ShieldAlert}>
                      Viewing these details writes an entry to the audit log against your
                      account. They are loaded only when you ask for them.
                    </Alert>
                    <Button
                      variant="secondary"
                      fullWidth
                      leadingIcon={<Eye className="size-4" aria-hidden="true" />}
                      onClick={() => setRevealRestricted(true)}
                    >
                      Show restricted details
                    </Button>
                  </>
                ) : (
                  <>
                    <div>
                      <p className="text-ink mb-2 flex items-center gap-1.5 text-sm font-medium">
                        <Landmark className="size-4" aria-hidden="true" />
                        Statutory
                      </p>
                      {statutoryQuery.isPending && <SkeletonRows rows={2} />}
                      {statutoryQuery.isError && (
                        <ErrorPanel
                          error={statutoryQuery.error}
                          title="Could not load statutory details"
                        />
                      )}
                      {statutoryQuery.isSuccess && (
                        <DefinitionList
                          columns={1}
                          items={[
                            {
                              label: 'PF UAN',
                              value: <Value>{statutoryQuery.data.pf_uan}</Value>,
                            },
                            {
                              label: 'ESI number',
                              value: <Value>{statutoryQuery.data.esi_number}</Value>,
                            },
                            {
                              label: 'PAN',
                              value: (
                                <span className="font-mono">
                                  <Value>{statutoryQuery.data.pan_masked}</Value>
                                </span>
                              ),
                            },
                            {
                              label: 'Aadhaar (last 4)',
                              value: (
                                <span className="font-mono">
                                  <Value>{statutoryQuery.data.aadhaar_last4}</Value>
                                </span>
                              ),
                            },
                            {
                              label: 'PF opted in',
                              value:
                                statutoryQuery.data.pf_opted_in === null ? (
                                  <Badge tone="warning">Not determined</Badge>
                                ) : statutoryQuery.data.pf_opted_in ? (
                                  'Yes'
                                ) : (
                                  'No'
                                ),
                            },
                            {
                              label: 'Professional tax state',
                              value: <Value>{statutoryQuery.data.pt_state}</Value>,
                            },
                          ]}
                        />
                      )}
                    </div>

                    <div className="border-line border-t pt-4">
                      <p className="text-ink mb-2 flex items-center gap-1.5 text-sm font-medium">
                        <CreditCard className="size-4" aria-hidden="true" />
                        Bank accounts
                      </p>
                      {bankQuery.isPending && <SkeletonRows rows={2} />}
                      {bankQuery.isSuccess &&
                        (bankQuery.data.length === 0 ? (
                          <p className="text-ink-muted text-sm">No bank account recorded.</p>
                        ) : (
                          <ul className="space-y-3">
                            {bankQuery.data.map((account) => (
                              <li key={account.id} className="bg-sunken rounded-md p-3 text-sm">
                                <div className="flex items-center justify-between gap-2">
                                  <span className="font-medium">{account.bank_name}</span>
                                  {account.is_primary && <Badge tone="success">Primary</Badge>}
                                </div>
                                <p className="text-ink-soft mt-1 font-mono text-xs">
                                  {account.account_number_masked} · {account.ifsc_code}
                                </p>
                                <p className="text-ink-muted text-xs">
                                  {account.account_holder_name}
                                </p>
                              </li>
                            ))}
                          </ul>
                        ))}
                      <p className="text-ink-muted mt-2 text-xs">
                        Account numbers are encrypted at rest and never returned in full.
                      </p>
                    </div>
                  </>
                )}
              </CardBody>
            </Card>
          )}

          <Card>
            <CardHeader title="Record" />
            <CardBody>
              <DefinitionList
                columns={1}
                items={[
                  { label: 'Created', value: formatDateTime(employee.created_at) },
                  { label: 'Last updated', value: formatDateTime(employee.updated_at) },
                  {
                    label: 'Sign-in account',
                    value: employee.linked_user_id ? (
                      `Linked (user #${employee.linked_user_id})`
                    ) : (
                      <span className="text-warning-700">Not linked</span>
                    ),
                  },
                ]}
              />
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Not yet available" />
            <CardBody>
              <ul className="text-ink-soft space-y-2 text-sm">
                <li className="flex items-center gap-2">
                  <Clock className="size-4 shrink-0" aria-hidden="true" />
                  Attendance and shifts — Milestone 3
                </li>
                <li className="flex items-center gap-2">
                  <FileText className="size-4 shrink-0" aria-hidden="true" />
                  Leave balances — Milestone 5
                </li>
                <li className="flex items-center gap-2">
                  <Building2 className="size-4 shrink-0" aria-hidden="true" />
                  Salary and payslips — Milestone 8
                </li>
              </ul>
            </CardBody>
          </Card>
        </div>
      </div>

      <ExitDialog
        open={showExit}
        employeeName={employee.full_name}
        minDate={employee.date_of_joining}
        isSubmitting={exitMutation.isPending}
        onCancel={() => setShowExit(false)}
        onSubmit={(body) => exitMutation.mutate(body)}
      />
    </div>
  );
}

function ExitDialog({
  open,
  employeeName,
  minDate,
  isSubmitting,
  onCancel,
  onSubmit,
}: {
  open: boolean;
  employeeName: string;
  minDate: string;
  isSubmitting: boolean;
  onCancel: () => void;
  onSubmit: (body: Record<string, unknown>) => void;
}) {
  const [dateOfExit, setDateOfExit] = useState('');
  const [exitType, setExitType] = useState<string>('resignation');
  const [reason, setReason] = useState('');
  const [lastWorkingDay, setLastWorkingDay] = useState('');
  const [deactivate, setDeactivate] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const submit = () => {
    if (!dateOfExit) {
      setError('An exit date is required.');
      return;
    }
    setError(null);
    onSubmit({
      date_of_exit: dateOfExit,
      exit_type: exitType,
      exit_reason: reason.trim() || null,
      last_working_day: lastWorkingDay || null,
      deactivate_account: deactivate,
    });
  };

  return (
    <Modal
      open={open}
      onClose={onCancel}
      title={`Record exit for ${employeeName}`}
      description="Nothing is deleted. Attendance, leave and payroll history are retained."
      footer={
        <>
          <Button variant="secondary" onClick={onCancel} disabled={isSubmitting}>
            Cancel
          </Button>
          <Button variant="danger" onClick={submit} isLoading={isSubmitting}>
            Record exit
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {error && (
          <Alert variant="danger" title="Check this form">
            {error}
          </Alert>
        )}
        <DatePicker
          label="Date of exit"
          required
          min={minDate}
          value={dateOfExit}
          onChange={setDateOfExit}
          clearable={false}
        />
        <DatePicker
          label="Last working day"
          min={minDate}
          hint="Defaults to the exit date if left blank."
          value={lastWorkingDay}
          onChange={setLastWorkingDay}
        />
        <SelectField
          label="Exit type"
          value={exitType}
          onChange={(event) => setExitType(event.target.value)}
          options={EXIT_TYPES.map((value) => ({ value, label: humanise(value) }))}
        />
        <TextAreaField
          label="Reason"
          rows={2}
          value={reason}
          onChange={(event) => setReason(event.target.value)}
        />
        <label className="flex items-start gap-2.5 text-sm">
          <input
            type="checkbox"
            className="border-line-strong accent-brand-600 mt-0.5 size-4 rounded"
            checked={deactivate}
            onChange={(event) => setDeactivate(event.target.checked)}
          />
          <span className="text-ink-soft">
            Deactivate their sign-in account. Recommended — the employee record stays intact
            either way.
          </span>
        </label>
      </div>
    </Modal>
  );
}
