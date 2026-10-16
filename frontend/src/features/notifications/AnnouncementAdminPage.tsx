/** Announcement authoring, for whoever holds `announcement.manage`. */

import { PageHeader } from '@/components/ui/Surface';
import { AnnouncementAdminPanel } from '@/features/notifications/AnnouncementAdminPanel';

export function AnnouncementAdminPage() {
  return (
    <div>
      <PageHeader
        title="Announcements"
        description="Write a notice, choose who it reaches, publish or schedule it, and see who has read it. Nothing is deleted: a published notice is withdrawn with a reason."
      />
      <AnnouncementAdminPanel />
    </div>
  );
}
