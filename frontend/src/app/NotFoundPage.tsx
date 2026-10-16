import { Link } from 'react-router-dom';

import { Card, CardBody, PageHeader } from '@/components/ui/Surface';

export function NotFoundPage() {
  return (
    <div>
      <PageHeader title="Page not found" />
      <Card>
        <CardBody>
          <p className="text-ink-soft text-sm">
            The page you asked for does not exist. It may have been moved, or it may be part of
            a feature that has not been built yet.
          </p>
          <Link
            to="/dashboard"
            className="text-brand-600 mt-4 inline-block text-sm font-medium underline-offset-4 hover:underline"
          >
            Back to dashboard
          </Link>
        </CardBody>
      </Card>
    </div>
  );
}
