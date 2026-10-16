/**
 * Routes, with every authenticated page loaded on demand.
 *
 * Code-splitting by route, which is the split that matches how the application
 * is used: an employee who only ever opens their attendance and payslips never
 * downloads the payroll workspace or the audit viewer. The shell, the guards
 * and the two pages an unauthenticated visitor needs stay in the entry bundle -
 * splitting the sign-in page would add a round trip before anybody can log in.
 *
 * `Suspense` sits once around the outlet in `AppShell`, so a page arriving late
 * shows the shell with a loading panel rather than a blank screen.
 */

import { lazy } from 'react';
import { createBrowserRouter, Navigate } from 'react-router-dom';

import { AppShell } from '@/app/AppShell';
import { RequireAuth, RequireGuest, RequirePermission } from '@/app/guards';
import { NotFoundPage } from '@/app/NotFoundPage';
import { ChangePasswordPage } from '@/features/auth/ChangePasswordPage';
import { SignInPage } from '@/features/auth/SignInPage';

const AnnouncementAdminPage = lazy(() =>
  import('@/features/notifications/AnnouncementAdminPage').then((module) => ({
    default: module.AnnouncementAdminPage,
  })),
);
const AnnouncementsPage = lazy(() =>
  import('@/features/notifications/AnnouncementsPage').then((module) => ({
    default: module.AnnouncementsPage,
  })),
);
const AttendanceAdminPage = lazy(() =>
  import('@/features/attendance/AttendanceAdminPage').then((module) => ({
    default: module.AttendanceAdminPage,
  })),
);
const DashboardPage = lazy(() =>
  import('@/features/dashboard/DashboardPage').then((module) => ({
    default: module.DashboardPage,
  })),
);
const DevicesPage = lazy(() =>
  import('@/features/devices/DevicesPage').then((module) => ({ default: module.DevicesPage })),
);
const EmployeeDetailPage = lazy(() =>
  import('@/features/employees/EmployeeDetailPage').then((module) => ({
    default: module.EmployeeDetailPage,
  })),
);
const EmployeeDirectoryPage = lazy(() =>
  import('@/features/employees/EmployeeDirectoryPage').then((module) => ({
    default: module.EmployeeDirectoryPage,
  })),
);
const EmployeeFormPage = lazy(() =>
  import('@/features/employees/EmployeeFormPage').then((module) => ({
    default: module.EmployeeFormPage,
  })),
);
const EmployeeImportPage = lazy(() =>
  import('@/features/employees/EmployeeImportPage').then((module) => ({
    default: module.EmployeeImportPage,
  })),
);
const HolidaysPage = lazy(() =>
  import('@/features/holidays/HolidaysPage').then((module) => ({
    default: module.HolidaysPage,
  })),
);
const LeaveAdminPage = lazy(() =>
  import('@/features/leave/LeaveAdminPage').then((module) => ({
    default: module.LeaveAdminPage,
  })),
);
const MyAttendancePage = lazy(() =>
  import('@/features/attendance/MyAttendancePage').then((module) => ({
    default: module.MyAttendancePage,
  })),
);
const MyCompoffPage = lazy(() =>
  import('@/features/compoff/MyCompoffPage').then((module) => ({
    default: module.MyCompoffPage,
  })),
);
const MyLeavePage = lazy(() =>
  import('@/features/leave/MyLeavePage').then((module) => ({ default: module.MyLeavePage })),
);
const MyPayslipsPage = lazy(() =>
  import('@/features/payroll/MyPayslipsPage').then((module) => ({
    default: module.MyPayslipsPage,
  })),
);
const MyProfilePage = lazy(() =>
  import('@/features/employees/MyProfilePage').then((module) => ({
    default: module.MyProfilePage,
  })),
);
const NotificationsPage = lazy(() =>
  import('@/features/notifications/NotificationsPage').then((module) => ({
    default: module.NotificationsPage,
  })),
);
const OrganisationPage = lazy(() =>
  import('@/features/organisation/OrganisationPage').then((module) => ({
    default: module.OrganisationPage,
  })),
);
const PayrollPage = lazy(() =>
  import('@/features/payroll/PayrollPage').then((module) => ({ default: module.PayrollPage })),
);
const SettingsPage = lazy(() =>
  import('@/features/settings/SettingsPage').then((module) => ({
    default: module.SettingsPage,
  })),
);
const ShiftsPage = lazy(() =>
  import('@/features/attendance/ShiftsPage').then((module) => ({ default: module.ShiftsPage })),
);
const ReportsPage = lazy(() =>
  import('@/features/reports/ReportsPage').then((module) => ({ default: module.ReportsPage })),
);
const AuditPage = lazy(() =>
  import('@/features/reports/AuditPage').then((module) => ({ default: module.AuditPage })),
);

export const router = createBrowserRouter([
  {
    path: '/sign-in',
    element: (
      <RequireGuest>
        <SignInPage />
      </RequireGuest>
    ),
  },
  {
    // Outside the authenticated shell: a user with a temporary password has to
    // be able to reach it, and RequireAuth deliberately redirects them here.
    path: '/change-password',
    element: <ChangePasswordPage />,
  },
  {
    path: '/',
    element: (
      <RequireAuth>
        <AppShell />
      </RequireAuth>
    ),
    children: [
      { index: true, element: <Navigate to="/dashboard" replace /> },
      { path: 'dashboard', element: <DashboardPage /> },

      // Self-service: every signed-in employee has these.
      { path: 'my-profile', element: <MyProfilePage /> },
      {
        path: 'my-attendance',
        element: (
          <RequirePermission permissions={['attendance.read_self']}>
            <MyAttendancePage />
          </RequirePermission>
        ),
      },

      // Employee directory. The list and detail routes accept either
      // `employee.read` (HR) or `employee.read_team` (managers); the backend
      // narrows the result set to what the caller may actually see.
      {
        path: 'employees',
        element: (
          <RequirePermission permissions={['employee.read', 'employee.read_team']} mode="any" />
        ),
        children: [
          { index: true, element: <EmployeeDirectoryPage /> },
          {
            path: 'new',
            element: (
              <RequirePermission permissions={['employee.manage']}>
                <EmployeeFormPage />
              </RequirePermission>
            ),
          },
          {
            path: 'import',
            element: (
              <RequirePermission permissions={['employee.import']}>
                <EmployeeImportPage />
              </RequirePermission>
            ),
          },
          { path: ':employeeId', element: <EmployeeDetailPage /> },
          {
            path: ':employeeId/edit',
            element: (
              <RequirePermission permissions={['employee.manage']}>
                <EmployeeFormPage />
              </RequirePermission>
            ),
          },
        ],
      },

      {
        // Either grant reveals it; the page shows only the tabs the
        // caller can act on.
        path: 'attendance',
        element: (
          <RequirePermission
            permissions={[
              'attendance.read_all',
              'regularization.approve_hr',
              'regularization.approve_as_manager',
            ]}
            mode="any"
          >
            <AttendanceAdminPage />
          </RequirePermission>
        ),
      },
      {
        path: 'shifts',
        element: (
          <RequirePermission permissions={['shift.read']}>
            <ShiftsPage />
          </RequirePermission>
        ),
      },
      {
        path: 'leave',
        element: (
          <RequirePermission permissions={['leave.read_self']}>
            <MyLeavePage />
          </RequirePermission>
        ),
      },
      {
        path: 'compoff',
        element: (
          <RequirePermission permissions={['compoff.read_self']}>
            <MyCompoffPage />
          </RequirePermission>
        ),
      },
      {
        path: 'holidays',
        element: (
          <RequirePermission permissions={['holiday.read']}>
            <HolidaysPage />
          </RequirePermission>
        ),
      },
      {
        // Any one of these reveals the page; it shows only the tabs the caller
        // can act on, and every endpoint behind them is authorized server-side.
        path: 'leave-admin',
        element: (
          <RequirePermission
            permissions={[
              'leave.approve_as_manager',
              'leave.approve_hr',
              'leave.manage_policy',
              'leave.run_year_end',
              'compoff.verify',
              'compoff.approve_hr',
              'compoff.approve_as_manager',
              'holiday.manage',
            ]}
            mode="any"
          >
            <LeaveAdminPage />
          </RequirePermission>
        ),
      },
      {
        path: 'payslips',
        element: (
          <RequirePermission permissions={['payslip.read_self']}>
            <MyPayslipsPage />
          </RequirePermission>
        ),
      },
      {
        // Any one of these reveals the page; it shows only the tabs the caller
        // can act on, and the lifecycle needs several distinct permissions.
        path: 'payroll',
        element: (
          <RequirePermission
            permissions={[
              'payroll.read',
              'payroll.calculate',
              'salary.manage',
              'salary.read_all',
              'statutory.read',
              'statutory.manage',
            ]}
            mode="any"
          >
            <PayrollPage />
          </RequirePermission>
        ),
      },
      {
        path: 'announcements',
        element: (
          <RequirePermission permissions={['announcement.read']}>
            <AnnouncementsPage />
          </RequirePermission>
        ),
      },
      {
        path: 'notifications',
        element: (
          <RequirePermission permissions={['notification.read_self']}>
            <NotificationsPage />
          </RequirePermission>
        ),
      },
      {
        path: 'announcements-admin',
        element: (
          <RequirePermission permissions={['announcement.manage']}>
            <AnnouncementAdminPage />
          </RequirePermission>
        ),
      },
      {
        path: 'devices',
        element: (
          <RequirePermission permissions={['device.read']}>
            <DevicesPage />
          </RequirePermission>
        ),
      },
      {
        path: 'organisation',
        element: (
          <RequirePermission permissions={['org.read']}>
            <OrganisationPage />
          </RequirePermission>
        ),
      },
      {
        path: 'reports',
        element: (
          <RequirePermission
            permissions={[
              'report.attendance',
              'report.leave',
              'report.employee',
              'report.payroll',
            ]}
            mode="any"
          >
            <ReportsPage />
          </RequirePermission>
        ),
      },
      {
        path: 'audit',
        element: (
          <RequirePermission permissions={['audit.read']}>
            <AuditPage />
          </RequirePermission>
        ),
      },
      {
        path: 'settings',
        element: (
          <RequirePermission permissions={['settings.read']}>
            <SettingsPage />
          </RequirePermission>
        ),
      },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
]);
