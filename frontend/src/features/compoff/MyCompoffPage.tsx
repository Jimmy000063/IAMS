/** Employee comp-off: balance, credits and requests. */

import { PageHeader } from '@/components/ui/Surface';
import { CompoffPanel } from '@/features/compoff/CompoffPanel';

export function MyCompoffPage() {
  return (
    <div>
      <PageHeader
        title="Comp-off"
        description="Time off earned by working on a weekly off or a holiday. Each credit expires on its own date, so they are listed one by one rather than as a single total."
      />
      <CompoffPanel />
    </div>
  );
}
