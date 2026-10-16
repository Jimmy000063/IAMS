/**
 * Building a reporting hierarchy out of a flat employee list.
 *
 * Separated from the component that draws it because the hard part is not the
 * drawing. Two cases exist in real data that a naive recursive walk gets wrong:
 *
 * * **Several roots.** Anybody with no manager is a root - a founder, but also
 *   somebody whose manager has left and not been replaced, and everybody whose
 *   manager simply is not in the visible set because the caller is scoped to
 *   their own team. All three are drawn as tops of their own trees.
 * * **A cycle.** If A reports to B and B reports to A, a recursive descent
 *   never returns. The database does not forbid it; a careless pair of edits
 *   can create it. The descent therefore carries the ancestors it has already
 *   seen and stops, reporting the people involved so the interface can say so
 *   instead of hanging the tab.
 */

import type { EmployeeSummary } from '@/features/employees/api';

export interface TreeNode {
  employee: EmployeeSummary;
  reports: TreeNode[];
  /** Everybody beneath this person, at any depth. */
  total: number;
}

export interface Forest {
  roots: TreeNode[];
  /** People whose manager chain loops back on itself. */
  cyclic: EmployeeSummary[];
}

export function buildForest(employees: EmployeeSummary[]): Forest {
  const byId = new Map(employees.map((employee) => [employee.id, employee]));
  const childrenOf = new Map<number | null, EmployeeSummary[]>();

  for (const employee of employees) {
    // A manager outside the visible set makes this person a root here, not an
    // orphan: a manager-scoped caller sees their own team and nothing above it.
    const managerId =
      employee.reporting_manager_id !== null && byId.has(employee.reporting_manager_id)
        ? employee.reporting_manager_id
        : null;
    // Somebody recorded as their own manager would otherwise never appear at
    // all, since they would be waiting for themselves.
    const parentId = managerId === employee.id ? null : managerId;
    const siblings = childrenOf.get(parentId) ?? [];
    siblings.push(employee);
    childrenOf.set(parentId, siblings);
  }

  const cyclic: EmployeeSummary[] = [];

  const descend = (employee: EmployeeSummary, ancestors: Set<number>): TreeNode => {
    if (ancestors.has(employee.id)) {
      cyclic.push(employee);
      return { employee, reports: [], total: 0 };
    }
    const nextAncestors = new Set(ancestors).add(employee.id);
    const reports = (childrenOf.get(employee.id) ?? [])
      .slice()
      .sort((a, b) => a.full_name.localeCompare(b.full_name))
      .map((report) => descend(report, nextAncestors));
    const total = reports.reduce((sum, node) => sum + node.total + 1, 0);
    return { employee, reports, total };
  };

  const roots = (childrenOf.get(null) ?? [])
    .slice()
    .sort((a, b) => a.full_name.localeCompare(b.full_name))
    .map((employee) => descend(employee, new Set()));

  /*
   * A cycle with no member reachable from any root - A reports to B, B to A,
   * and nobody else involved - produces no roots at all for that group, so the
   * people in it would vanish from the chart rather than be reported. Anyone
   * not placed by the descent above is therefore collected here.
   */
  const placed = new Set<number>();
  const mark = (node: TreeNode) => {
    placed.add(node.employee.id);
    node.reports.forEach(mark);
  };
  roots.forEach(mark);

  for (const employee of employees) {
    if (!placed.has(employee.id) && !cyclic.some((row) => row.id === employee.id)) {
      cyclic.push(employee);
    }
  }

  return { roots, cyclic };
}
