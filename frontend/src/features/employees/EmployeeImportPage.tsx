import { useMutation, useQueryClient } from '@tanstack/react-query';
import {
  ArrowLeft,
  CircleCheck,
  Download,
  FileSpreadsheet,
  TriangleAlert,
  Upload,
} from 'lucide-react';
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import { Button } from '@/components/ui/Button';
import { Alert } from '@/components/ui/Feedback';
import {
  Card,
  CardBody,
  CardHeader,
  PageHeader,
  TableShell,
  Td,
  Th,
} from '@/components/ui/Surface';
import {
  downloadImportTemplate,
  employeeKeys,
  importEmployees,
  type ImportReport,
} from '@/features/employees/api';
import { useToast } from '@/lib/toast';

export function EmployeeImportPage() {
  const toast = useToast();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [file, setFile] = useState<File | null>(null);
  const [report, setReport] = useState<ImportReport | null>(null);

  const mutation = useMutation({
    mutationFn: ({ selected, dryRun }: { selected: File; dryRun: boolean }) =>
      importEmployees(selected, dryRun),
    onSuccess: async (result) => {
      setReport(result);
      if (!result.dry_run && result.ok) {
        toast.success(
          'Import complete',
          `${result.created_codes.length} employee${
            result.created_codes.length === 1 ? '' : 's'
          } created.`,
        );
        await queryClient.invalidateQueries({ queryKey: employeeKeys.all });
      } else if (!result.ok) {
        toast.error(
          'The file has problems',
          `${result.error_count} row${result.error_count === 1 ? '' : 's'} need fixing. Nothing was written.`,
        );
      }
    },
    onError: (error) => {
      setReport(null);
      toast.apiError(error, 'The file could not be read');
    },
  });

  const run = (dryRun: boolean) => {
    if (!file) {
      toast.error('Choose a CSV file first');
      return;
    }
    mutation.mutate({ selected: file, dryRun });
  };

  const validatedOk = report?.ok === true && report.dry_run;
  const committed = report?.ok === true && !report.dry_run;

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
        title="Import employees"
        description="Validate a CSV, review the preview, then commit. The import is all-or-nothing: if any row has a problem, nothing is written."
        action={
          <Button
            variant="secondary"
            leadingIcon={<Download className="size-4" aria-hidden="true" />}
            onClick={() => {
              void downloadImportTemplate().catch(() =>
                toast.error('Could not download the template'),
              );
            }}
          >
            Download template
          </Button>
        }
      />

      <div className="space-y-6">
        <Card>
          <CardHeader title="1. Choose a file" />
          <CardBody className="space-y-4">
            <label className="border-line-strong hover:bg-sunken flex cursor-pointer flex-col items-center gap-2 rounded-lg border border-dashed px-6 py-10 text-center transition-colors">
              <FileSpreadsheet className="text-ink-muted size-8" aria-hidden="true" />
              <span className="text-ink text-sm font-medium">
                {file ? file.name : 'Select a CSV file'}
              </span>
              <span className="text-ink-muted text-xs">
                UTF-8 CSV, up to 2 MB and 2000 rows. Excel&apos;s &ldquo;CSV UTF-8&rdquo; export
                works.
              </span>
              <input
                type="file"
                accept=".csv,text/csv"
                className="sr-only"
                onChange={(event) => {
                  setFile(event.target.files?.[0] ?? null);
                  setReport(null);
                }}
              />
            </label>

            <Alert variant="info" title="What can be imported">
              Identifying and positional fields only — code, name, contact, dates, department,
              designation, location and reporting manager (by code). Bank and statutory details
              are deliberately excluded: bulk-loading them would mean those values sitting in a
              spreadsheet on someone&apos;s disk.
            </Alert>
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="2. Validate"
            description="Nothing is written. Every row is checked and all problems are reported at once."
          />
          <CardBody className="flex flex-wrap gap-2">
            <Button
              onClick={() => run(true)}
              disabled={!file}
              isLoading={mutation.isPending && mutation.variables?.dryRun === true}
              leadingIcon={<CircleCheck className="size-4" aria-hidden="true" />}
            >
              Validate and preview
            </Button>
          </CardBody>
        </Card>

        {report && (
          <Card>
            <CardHeader
              title="Result"
              description={
                report.dry_run ? 'Preview only — nothing has been written.' : 'Applied.'
              }
            />
            <CardBody className="space-y-4">
              <div className="grid gap-3 sm:grid-cols-3">
                <Stat label="Rows read" value={report.total_rows} />
                <Stat label="Valid rows" value={report.valid_rows} tone="success" />
                <Stat
                  label="Problems"
                  value={report.error_count}
                  tone={report.error_count ? 'danger' : 'success'}
                />
              </div>

              {report.errors.length > 0 && (
                <>
                  <Alert
                    variant="danger"
                    title="Fix these and upload again"
                    icon={TriangleAlert}
                  >
                    Nothing was written. Row numbers match the spreadsheet, counting the header
                    as row 1.
                  </Alert>
                  <TableShell caption="Import problems">
                    <thead>
                      <tr>
                        <Th className="w-20">Row</Th>
                        <Th className="w-48">Column</Th>
                        <Th>Problem</Th>
                      </tr>
                    </thead>
                    <tbody>
                      {report.errors.map((issue, index) => (
                        <tr key={index}>
                          <Td className="numeric">{issue.row}</Td>
                          <Td className="font-mono text-xs">{issue.column ?? '—'}</Td>
                          <Td>{issue.message}</Td>
                        </tr>
                      ))}
                    </tbody>
                  </TableShell>
                </>
              )}

              {report.preview.length > 0 && report.errors.length === 0 && (
                <>
                  <p className="text-ink-soft text-sm">
                    {report.dry_run
                      ? 'These employees would be created:'
                      : 'These employees were created:'}
                  </p>
                  <TableShell caption="Import preview">
                    <thead>
                      <tr>
                        <Th className="w-20">Row</Th>
                        <Th>Code</Th>
                        <Th>Name</Th>
                        <Th className="hidden sm:table-cell">Email</Th>
                        <Th className="hidden sm:table-cell">Joining</Th>
                        <Th className="hidden md:table-cell">Manager</Th>
                      </tr>
                    </thead>
                    <tbody>
                      {report.preview.map((row) => (
                        <tr key={row.row}>
                          <Td className="numeric">{row.row}</Td>
                          <Td className="font-mono text-xs">{row.employee_code}</Td>
                          <Td>{row.name}</Td>
                          <Td className="hidden sm:table-cell">{row.work_email ?? '—'}</Td>
                          <Td className="numeric hidden sm:table-cell">
                            {row.date_of_joining}
                          </Td>
                          <Td className="hidden font-mono text-xs md:table-cell">
                            {row.reporting_manager_code ?? '—'}
                          </Td>
                        </tr>
                      ))}
                    </tbody>
                  </TableShell>
                  {report.total_rows > report.preview.length && (
                    <p className="text-ink-muted text-xs">
                      Showing the first {report.preview.length} of {report.total_rows} rows.
                    </p>
                  )}
                </>
              )}
            </CardBody>
          </Card>
        )}

        {validatedOk && (
          <Card>
            <CardHeader
              title="3. Commit"
              description="Creates every employee above and opens their employment history."
            />
            <CardBody className="flex flex-wrap gap-2">
              <Button
                onClick={() => run(false)}
                isLoading={mutation.isPending && mutation.variables?.dryRun === false}
                leadingIcon={<Upload className="size-4" aria-hidden="true" />}
              >
                Import {report?.valid_rows} employee
                {report?.valid_rows === 1 ? '' : 's'}
              </Button>
            </CardBody>
          </Card>
        )}

        {committed && (
          <Alert variant="success" title="Import complete" icon={CircleCheck}>
            <p>
              {report.created_codes.length} employee
              {report.created_codes.length === 1 ? '' : 's'} created:{' '}
              <span className="font-mono text-xs">
                {report.created_codes.slice(0, 20).join(', ')}
                {report.created_codes.length > 20 ? '…' : ''}
              </span>
            </p>
            <Button
              variant="secondary"
              size="sm"
              className="mt-3"
              onClick={() => navigate('/employees')}
            >
              View directory
            </Button>
          </Alert>
        )}
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  tone = 'neutral',
}: {
  label: string;
  value: number;
  tone?: 'neutral' | 'success' | 'danger';
}) {
  const valueTone =
    tone === 'success'
      ? 'text-success-700'
      : tone === 'danger'
        ? 'text-danger-700'
        : 'text-ink';
  return (
    <div className="border-line rounded-md border p-3">
      <p className="text-ink-muted text-xs font-medium tracking-wide uppercase">{label}</p>
      <p className={`numeric mt-1 text-2xl font-semibold ${valueTone}`}>{value}</p>
    </div>
  );
}
