/** Authenticated application frame: sidebar, top bar and the routed outlet. */

import { ChevronDown, ChevronsLeft, LogOut, Menu, Search, UserCircle2, X } from 'lucide-react';
import { Suspense, useEffect, useRef, useState } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';

import { AppearanceMenuItems } from '@/app/AppearanceMenu';
import { CommandPalette, useCommandPaletteShortcut } from '@/app/CommandPalette';
import { NAVIGATION, type NavItem } from '@/app/navigation';
import { LoadingPanel } from '@/components/ui/Feedback';
import { Avatar, Badge } from '@/components/ui/Surface';
import { NotificationBell } from '@/features/notifications/NotificationBell';
import { useAuth } from '@/lib/auth';
import { cn } from '@/lib/utils';

function NavEntry({
  item,
  onNavigate,
  collapsed,
}: {
  item: NavItem;
  onNavigate: () => void;
  collapsed: boolean;
}) {
  const Icon = item.icon;
  const base = cn(
    'group relative flex items-center gap-3 rounded-lg py-2 text-sm font-medium',
    'transition-[background,color] duration-150',
    collapsed ? 'justify-center px-2' : 'px-3',
  );

  if (item.planned) {
    return (
      <span
        className={cn(base, 'text-ink-muted cursor-not-allowed')}
        title={`Planned for ${item.planned}`}
        aria-disabled="true"
      >
        <Icon className="size-4.5 shrink-0" aria-hidden="true" />
        {!collapsed && (
          <>
            <span className="flex-1 truncate">{item.label}</span>
            <Badge tone="neutral" className="shrink-0 text-[10px]">
              Soon
            </Badge>
          </>
        )}
      </span>
    );
  }

  return (
    <NavLink
      to={item.to}
      onClick={onNavigate}
      title={collapsed ? item.label : undefined}
      className={({ isActive }) =>
        cn(
          base,
          isActive
            ? 'text-ink bg-raised shadow-card'
            : 'text-ink-soft hover:bg-raised/60 hover:text-ink',
        )
      }
    >
      {({ isActive }) => (
        <>
          {/* The active marker is a bar on the leading edge rather than a
              filled row: it marks position without competing with the content
              for attention. */}
          <span
            aria-hidden="true"
            className={cn(
              'bg-brand-500 absolute inset-y-1.5 -left-2 w-1 rounded-full transition-all duration-200',
              isActive ? 'opacity-100' : 'scale-y-50 opacity-0',
            )}
          />
          <Icon
            className={cn(
              'size-4.5 shrink-0 transition-colors',
              isActive ? 'text-brand-500' : 'text-ink-muted group-hover:text-ink-soft',
            )}
            aria-hidden="true"
          />
          {!collapsed && <span className="truncate">{item.label}</span>}
        </>
      )}
    </NavLink>
  );
}

function UserMenu() {
  const { user, signOut } = useAuth();
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();

  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [open]);

  const handleSignOut = async () => {
    await signOut();
    navigate('/sign-in', { replace: true });
  };

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-haspopup="menu"
        className="hover:bg-sunken flex items-center gap-2 rounded-lg py-1 pr-2 pl-1 transition-colors"
      >
        <Avatar name={user?.username ?? '?'} size="sm" />
        <span className="text-ink hidden max-w-32 truncate text-sm font-medium sm:inline">
          {user?.username}
        </span>
        <ChevronDown
          className={cn(
            'text-ink-muted size-4 transition-transform duration-200',
            open && 'rotate-180',
          )}
          aria-hidden="true"
        />
      </button>

      {open && (
        <div
          role="menu"
          style={{ animation: 'scale-in var(--duration-fast) var(--ease-out-soft)' }}
          className="border-line glass-strong shadow-overlay edge-light absolute right-0 z-30 mt-2 w-64 origin-top-right rounded-[var(--radius-card)] border p-1.5"
        >
          <div className="border-line flex items-center gap-3 border-b px-2.5 py-2.5">
            <Avatar name={user?.username ?? '?'} size="md" />
            <div className="min-w-0">
              <p className="text-ink truncate text-sm font-medium">{user?.username}</p>
              {user?.email && <p className="text-ink-muted truncate text-xs">{user.email}</p>}
            </div>
          </div>
          <div className="flex flex-wrap gap-1 px-2.5 py-2">
            {user?.roles.map((role) => (
              <Badge key={role.code} tone="brand">
                {role.name}
              </Badge>
            ))}
          </div>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              navigate('/my-profile');
            }}
            className="text-ink-soft hover:bg-sunken hover:text-ink flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-sm transition-colors"
          >
            <UserCircle2 className="size-4" aria-hidden="true" />
            My profile
          </button>

          <AppearanceMenuItems />

          <div className="border-line my-1 border-t" aria-hidden="true" />

          <button
            type="button"
            role="menuitem"
            onClick={handleSignOut}
            className="text-danger-700 dark:text-danger-400 hover:bg-danger-50 dark:hover:bg-danger-900/30 flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-sm transition-colors"
          >
            <LogOut className="size-4" aria-hidden="true" />
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}

export function AppShell() {
  const { canAny } = useAuth();
  const location = useLocation();

  /*
   * The mobile drawer's open state is *derived* from the route rather than
   * synchronised to it: we record the path it was opened on, and it counts as
   * open only while that is still the current path.
   *
   * Navigating therefore closes it automatically - including via the browser's
   * back and forward buttons - with no effect watching the location and calling
   * setState, which would cost an extra render and briefly leave the drawer
   * covering the page it had just opened.
   */
  const [openedOnPath, setOpenedOnPath] = useState<string | null>(null);
  const mobileOpen = openedOnPath === location.pathname;
  const setMobileOpen = (open: boolean) => setOpenedOnPath(open ? location.pathname : null);

  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem('iams.sidebar') === 'collapsed';
    } catch {
      return false;
    }
  });

  const toggleCollapsed = () => {
    setCollapsed((value) => {
      const next = !value;
      try {
        localStorage.setItem('iams.sidebar', next ? 'collapsed' : 'expanded');
      } catch {
        // A preference that cannot be stored is not worth failing over.
      }
      return next;
    });
  };

  const [paletteOpen, setPaletteOpen] = useState(false);
  useCommandPaletteShortcut(() => setPaletteOpen(true));

  const sections = NAVIGATION.map((section) => ({
    ...section,
    items: section.items.filter((item) => canAny(...item.permissions)),
  })).filter((section) => section.items.length > 0);

  const sidebar = (
    <nav
      className={cn(
        'flex h-full flex-col gap-6 overflow-y-auto px-4 py-5',
        collapsed && 'px-3',
      )}
      aria-label="Main"
    >
      {sections.map((section) => (
        <div key={section.heading}>
          {!collapsed && (
            <p className="text-ink-muted px-3 pb-2 text-[10px] font-semibold tracking-[0.14em] uppercase">
              {section.heading}
            </p>
          )}
          {collapsed && <div className="border-line mb-2 border-t" aria-hidden="true" />}
          <div className="space-y-0.5">
            {section.items.map((item) => (
              <NavEntry
                key={item.to}
                item={item}
                collapsed={collapsed}
                onNavigate={() => setMobileOpen(false)}
              />
            ))}
          </div>
        </div>
      ))}
    </nav>
  );

  return (
    <div className="min-h-dvh">
      <a href="#main-content" className="skip-link">
        Skip to main content
      </a>

      <header className="h-topbar border-line glass-strong sticky top-0 z-30 flex items-center gap-3 border-b px-4">
        <button
          type="button"
          onClick={() => setMobileOpen(!mobileOpen)}
          aria-expanded={mobileOpen}
          aria-controls="sidebar"
          aria-label={mobileOpen ? 'Close navigation' : 'Open navigation'}
          className="text-ink-soft hover:bg-sunken rounded-lg p-2 transition-colors lg:hidden"
        >
          {mobileOpen ? (
            <X className="size-5" aria-hidden="true" />
          ) : (
            <Menu className="size-5" aria-hidden="true" />
          )}
        </button>

        <div className="flex min-w-0 items-center gap-2.5">
          <span
            aria-hidden="true"
            className="from-brand-500 to-brand-700 shadow-glow grid size-8 shrink-0 place-items-center rounded-lg bg-linear-to-br text-sm font-bold text-white"
          >
            I
          </span>
          <span className="text-ink hidden truncate font-semibold tracking-tight sm:inline">
            Ishvara AMS
          </span>
        </div>

        {/* The command palette's trigger doubles as the search affordance, so
            there is one place to look for anything rather than two. */}
        <button
          type="button"
          onClick={() => setPaletteOpen(true)}
          className={cn(
            'border-line bg-sunken/60 text-ink-muted ml-2 hidden items-center gap-2 rounded-lg border',
            'hover:border-line-strong hover:text-ink-soft px-3 py-1.5 text-sm transition-colors md:flex',
            'w-56 lg:w-72',
          )}
        >
          <Search className="size-4" aria-hidden="true" />
          <span className="flex-1 text-left">Search or jump to...</span>
          <kbd className="border-line bg-raised text-ink-muted rounded border px-1.5 py-0.5 font-sans text-[10px]">
            Ctrl K
          </kbd>
        </button>

        <div className="ml-auto flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => setPaletteOpen(true)}
            aria-label="Search"
            className="text-ink-soft hover:bg-sunken rounded-lg p-2 transition-colors md:hidden"
          >
            <Search className="size-5" aria-hidden="true" />
          </button>
          <NotificationBell />
          <UserMenu />
        </div>
      </header>

      <div className="flex">
        <aside
          id="sidebar"
          className={cn(
            'top-topbar h-[calc(100dvh-var(--spacing-topbar))] shrink-0',
            'border-line hidden border-r lg:sticky lg:block',
            'transition-[width] duration-300 [transition-timing-function:var(--ease-out-soft)]',
            collapsed ? 'w-sidebar-collapsed' : 'w-sidebar',
          )}
        >
          <div className="flex h-full flex-col">
            <div className="flex-1 overflow-y-auto">{sidebar}</div>
            <div className="border-line border-t p-3">
              <button
                type="button"
                onClick={toggleCollapsed}
                aria-label={collapsed ? 'Expand the sidebar' : 'Collapse the sidebar'}
                className={cn(
                  'text-ink-muted hover:bg-sunken hover:text-ink-soft flex w-full items-center gap-2.5',
                  'rounded-lg px-3 py-2 text-sm transition-colors',
                  collapsed && 'justify-center px-2',
                )}
              >
                <ChevronsLeft
                  className={cn(
                    'size-4.5 transition-transform duration-300',
                    collapsed && 'rotate-180',
                  )}
                  aria-hidden="true"
                />
                {!collapsed && <span>Collapse</span>}
              </button>
            </div>
          </div>
        </aside>

        {mobileOpen && (
          <>
            <button
              type="button"
              aria-label="Close navigation"
              onClick={() => setMobileOpen(false)}
              className="bg-veil fixed inset-0 z-20 lg:hidden"
              style={{ animation: 'fade-in var(--duration-fast) ease-out' }}
            />
            <aside
              className="top-topbar w-sidebar border-line glass-strong fixed inset-y-0 z-20 border-r lg:hidden"
              style={{ animation: 'rise var(--duration-base) var(--ease-out-soft)' }}
            >
              {sidebar}
            </aside>
          </>
        )}

        <main id="main-content" className="min-w-0 flex-1 px-4 py-6 sm:px-6 lg:px-8">
          {/*
            Every authenticated page is loaded on demand, so one Suspense
            boundary sits here rather than per route: a page arriving late shows
            the shell with a loading panel instead of a blank screen, and the
            navigation stays usable while it arrives.
          */}
          <Suspense fallback={<LoadingPanel label="Loading the page..." />}>
            <Outlet />
          </Suspense>
        </main>
      </div>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </div>
  );
}
