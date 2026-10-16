/**
 * Holiday calendar administration.
 *
 * Publishing is kept visibly separate from editing, because it marks those
 * dates as non-working for everyone in scope at once. A draft calendar affects
 * nothing, which is what makes it safe to prepare a year's list and check it.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CalendarPlus, CircleAlert, Plus, Send } from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/Button';
import { DatePicker } from '@/components/ui/DatePicker';
import { CheckboxField, SelectField, TextAreaField, TextField } from '@/components/ui/Field';
import { Alert, EmptyState, ErrorPanel, SkeletonRows } from '@/components/ui/Feedback';
import { Modal } from '@/components/ui/Modal';
import {
  Badge,
  Card,
  CardBody,
  CardHeader,
  TableShell,
  Td,
  Th,
  type BadgeTone,
} from '@/components/ui/Surface';
import {
  addHoliday,
  createCalendar,
  fetchCalendarHolidays,
  fetchCalendars,
  holidayKeys,
  importHolidays,
  publishCalendar,
  removeHoliday,
  unpublishCalendar,
  type HolidayCalendar,
  type HolidayType,
} from '@/features/holidays/api';
import { fetchWorkLocations, orgKeys } from '@/features/employees/api';
import { formatIsoLong, todayIso, type IsoDate } from '@/lib/dates';
import { useToast } from '@/lib/toast';

const TYPE_TONES: Record<string, BadgeTone> = {
  public: 'brand',
  company: 'accent',
  optional: 'warning',
  restricted: 'warning',
};

const TYPE_OPTIONS = [
  { value: 'public', label: 'Public - applies automatically' },
  { value: 'company', label: 'Company - applies automatically' },
  { value: 'restricted', label: 'Restricted - the employee must choose it' },
  { value: 'optional', label: 'Optional - the employee must choose it' },
];

export function HolidayAdminPanel() {
  const toast = useToast();
  const queryClient = useQueryClient();

  const currentYear = Number(todayIso().slice(0, 4));
  const [year, setYear] = useState(String(currentYear));
  const [selected, setSelected] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const [addingTo, setAddingTo] = useState<HolidayCalendar | null>(null);
  const [importingTo, setImportingTo] = useState<HolidayCalendar | null>(null);
  const [withdrawing, setWithdrawing] = useState<HolidayCalendar | null>(null);
  const [reason, setReason] = useState('');

  const calendars = useQuery({
    queryKey: holidayKeys.calendars({ year }),
    queryFn: () => fetchCalendars({ year: Number(year), include_inactive: true }),
  });

  const holidays = useQuery({
    queryKey: holidayKeys.calendarHolidays(selected ?? 0),
    queryFn: () => fetchCalendarHolidays(selected as number),
    enabled: selected !== null,
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: holidayKeys.all });
  };

  const publish = useMutation({
    mutationFn: (calendarId: number) => publishCalendar(calendarId),
    onSuccess: (calendar) => {
      invalidate();
      toast.success(
        `${calendar.code} published. Those dates are now non-working for everyone in scope.`,
        'Attendance already computed for them is not rewritten automatically.',
      );
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  const withdraw = useMutation({
    mutationFn: () => unpublishCalendar(withdrawing!.id, reason),
    onSuccess: () => {
      setWithdrawing(null);
      setReason('');
      invalidate();
      toast.success('Withdrawn. Those dates are working days again.');
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  const drop = useMutation({
    mutationFn: (holidayId: number) => removeHoliday(holidayId),
    onSuccess: () => {
      invalidate();
      toast.success('Removed.');
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  if (calendars.isError) {
    return <ErrorPanel error={calendars.error} onRetry={() => void calendars.refetch()} />;
  }

  return (
    <div className="space-y-5">
      <Alert variant="warning" title="No holidays are created for you">
        Dates vary by year, state and company. An invented one would mark a working day as
        non-working for everyone on that calendar, in attendance and in payroll, so this system
        ships none - you publish each year's list.
      </Alert>

      <Card>
        <CardHeader
          title="Holiday calendars"
          description="A calendar with no scope applies to everyone. Scoped calendars add to it rather than replacing it, so a regional list supplements the national one."
          action={
            <div className="flex flex-wrap items-end gap-3">
              <div className="w-28">
                <SelectField
                  label="Year"
                  value={year}
                  onChange={(event) => {
                    setYear(event.target.value);
                    setSelected(null);
                  }}
                  options={[currentYear - 1, currentYear, currentYear + 1].map((item) => ({
                    value: String(item),
                    label: String(item),
                  }))}
                />
              </div>
              <Button onClick={() => setCreating(true)}>
                <Plus className="size-4" aria-hidden="true" />
                Add calendar
              </Button>
            </div>
          }
        />
        <CardBody>
          {calendars.isPending ? (
            <SkeletonRows rows={3} />
          ) : calendars.data.length === 0 ? (
            <EmptyState
              icon={CalendarPlus}
              title={`No calendar for ${year}`}
              description="Create one, add the dates, then publish it."
              action={<Button onClick={() => setCreating(true)}>Add calendar</Button>}
            />
          ) : (
            <TableShell caption="Holiday calendars">
              <thead>
                <tr>
                  <Th>Calendar</Th>
                  <Th>Scope</Th>
                  <Th>Holidays</Th>
                  <Th>Status</Th>
                  <Th className="text-right">Actions</Th>
                </tr>
              </thead>
              <tbody>
                {calendars.data.map((calendar) => (
                  <tr
                    key={calendar.id}
                    className={selected === calendar.id ? 'bg-brand-50/50' : undefined}
                  >
                    <Td>
                      <button
                        type="button"
                        onClick={() =>
                          setSelected(selected === calendar.id ? null : calendar.id)
                        }
                        className="text-brand-700 font-medium underline underline-offset-2"
                        aria-expanded={selected === calendar.id}
                      >
                        {calendar.code}
                      </button>
                      <p className="text-ink-soft text-xs">{calendar.name}</p>
                    </Td>
                    <Td className="text-sm">
                      {calendar.work_location_id === null && calendar.employee_group_id === null
                        ? 'Everyone'
                        : [
                            calendar.work_location_id !== null
                              ? `location #${calendar.work_location_id}`
                              : null,
                            calendar.employee_group_id !== null
                              ? `group #${calendar.employee_group_id}`
                              : null,
                          ]
                            .filter(Boolean)
                            .join(' and ')}
                    </Td>
                    <Td className="tabular-nums">
                      {calendar.holiday_count}
                      {calendar.electable_count > 0 && (
                        <p className="text-ink-muted text-xs">
                          {calendar.electable_count} optional
                        </p>
                      )}
                    </Td>
                    <Td>
                      {calendar.is_published ? (
                        <Badge tone="success">Published</Badge>
                      ) : (
                        <Badge tone="neutral">Draft</Badge>
                      )}
                      {!calendar.is_active && (
                        <Badge tone="neutral" className="ml-1">
                          Inactive
                        </Badge>
                      )}
                    </Td>
                    <Td className="text-right">
                      <div className="flex flex-wrap justify-end gap-2">
                        <Button variant="ghost" size="sm" onClick={() => setAddingTo(calendar)}>
                          Add date
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setImportingTo(calendar)}
                        >
                          Import
                        </Button>
                        {calendar.is_published ? (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => {
                              setReason('');
                              setWithdrawing(calendar);
                            }}
                          >
                            Withdraw
                          </Button>
                        ) : (
                          <Button
                            size="sm"
                            onClick={() => publish.mutate(calendar.id)}
                            disabled={publish.isPending || calendar.holiday_count === 0}
                            title={
                              calendar.holiday_count === 0
                                ? 'Add at least one date first.'
                                : undefined
                            }
                          >
                            <Send className="size-4" aria-hidden="true" />
                            Publish
                          </Button>
                        )}
                      </div>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </TableShell>
          )}
        </CardBody>
      </Card>

      {selected !== null && (
        <Card>
          <CardHeader
            title="Dates on this calendar"
            description="An optional or restricted holiday is only a day off for an employee who chooses it."
          />
          <CardBody>
            {holidays.isPending ? (
              <SkeletonRows rows={3} />
            ) : (holidays.data ?? []).length === 0 ? (
              <EmptyState
                title="No dates yet"
                description="Add them one by one or import a list."
              />
            ) : (
              <TableShell caption="Holidays on this calendar">
                <thead>
                  <tr>
                    <Th>Date</Th>
                    <Th>Holiday</Th>
                    <Th>Kind</Th>
                    <Th>Region</Th>
                    <Th className="text-right">Actions</Th>
                  </tr>
                </thead>
                <tbody>
                  {(holidays.data ?? []).map((holiday) => (
                    <tr key={holiday.id}>
                      <Td className="whitespace-nowrap">
                        {formatIsoLong(holiday.holiday_date)}
                      </Td>
                      <Td>{holiday.name}</Td>
                      <Td>
                        <Badge tone={TYPE_TONES[holiday.holiday_type] ?? 'neutral'}>
                          {holiday.holiday_type}
                        </Badge>
                        {!holiday.is_paid && (
                          <Badge tone="warning" className="ml-1">
                            Unpaid
                          </Badge>
                        )}
                      </Td>
                      <Td className="text-ink-soft text-sm">{holiday.region ?? '—'}</Td>
                      <Td className="text-right">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => drop.mutate(holiday.id)}
                          disabled={drop.isPending}
                        >
                          Remove
                        </Button>
                      </Td>
                    </tr>
                  ))}
                </tbody>
              </TableShell>
            )}
          </CardBody>
        </Card>
      )}

      <CreateCalendarModal
        open={creating}
        year={Number(year)}
        onClose={() => setCreating(false)}
        onCreated={() => {
          setCreating(false);
          invalidate();
        }}
      />

      <AddHolidayModal
        calendar={addingTo}
        onClose={() => setAddingTo(null)}
        onAdded={() => {
          setAddingTo(null);
          invalidate();
        }}
      />

      <ImportModal
        calendar={importingTo}
        onClose={() => setImportingTo(null)}
        onImported={() => {
          setImportingTo(null);
          invalidate();
        }}
      />

      <Modal
        open={withdrawing !== null}
        onClose={() => setWithdrawing(null)}
        title="Withdraw this calendar"
        description="Those dates become working days again for everyone in scope, which can turn a quiet day into an absence. Recompute attendance for the affected range afterwards."
        footer={
          <>
            <Button variant="secondary" onClick={() => setWithdrawing(null)}>
              Keep it published
            </Button>
            <Button
              variant="danger"
              onClick={() => withdraw.mutate()}
              disabled={withdraw.isPending || reason.trim() === ''}
            >
              {withdraw.isPending ? 'Withdrawing…' : 'Withdraw'}
            </Button>
          </>
        }
      >
        <TextAreaField
          label="Reason"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          rows={3}
          required
        />
      </Modal>
    </div>
  );
}

function CreateCalendarModal({
  open,
  year,
  onClose,
  onCreated,
}: {
  open: boolean;
  year: number;
  onClose: () => void;
  onCreated: () => void;
}) {
  const toast = useToast();
  const [code, setCode] = useState('');
  const [name, setName] = useState('');
  const [locationId, setLocationId] = useState('');

  const locations = useQuery({
    queryKey: orgKeys.locations(),
    queryFn: () => fetchWorkLocations(false),
    enabled: open,
  });

  const create = useMutation({
    mutationFn: () =>
      createCalendar({
        code: code.trim().toUpperCase(),
        name: name.trim(),
        year,
        work_location_id: locationId ? Number(locationId) : null,
      }),
    onSuccess: () => {
      setCode('');
      setName('');
      setLocationId('');
      toast.success('Calendar created as a draft. Add the dates, then publish it.');
      onCreated();
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`Add a ${year} holiday calendar`}
      description="Created as a draft: it affects nothing until you publish it."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={() => create.mutate()}
            disabled={create.isPending || !code.trim() || !name.trim()}
          >
            {create.isPending ? 'Creating…' : 'Create calendar'}
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
            hint="Short, e.g. IN or KA."
            required
          />
          <TextField
            label="Name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            hint={`e.g. "India ${year}".`}
            required
          />
        </div>
        <SelectField
          label="Applies to"
          value={locationId}
          onChange={(event) => setLocationId(event.target.value)}
          options={[
            { value: '', label: 'Everyone' },
            ...(locations.data ?? []).map((location) => ({
              value: String(location.id),
              label: `${location.code} — ${location.name}`,
            })),
          ]}
          hint="A location-scoped calendar adds to the one that applies to everyone."
        />
      </div>
    </Modal>
  );
}

function AddHolidayModal({
  calendar,
  onClose,
  onAdded,
}: {
  calendar: HolidayCalendar | null;
  onClose: () => void;
  onAdded: () => void;
}) {
  const toast = useToast();
  const [holidayDate, setHolidayDate] = useState<IsoDate>(todayIso());
  const [name, setName] = useState('');
  const [type, setType] = useState<HolidayType>('public');
  const [region, setRegion] = useState('');
  const [isPaid, setIsPaid] = useState(true);

  const add = useMutation({
    mutationFn: () =>
      addHoliday(calendar!.id, {
        holiday_date: holidayDate,
        name: name.trim(),
        holiday_type: type,
        region: region.trim() || null,
        is_paid: isPaid,
      }),
    onSuccess: () => {
      setName('');
      setRegion('');
      toast.success('Added.');
      onAdded();
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  return (
    <Modal
      open={calendar !== null}
      onClose={onClose}
      title={calendar ? `Add a date to ${calendar.code}` : 'Add a date'}
      description={
        calendar
          ? `The date must fall in ${calendar.year}, which is the year this calendar covers.`
          : undefined
      }
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => add.mutate()} disabled={add.isPending || !name.trim()}>
            {add.isPending ? 'Adding…' : 'Add date'}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <DatePicker label="Date" value={holidayDate} onChange={setHolidayDate} />
        <TextField
          label="Name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          required
        />
        <SelectField
          label="Kind"
          value={type}
          onChange={(event) => setType(event.target.value as HolidayType)}
          options={TYPE_OPTIONS}
          hint="An optional or restricted holiday is only a day off for employees who choose it."
        />
        <TextField
          label="Region"
          value={region}
          onChange={(event) => setRegion(event.target.value)}
          hint="Optional, shown alongside the holiday."
        />
        <CheckboxField
          label="Paid"
          checked={isPaid}
          onChange={(event) => setIsPaid(event.target.checked)}
          hint="Nearly always yes. An unpaid declared closure is a real arrangement, so payroll does not assume."
        />
      </div>
    </Modal>
  );
}

function ImportModal({
  calendar,
  onClose,
  onImported,
}: {
  calendar: HolidayCalendar | null;
  onClose: () => void;
  onImported: () => void;
}) {
  const toast = useToast();
  const [text, setText] = useState('');

  const parsed = text
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [date, name, kind, region] = line.split(',').map((part) => part.trim());
      return {
        holiday_date: (date ?? '') as IsoDate,
        name: name ?? '',
        holiday_type: (kind || 'public') as HolidayType,
        ...(region ? { region } : {}),
      };
    });

  const run = useMutation({
    mutationFn: () => importHolidays(calendar!.id, parsed),
    onSuccess: (result) => {
      if (result.already_imported) {
        toast.show({
          variant: 'info',
          title: 'This exact list was already imported.',
          description: 'Nothing was added again.',
        });
      } else {
        toast.success(
          `${result.created} added, ${result.updated} updated, ${result.rejected} rejected.`,
          result.rejected > 0 ? result.problems[0]?.message : undefined,
        );
      }
      setText('');
      onImported();
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  return (
    <Modal
      open={calendar !== null}
      onClose={onClose}
      title={calendar ? `Import dates into ${calendar.code}` : 'Import dates'}
      className="max-w-2xl"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => run.mutate()} disabled={run.isPending || parsed.length === 0}>
            {run.isPending ? 'Importing…' : `Import ${parsed.length} row(s)`}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Alert variant="info" title="One holiday per line" icon={CircleAlert}>
          <code>date, name, kind, region</code> — kind and region are optional and kind defaults
          to <code>public</code>. Importing the same list twice does nothing the second time,
          and a row that cannot be read is reported without rejecting the rest.
        </Alert>
        <TextAreaField
          label="Holidays"
          value={text}
          onChange={(event) => setText(event.target.value)}
          rows={10}
          hint={`e.g. ${calendar?.year ?? 2027}-01-26, Republic Day`}
        />
      </div>
    </Modal>
  );
}
