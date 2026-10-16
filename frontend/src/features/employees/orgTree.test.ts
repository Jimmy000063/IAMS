/**
 * Reporting hierarchy construction.
 *
 * The first test here is the one that matters: a reporting cycle must not hang
 * the browser. The rest guard the cases that would silently drop a person from
 * the chart, which is worse than drawing them in the wrong place because
 * nothing on screen says anybody is missing.
 */

import { describe, expect, it } from 'vitest';

import type { EmployeeSummary } from '@/features/employees/api';
import { buildForest, type TreeNode } from '@/features/employees/orgTree';

function employee(
  id: number,
  full_name: string,
  reporting_manager_id: number | null,
): EmployeeSummary {
  return {
    id,
    employee_code: `E${String(id).padStart(3, '0')}`,
    full_name,
    work_email: null,
    department_id: null,
    department_name: null,
    designation_id: null,
    designation_name: null,
    work_location_id: null,
    work_location_name: null,
    reporting_manager_id,
    employment_type: 'full_time',
    employment_status: 'active',
    date_of_joining: '2026-01-01',
    date_of_exit: null,
  };
}

/** Every id the forest actually drew, at any depth. */
function drawn(roots: TreeNode[]): number[] {
  const ids: number[] = [];
  const walk = (node: TreeNode) => {
    ids.push(node.employee.id);
    node.reports.forEach(walk);
  };
  roots.forEach(walk);
  return ids.sort((a, b) => a - b);
}

describe('buildForest', () => {
  it('returns an empty forest for an empty list', () => {
    expect(buildForest([])).toEqual({ roots: [], cyclic: [] });
  });

  it('nests reports under their manager and counts the whole subtree', () => {
    const forest = buildForest([
      employee(1, 'Anita Rao', null),
      employee(2, 'Bhavin Shah', 1),
      employee(3, 'Chitra Nair', 2),
      employee(4, 'Devi Menon', 1),
    ]);

    expect(forest.roots).toHaveLength(1);
    const root = forest.roots[0]!;
    expect(root.employee.id).toBe(1);
    // Two direct reports, three people in total beneath them.
    expect(root.reports.map((node) => node.employee.id)).toEqual([2, 4]);
    expect(root.total).toBe(3);
    expect(root.reports[0]!.total).toBe(1);
    expect(root.reports[1]!.total).toBe(0);
  });

  it('sorts siblings by name so the chart does not reorder between loads', () => {
    const forest = buildForest([
      employee(1, 'Anita Rao', null),
      employee(2, 'Zoya Khan', 1),
      employee(3, 'Bhavin Shah', 1),
    ]);
    expect(forest.roots[0]!.reports.map((node) => node.employee.full_name)).toEqual([
      'Bhavin Shah',
      'Zoya Khan',
    ]);
  });

  it('treats a manager outside the visible set as a root, not an orphan', () => {
    // What a manager-scoped caller sees: their own team, with the manager above
    // them absent from the list entirely.
    const forest = buildForest([employee(5, 'Esha Patel', 99), employee(6, 'Farid Khan', 5)]);

    expect(forest.roots.map((node) => node.employee.id)).toEqual([5]);
    expect(forest.cyclic).toEqual([]);
    expect(drawn(forest.roots)).toEqual([5, 6]);
  });

  it('draws several roots when nobody has a manager', () => {
    const forest = buildForest([
      employee(1, 'Anita Rao', null),
      employee(2, 'Bhavin Shah', null),
    ]);
    expect(forest.roots).toHaveLength(2);
    expect(forest.roots.every((node) => node.total === 0)).toBe(true);
  });

  it('terminates on a two-person cycle instead of recursing forever', () => {
    // If this regresses, the test run hangs rather than failing - which is
    // exactly what would happen in the browser.
    const forest = buildForest([employee(1, 'Anita Rao', 2), employee(2, 'Bhavin Shah', 1)]);

    expect(forest.cyclic.map((row) => row.id).sort()).toEqual([1, 2]);
  });

  it('terminates on a longer cycle and still reports everyone in it', () => {
    const forest = buildForest([
      employee(1, 'Anita Rao', 3),
      employee(2, 'Bhavin Shah', 1),
      employee(3, 'Chitra Nair', 2),
    ]);

    expect(forest.cyclic.map((row) => row.id).sort()).toEqual([1, 2, 3]);
  });

  it('places somebody recorded as their own manager at the top', () => {
    const forest = buildForest([employee(1, 'Anita Rao', 1), employee(2, 'Bhavin Shah', 1)]);

    expect(forest.roots.map((node) => node.employee.id)).toEqual([1]);
    expect(drawn(forest.roots)).toEqual([1, 2]);
  });

  it('keeps a healthy tree intact when a cycle exists elsewhere in the list', () => {
    const forest = buildForest([
      employee(1, 'Anita Rao', null),
      employee(2, 'Bhavin Shah', 1),
      employee(7, 'Gita Bose', 8),
      employee(8, 'Hari Iyer', 7),
    ]);

    expect(drawn(forest.roots)).toEqual([1, 2]);
    expect(forest.cyclic.map((row) => row.id).sort()).toEqual([7, 8]);
  });

  it('accounts for every employee, as either drawn or flagged', () => {
    const employees = [
      employee(1, 'Anita Rao', null),
      employee(2, 'Bhavin Shah', 1),
      employee(3, 'Chitra Nair', 2),
      employee(7, 'Gita Bose', 8),
      employee(8, 'Hari Iyer', 7),
      employee(9, 'Ishan Verma', 404),
    ];
    const forest = buildForest(employees);
    const accounted = new Set([...drawn(forest.roots), ...forest.cyclic.map((row) => row.id)]);
    expect(accounted.size).toBe(employees.length);
  });
});
