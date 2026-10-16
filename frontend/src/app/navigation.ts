/**
 * Navigation definition.
 *
 * Each item names the permission a user must hold for the link to appear. This
 * controls *visibility only* - the backend authorizes the request itself. The
 * permission strings must match `app/core/permissions.py`.
 *
 * Items marked `planned` are rendered disabled with a "coming soon" hint rather
 * than omitted, so the shape of the finished product is visible while later
 * milestones are built. A disabled link is honest; a link that 404s is not.
 */

import {
  BarChart3,
  Bell,
  Building2,
  CalendarCheck,
  CalendarHeart,
  CalendarCog,
  ClipboardClock,
  CalendarDays,
  Clock,
  FileText,
  LayoutDashboard,
  Megaphone,
  Router,
  ScrollText,
  Settings,
  Timer,
  UserCircle,
  Users,
  Wallet,
  type LucideIcon,
} from 'lucide-react';

export interface NavItem {
  label: string;
  to: string;
  icon: LucideIcon;
  /** Any one of these permissions makes the item visible. */
  permissions: string[];
  /** Not yet implemented; shown disabled with the milestone that delivers it. */
  planned?: string;
}

export interface NavSection {
  heading: string;
  items: NavItem[];
}

export const NAVIGATION: NavSection[] = [
  {
    heading: 'Overview',
    items: [
      {
        label: 'Dashboard',
        to: '/dashboard',
        icon: LayoutDashboard,
        permissions: ['profile.read_self'],
      },
    ],
  },
  {
    heading: 'My work',
    items: [
      {
        label: 'My profile',
        to: '/my-profile',
        icon: UserCircle,
        permissions: ['profile.read_self'],
      },
      {
        label: 'My attendance',
        to: '/my-attendance',
        icon: Clock,
        permissions: ['attendance.read_self'],
      },
      {
        label: 'Leave',
        to: '/leave',
        icon: CalendarDays,
        permissions: ['leave.read_self'],
      },
      {
        label: 'Comp-off',
        to: '/compoff',
        icon: Timer,
        permissions: ['compoff.read_self'],
      },
      {
        label: 'Holidays',
        to: '/holidays',
        icon: CalendarHeart,
        permissions: ['holiday.read'],
      },
      {
        label: 'Payslips',
        to: '/payslips',
        icon: Wallet,
        permissions: ['payslip.read_self'],
      },
      {
        label: 'Announcements',
        to: '/announcements',
        icon: Megaphone,
        permissions: ['announcement.read'],
      },
    ],
  },
  {
    heading: 'Administration',
    items: [
      {
        label: 'Employees',
        to: '/employees',
        icon: Users,
        // Either grant reveals the link; the backend narrows the results, so a
        // manager sees their team and HR sees everyone.
        permissions: ['employee.read', 'employee.read_team'],
      },
      {
        label: 'Attendance',
        to: '/attendance',
        icon: ClipboardClock,
        // Any of these reveals the link; the page shows only the tabs the
        // caller can act on.
        permissions: [
          'attendance.read_all',
          'regularization.approve_hr',
          'regularization.approve_as_manager',
        ],
      },
      {
        label: 'Shifts & calendars',
        to: '/shifts',
        icon: CalendarCog,
        permissions: ['shift.read'],
      },
      {
        label: 'Leave & time off',
        to: '/leave-admin',
        icon: CalendarCheck,
        // Any one of these reveals the link; the page shows only the tabs the
        // caller can act on.
        permissions: [
          'leave.approve_as_manager',
          'leave.approve_hr',
          'leave.manage_policy',
          'leave.run_year_end',
          'compoff.verify',
          'compoff.approve_hr',
          'compoff.approve_as_manager',
          'holiday.manage',
        ],
      },
      {
        label: 'Post announcements',
        to: '/announcements-admin',
        icon: Megaphone,
        permissions: ['announcement.manage'],
      },
      {
        label: 'Devices',
        to: '/devices',
        icon: Router,
        permissions: ['device.read'],
      },
      {
        label: 'Organisation',
        to: '/organisation',
        icon: Building2,
        permissions: ['org.read'],
      },
      {
        label: 'Payroll',
        to: '/payroll',
        icon: FileText,
        // Any of these reveals the link; the page shows only the tabs the
        // caller can act on.
        permissions: ['payroll.read', 'payroll.calculate', 'salary.manage', 'statutory.read'],
      },
      {
        label: 'Reports',
        to: '/reports',
        icon: BarChart3,
        permissions: ['report.attendance', 'report.leave', 'report.employee', 'report.payroll'],
      },
      {
        label: 'Audit & retention',
        to: '/audit',
        icon: ScrollText,
        permissions: ['audit.read'],
      },
      {
        label: 'Notifications',
        to: '/notifications',
        icon: Bell,
        permissions: ['notification.read_self'],
      },
      {
        label: 'Settings',
        to: '/settings',
        icon: Settings,
        permissions: ['settings.read'],
      },
    ],
  },
];
