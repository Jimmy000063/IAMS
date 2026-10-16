/**
 * The reporting hierarchy, drawn from `reporting_manager_id`.
 *
 * Built in the browser from the employee list rather than requested as a tree:
 * the list already carries every manager link, and a second endpoint returning
 * the same facts in a different shape is a second thing that can disagree.
 *
 * The tree construction - including the cycle guard, which is the part that can
 * hang a tab if it is wrong - lives in `orgTree.ts` under test. This file only
 * draws it.
 */

import { ChevronRight, Network, UserRound } from 'lucide-react';
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';

import { Alert } from '@/components/ui/Feedback';
import { Avatar, Badge } from '@/components/ui/Surface';
import type { EmployeeSummary } from '@/features/employees/api';
import { buildForest, type TreeNode } from '@/features/employees/orgTree';
import { cn } from '@/lib/utils';

function Node({
  node,
  depth,
  expandedAll,
}: {
  node: TreeNode;
  depth: number;
  expandedAll: boolean;
}) {
  // The first two levels start open: deeper than that and the page opens as a
  // wall of names, which is the thing an org chart is supposed to prevent.
  const [open, setOpen] = useState(depth < 2);
  const isOpen = expandedAll || open;
  const hasReports = node.reports.length > 0;

  return (
    <li className="relative">
      <div
        className={cn(
          'group relative flex items-center gap-3 rounded-[var(--radius-card)] py-2 pr-3 pl-2',
          'hover:bg-sunken/70 transition-colors',
        )}
      >
        {hasReports ? (
          <button
            type="button"
            onClick={() => setOpen((value) => !value)}
            aria-expanded={isOpen}
            aria-label={
              isOpen
                ? `Collapse ${node.employee.full_name}`
                : `Expand ${node.employee.full_name}`
            }
            className="text-ink-muted hover:text-ink grid size-6 shrink-0 place-items-center rounded-md transition-colors"
          >
            <ChevronRight
              className={cn('size-4 transition-transform duration-200', isOpen && 'rotate-90')}
              aria-hidden="true"
            />
          </button>
        ) : (
          <span className="size-6 shrink-0" aria-hidden="true" />
        )}

        <Avatar name={node.employee.full_name} size="sm" />

        <div className="min-w-0 flex-1">
          <Link
            to={`/employees/${node.employee.id}`}
            className="text-ink hover:text-brand-600 dark:hover:text-brand-300 truncate text-sm font-medium transition-colors"
          >
            {node.employee.full_name}
          </Link>
          <p className="text-ink-muted truncate text-xs">
            {node.employee.designation_name ?? node.employee.employee_code}
            {node.employee.department_name ? ` - ${node.employee.department_name}` : ''}
          </p>
        </div>

        {hasReports && (
          <Badge tone="neutral" className="shrink-0">
            {node.total} {node.total === 1 ? 'report' : 'reports'}
          </Badge>
        )}
        {node.employee.employment_status === 'exited' && (
          <Badge tone="danger" className="shrink-0">
            exited
          </Badge>
        )}
      </div>

      {hasReports && isOpen && (
        <ul
          className={cn(
            'relative ml-5 space-y-0.5 pl-5',
            // The connector: one vertical line down the group, with a short
            // horizontal stub to each child. Drawn in CSS rather than SVG so it
            // reflows with the text at any zoom.
            'before:bg-line before:absolute before:inset-y-0 before:left-0 before:w-px',
          )}
        >
          {node.reports.map((report) => (
            <div key={report.employee.id} className="relative">
              <span
                aria-hidden="true"
                className="bg-line absolute top-[1.4rem] -left-5 h-px w-5"
              />
              <Node node={report} depth={depth + 1} expandedAll={expandedAll} />
            </div>
          ))}
        </ul>
      )}
    </li>
  );
}

export function OrgChart({ employees }: { employees: EmployeeSummary[] }) {
  const [expandedAll, setExpandedAll] = useState(false);
  const { roots, cyclic } = useMemo(() => buildForest(employees), [employees]);

  if (employees.length === 0) {
    return (
      <div className="text-ink-soft flex flex-col items-center gap-3 py-16 text-center">
        <Network className="text-ink-muted size-8" aria-hidden="true" />
        <p className="text-sm">
          No employees match these filters, so there is no chart to draw.
        </p>
      </div>
    );
  }

  const unmanaged = roots.filter((node) => node.reports.length === 0).length;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Badge tone="brand" dot>
          {employees.length} in view
        </Badge>
        <Badge tone="neutral">
          {roots.length} at the top{roots.length > 1 ? ' (no manager recorded)' : ''}
        </Badge>
        {unmanaged > 0 && (
          <Badge tone="warning">{unmanaged} with neither a manager nor reports</Badge>
        )}
        <button
          type="button"
          onClick={() => setExpandedAll((value) => !value)}
          className="text-brand-600 dark:text-brand-300 ml-auto text-sm font-medium hover:underline"
        >
          {expandedAll ? 'Collapse to two levels' : 'Expand everything'}
        </button>
      </div>

      {cyclic.length > 0 && (
        <Alert variant="warning" title="A reporting loop was found" icon={UserRound}>
          {cyclic.map((employee) => employee.full_name).join(', ')} report into a cycle, so the
          chart stops there. Correct the reporting manager on one of them.
        </Alert>
      )}

      <ul className="space-y-0.5">
        {roots.map((root) => (
          <Node key={root.employee.id} node={root} depth={0} expandedAll={expandedAll} />
        ))}
      </ul>
    </div>
  );
}
