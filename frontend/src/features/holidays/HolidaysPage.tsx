/**
 * Holidays: the published list, and the optional ones an employee chooses.
 *
 * The distinction the page has to make clear is that an optional holiday is
 * **not** a day off until it has been chosen. Showing both kinds in one list
 * with no difference would have someone plan around a day they never elected.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CalendarCheck, CalendarPlus, CircleAlert, Info } from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/Button';
import { SelectField } from '@/components/ui/Field';
import { Alert, EmptyState, ErrorPanel, SkeletonRows } from '@/components/ui/Feedback';
import {
  Badge,
  Card,
  CardBody,
  CardHeader,
  PageHeader,
  TableShell,
  Td,
  Th,
  type BadgeTone,
} from '@/components/ui/Surface';
import {
  electHoliday,
  fetchMyElectionLimits,
  fetchMyHolidays,
  fetchMyOptionalHolidays,
  holidayKeys,
  withdrawElection,
} from '@/features/holidays/api';
import { useAuth } from '@/lib/auth';
import { formatIsoLong, todayIso } from '@/lib/dates';
import { useToast } from '@/lib/toast';

const TYPE_TONES: Record<string, BadgeTone> = {
  public: 'brand',
  company: 'accent',
  optional: 'warning',
  restricted: 'warning',
};

function yearOptions(): { value: string; label: string }[] {
  const current = Number(todayIso().slice(0, 4));
  return [current - 1, current, current + 1].map((year) => ({
    value: String(year),
    label: String(year),
  }));
}

export function HolidaysPage() {
  const { can } = useAuth();
  const toast = useToast();
  const queryClient = useQueryClient();

  const [year, setYear] = useState(() => todayIso().slice(0, 4));
  const numericYear = Number(year);

  const holidays = useQuery({
    queryKey: holidayKeys.mine({ year }),
    queryFn: () => fetchMyHolidays({ start: `${year}-01-01`, end: `${year}-12-31` }),
  });

  const optional = useQuery({
    queryKey: holidayKeys.myOptional(numericYear),
    queryFn: () => fetchMyOptionalHolidays(numericYear),
    enabled: can('holiday.elect_optional'),
  });

  const limits = useQuery({
    queryKey: holidayKeys.myLimits(numericYear),
    queryFn: () => fetchMyElectionLimits(numericYear),
    enabled: can('holiday.elect_optional'),
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: holidayKeys.all });
  };

  const elect = useMutation({
    mutationFn: (holidayId: number) => electHoliday(holidayId),
    onSuccess: () => {
      invalidate();
      toast.success('Chosen. That day is now a holiday for you.');
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  const withdraw = useMutation({
    mutationFn: (holidayId: number) => withdrawElection(holidayId),
    onSuccess: () => {
      invalidate();
      toast.success('Withdrawn. That day is a working day again.');
    },
    onError: (error: unknown) => toast.apiError(error),
  });

  if (holidays.isError) {
    return <ErrorPanel error={holidays.error} onRetry={() => void holidays.refetch()} />;
  }

  const applying = (holidays.data ?? []).filter((item) => item.applies);
  const notApplying = (holidays.data ?? []).filter((item) => !item.applies);

  return (
    <div>
      <PageHeader
        title="Holidays"
        description="The holidays that apply to you, and the optional ones you can still choose."
        action={
          <div className="w-32">
            <SelectField
              label="Year"
              value={year}
              onChange={(event) => setYear(event.target.value)}
              options={yearOptions()}
            />
          </div>
        }
      />

      <div className="space-y-6">
        {limits.data && limits.data.max_elections > 0 && (
          <Alert
            variant={limits.data.remaining > 0 ? 'info' : 'warning'}
            title={
              limits.data.remaining > 0
                ? `You can choose ${limits.data.remaining} more optional holiday${limits.data.remaining === 1 ? '' : 's'}`
                : 'You have chosen all your optional holidays'
            }
            icon={Info}
          >
            {limits.data.elected_count} of {limits.data.max_elections} chosen for{' '}
            {limits.data.year}. An optional holiday is only a day off once you have chosen it —
            withdraw one to choose another.
          </Alert>
        )}

        <Card>
          <CardHeader
            title={`Your holidays in ${year}`}
            description="Non-working days for you, from every published calendar that applies."
          />
          <CardBody>
            {holidays.isPending ? (
              <SkeletonRows rows={4} />
            ) : applying.length === 0 ? (
              <EmptyState
                icon={CalendarCheck}
                title={`No holidays apply to you in ${year}`}
                description="Either no calendar has been published for this year, or the only ones listed are optional and you have not chosen any."
              />
            ) : (
              <TableShell caption={`Holidays applying in ${year}`}>
                <thead>
                  <tr>
                    <Th>Date</Th>
                    <Th>Holiday</Th>
                    <Th>Kind</Th>
                    <Th className="text-right">Actions</Th>
                  </tr>
                </thead>
                <tbody>
                  {applying.map((item) => (
                    <tr key={item.holiday_id}>
                      <Td className="whitespace-nowrap">{formatIsoLong(item.holiday_date)}</Td>
                      <Td>
                        <p className="font-medium">{item.name}</p>
                        {item.region && <p className="text-ink-muted text-xs">{item.region}</p>}
                      </Td>
                      <Td>
                        <Badge tone={TYPE_TONES[item.holiday_type] ?? 'neutral'}>
                          {item.holiday_type}
                        </Badge>
                        {!item.is_paid && (
                          <Badge tone="warning" className="ml-1">
                            Unpaid
                          </Badge>
                        )}
                      </Td>
                      <Td className="text-right">
                        {item.requires_election && can('holiday.elect_optional') && (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => withdraw.mutate(item.holiday_id)}
                            disabled={withdraw.isPending}
                          >
                            Give up
                          </Button>
                        )}
                      </Td>
                    </tr>
                  ))}
                </tbody>
              </TableShell>
            )}
          </CardBody>
        </Card>

        {can('holiday.elect_optional') && (
          <Card>
            <CardHeader
              title="Optional holidays"
              description="These are not days off until you choose them. Each choice counts against your yearly limit."
            />
            <CardBody>
              {optional.isPending ? (
                <SkeletonRows rows={3} />
              ) : optional.isError ? (
                <ErrorPanel error={optional.error} onRetry={() => void optional.refetch()} />
              ) : (optional.data ?? []).length === 0 ? (
                <EmptyState
                  icon={CalendarPlus}
                  title="No optional holidays are published"
                  description="HR publishes the list of restricted and optional holidays each year."
                />
              ) : (
                <TableShell caption="Optional holidays you can choose">
                  <thead>
                    <tr>
                      <Th>Date</Th>
                      <Th>Holiday</Th>
                      <Th>Status</Th>
                      <Th className="text-right">Actions</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {(optional.data ?? []).map((item) => (
                      <tr key={item.holiday_id}>
                        <Td className="whitespace-nowrap">
                          {formatIsoLong(item.holiday_date)}
                        </Td>
                        <Td>
                          <p className="font-medium">{item.name}</p>
                          {item.region && (
                            <p className="text-ink-muted text-xs">{item.region}</p>
                          )}
                        </Td>
                        <Td>
                          {item.is_elected ? (
                            <Badge tone="success">Chosen</Badge>
                          ) : item.has_passed ? (
                            <Badge tone="neutral">Passed</Badge>
                          ) : (
                            <Badge tone="neutral">Not chosen</Badge>
                          )}
                        </Td>
                        <Td className="text-right">
                          {item.is_elected ? (
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => withdraw.mutate(item.holiday_id)}
                              disabled={withdraw.isPending || item.has_passed}
                              title={
                                item.has_passed
                                  ? 'This day has passed, so the choice cannot be withdrawn.'
                                  : undefined
                              }
                            >
                              Give up
                            </Button>
                          ) : (
                            <Button
                              variant="secondary"
                              size="sm"
                              onClick={() => elect.mutate(item.holiday_id)}
                              disabled={
                                elect.isPending ||
                                item.has_passed ||
                                (limits.data?.remaining ?? 0) <= 0
                              }
                              title={
                                item.has_passed
                                  ? 'This day has passed.'
                                  : (limits.data?.remaining ?? 0) <= 0
                                    ? 'You have used all your choices for this year.'
                                    : undefined
                              }
                            >
                              Choose
                            </Button>
                          )}
                        </Td>
                      </tr>
                    ))}
                  </tbody>
                </TableShell>
              )}
            </CardBody>
          </Card>
        )}

        {notApplying.length > 0 && (
          <Card>
            <CardHeader
              title="Listed but not applying to you"
              description="Optional holidays you have not chosen. They are shown so you know they exist."
            />
            <CardBody>
              <ul className="text-ink-soft space-y-1 text-sm">
                {notApplying.map((item) => (
                  <li key={item.holiday_id} className="flex items-start gap-2">
                    <CircleAlert
                      className="text-ink-muted mt-0.5 size-3.5 shrink-0"
                      aria-hidden="true"
                    />
                    <span>
                      {formatIsoLong(item.holiday_date)} — {item.name}
                      <span className="text-ink-muted"> ({item.holiday_type})</span>
                    </span>
                  </li>
                ))}
              </ul>
            </CardBody>
          </Card>
        )}
      </div>
    </div>
  );
}
