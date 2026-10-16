/**
 * Route guards.
 *
 * These make the UI coherent - sending an unauthenticated visitor to sign-in,
 * and a user with a temporary password to the change-password screen. They are
 * *not* the security control: every protected endpoint re-checks authorization
 * server-side, so bypassing a guard in the browser gains nothing.
 */

import type { ReactNode } from 'react';
import { Navigate, Outlet, useLocation } from 'react-router-dom';

import { Button } from '@/components/ui/Button';
import { LoadingPanel } from '@/components/ui/Feedback';
import { Card, CardBody, PageHeader } from '@/components/ui/Surface';
import { useAuth } from '@/lib/auth';

export function RequireAuth({ children }: { children?: ReactNode }) {
  const { isAuthenticated, isLoading, mustChangePassword } = useAuth();
  const location = useLocation();

  if (isLoading) return <LoadingPanel label="Checking your session…" />;

  if (!isAuthenticated) {
    // Remember where they were headed so sign-in can return them there.
    return <Navigate to="/sign-in" replace state={{ from: location.pathname }} />;
  }

  if (mustChangePassword) {
    return <Navigate to="/change-password" replace />;
  }

  return <>{children ?? <Outlet />}</>;
}

export function RequireGuest({ children }: { children?: ReactNode }) {
  const { isAuthenticated, isLoading, mustChangePassword } = useAuth();

  if (isLoading) return <LoadingPanel label="Checking your session…" />;
  if (isAuthenticated) {
    return <Navigate to={mustChangePassword ? '/change-password' : '/dashboard'} replace />;
  }
  return <>{children ?? <Outlet />}</>;
}

/**
 * Permission gate for a whole route.
 *
 * Renders an explanatory page rather than redirecting: a silent bounce to the
 * dashboard leaves the user unsure whether the feature exists or they simply
 * lack access.
 */
export function RequirePermission({
  permissions,
  mode = 'all',
  children,
}: {
  permissions: string[];
  mode?: 'all' | 'any';
  children?: ReactNode;
}) {
  const { can, canAny } = useAuth();
  const allowed = mode === 'all' ? can(...permissions) : canAny(...permissions);

  if (!allowed) {
    return (
      <div>
        <PageHeader title="Access restricted" />
        <Card>
          <CardBody className="space-y-4">
            <p className="text-ink-soft text-sm">
              Your account does not have permission to view this page. If you believe you should
              have access, contact HR.
            </p>
            <Button variant="secondary" onClick={() => window.history.back()}>
              Go back
            </Button>
          </CardBody>
        </Card>
      </div>
    );
  }

  return <>{children ?? <Outlet />}</>;
}
