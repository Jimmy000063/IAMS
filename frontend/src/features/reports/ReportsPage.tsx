/**
 * Reports: pick one, filter it, read it, export it.
 *
 * The catalogue and the filter list come from the backend, so this page has no
 * second copy of which reports exist. The scope badge is shown rather than
 * hidden: a manager's report legitimately covers fewer people than HR's, and
 * somebody comparing two exports deserves to know why the totals differ.
 */

import { useMutation, useQuery } from '@tanstack/react-query';
import { BarChart3, Download, FileSpreadsheet, Play, TriangleAlert } from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/Button';
import { DatePicker } from '@/components/ui/DatePicker';
import { CheckboxField, TextField } from '@/components/ui/Field';
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
} from '@/components/ui/Surface';
import {
  exportReport,
  fetchCatalogue,
  formatCell,
  isNumeric,
  runReport,
  type ReportFilters,
} from '@/features/reports/api';
import { todayIso } from '@/lib/dates';
import { useToast } from '@/lib/toast';
import { cn } from '@/lib/utils';

export function ReportsPage() {
  const toast = useToast();
  const today = todayIso();
  const monthStart = `${today.slice(0, 7)}-01`;

  const [selected, setSelected] = useState<string | null>(null);
  const [filters, setFilters] = useState<ReportFilters>({
    start: monthStart,
    end: today,
    as_of: today,
  });
  const [ran, setRan] = useState(false);

  const catalogue = useQuery({ queryKey: ['reports', 'catalogue'], queryFn: fetchCatalogue });

  const definition = (catalogue.data ?? []).find((item) => item.code === selected);

  const result = useQuery({
    queryKey: ['reports', 'result', selected, filters],
    queryFn: () => runReport(selected as string, filters),
    enabled: selected !== null && ran,
  });

  const download = useMutation({
    mutationFn: (format: 'csv' | 'xlsx') => exportReport(selected as string, filters, format),
    onSuccess: () =>
      toast.show({
        variant: 'info',
        title: 'Export downloaded.',
        description:
          'The file has left the system, so the export was recorded in the audit log.',
      }),
    onError: (error: unknown) => toast.apiError(error),
  });

  const accepts = (parameter: string) => definition?.parameters.includes(parameter) ?? false;

  return (
    <div>
      <PageHeader
        title="Reports"
        description="Every figure here is read from the module that owns it, never recomputed - so a report and a payslip cannot disagree."
      />

      {catalogue.isPending ? (
        <SkeletonRows rows={4} />
      ) : catalogue.isError ? (
        <ErrorPanel error={catalogue.error} onRetry={() => void catalogue.refetch()} />
      ) : (catalogue.data ?? []).length === 0 ? (
        <EmptyState
          icon={BarChart3}
          title="No reports available to this account"
          description="Reports need one of the report permissions."
        />
      ) : (
        <div className="space-y-5">
          <Card>
            <CardHeader
              title="Choose a report"
              description="Scoped to what this account may run."
            />
            <CardBody>
              <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {(catalogue.data ?? []).map((item) => (
                  <button
                    key={item.code}
                    type="button"
                    onClick={() => {
                      setSelected(item.code);
                      setRan(false);
                    }}
                    className={cn(
                      'border-line rounded-lg border p-3 text-left transition',
                      selected === item.code
                        ? 'border-brand-600 bg-brand-50'
                        : 'hover:bg-sunken',
                    )}
                  >
                    <p className="text-ink flex items-center gap-2 text-sm font-medium">
                      {item.title}
                      <Badge tone={item.scope === 'all' ? 'brand' : 'neutral'}>
                        {item.scope === 'all' ? 'everyone' : 'your team'}
                      </Badge>
                    </p>
                    <p className="text-ink-soft mt-1 text-xs">{item.description}</p>
                  </button>
                ))}
              </div>
            </CardBody>
          </Card>

          {definition && (
            <Card>
              <CardHeader
                title={definition.title}
                description={definition.description}
                action={
                  <div className="flex flex-wrap gap-2">
                    <Button onClick={() => setRan(true)}>
                      <Play className="size-4" aria-hidden="true" />
                      Run
                    </Button>
                    <Button
                      variant="secondary"
                      onClick={() => download.mutate('csv')}
                      disabled={download.isPending}
                    >
                      <Download className="size-4" aria-hidden="true" />
                      CSV
                    </Button>
                    <Button
                      variant="secondary"
                      onClick={() => download.mutate('xlsx')}
                      disabled={download.isPending}
                    >
                      <FileSpreadsheet className="size-4" aria-hidden="true" />
                      Excel
                    </Button>
                  </div>
                }
              />
              <CardBody className="space-y-4">
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                  {accepts('start') && (
                    <DatePicker
                      label="From"
                      value={filters.start ?? ''}
                      onChange={(next) =>
                        setFilters((previous) => ({ ...previous, start: next }))
                      }
                    />
                  )}
                  {accepts('end') && (
                    <DatePicker
                      label="To"
                      value={filters.end ?? ''}
                      {...(filters.start ? { min: String(filters.start) } : {})}
                      onChange={(next) =>
                        setFilters((previous) => ({ ...previous, end: next }))
                      }
                    />
                  )}
                  {accepts('as_of') && (
                    <DatePicker
                      label="As of"
                      value={filters.as_of ?? ''}
                      onChange={(next) =>
                        setFilters((previous) => ({ ...previous, as_of: next }))
                      }
                    />
                  )}
                  {accepts('run_id') && (
                    <TextField
                      label="Payroll run id"
                      value={filters.run_id ? String(filters.run_id) : ''}
                      onChange={(event) =>
                        setFilters((previous) => {
                          const next = { ...previous };
                          if (event.target.value) {
                            next.run_id = Number(event.target.value);
                          } else {
                            delete next.run_id;
                          }
                          return next;
                        })
                      }
                    />
                  )}
                  {accepts('year') && (
                    <TextField
                      label="Year"
                      value={filters.year ? String(filters.year) : ''}
                      onChange={(event) =>
                        setFilters((previous) => {
                          const next = { ...previous };
                          if (event.target.value) {
                            next.year = Number(event.target.value);
                          } else {
                            delete next.year;
                          }
                          return next;
                        })
                      }
                    />
                  )}
                  {accepts('department_id') && (
                    <TextField
                      label="Department id"
                      value={filters.department_id ? String(filters.department_id) : ''}
                      onChange={(event) =>
                        setFilters((previous) => {
                          const next = { ...previous };
                          if (event.target.value) {
                            next.department_id = Number(event.target.value);
                          } else {
                            delete next.department_id;
                          }
                          return next;
                        })
                      }
                    />
                  )}
                  {accepts('include_exited') && (
                    <CheckboxField
                      label="Include employees who have left"
                      checked={filters.include_exited ?? false}
                      onChange={(event) =>
                        setFilters((previous) => ({
                          ...previous,
                          include_exited: event.target.checked,
                        }))
                      }
                    />
                  )}
                </div>

                {!ran ? (
                  <EmptyState
                    title="Not run yet"
                    description="Set the filters and press Run. An export uses the same filters."
                  />
                ) : result.isPending ? (
                  <SkeletonRows rows={6} />
                ) : result.isError ? (
                  <ErrorPanel error={result.error} onRetry={() => void result.refetch()} />
                ) : result.data ? (
                  <>
                    <div className="flex flex-wrap items-center gap-3">
                      <Badge tone="brand">{result.data.row_count} row(s)</Badge>
                      {Object.entries(result.data.totals).map(([key, value]) => (
                        <span key={key} className="text-ink-soft text-sm">
                          {key.replace(/_/g, ' ')}:{' '}
                          <span className="text-ink font-semibold tabular-nums">{value}</span>
                        </span>
                      ))}
                    </div>

                    {result.data.truncated && (
                      <Alert variant="warning" title="Truncated" icon={TriangleAlert}>
                        The row cap was reached, so this is not the whole answer. Narrow the
                        filters.
                      </Alert>
                    )}

                    {result.data.rows.length === 0 ? (
                      <EmptyState
                        title="Nothing matched"
                        description="No rows for those filters."
                      />
                    ) : (
                      <TableShell caption={result.data.title}>
                        <thead>
                          <tr>
                            {result.data.columns.map((column) => (
                              <Th
                                key={column.key}
                                className={isNumeric(column.kind) ? 'text-right' : undefined}
                              >
                                {column.label}
                              </Th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {result.data.rows.map((row, index) => (
                            <tr key={index}>
                              {result.data.columns.map((column) => (
                                <Td
                                  key={column.key}
                                  className={
                                    isNumeric(column.kind)
                                      ? 'text-right tabular-nums'
                                      : undefined
                                  }
                                >
                                  {formatCell(row[column.key], column.kind)}
                                </Td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </TableShell>
                    )}
                  </>
                ) : null}
              </CardBody>
            </Card>
          )}
        </div>
      )}
    </div>
  );
}
