/**
 * Display mode and theme colour, as two rows of the account menu.
 *
 * Deliberately not a settings panel. These are the only two appearance choices
 * the system offers, both take effect the instant they are clicked, and neither
 * needs explaining - so putting them anywhere other than one hop from the
 * avatar would be making them harder to reach than they are to understand.
 *
 * Each row opens a flyout on hover or on click. It opens to the left, because
 * the account menu is already pinned to the right edge of the window and a
 * submenu opening right would land off-screen.
 */

import { Check, ChevronRight, Monitor, Moon, Palette, Sun } from 'lucide-react';
import { useState, type ReactNode } from 'react';

import { PALETTES, type Mode } from '@/lib/appearance';
import { useAppearance } from '@/lib/theme';
import { cn } from '@/lib/utils';

const MODES: {
  value: Mode;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
}[] = [
  { value: 'light', label: 'Light', icon: Sun },
  { value: 'dark', label: 'Dark', icon: Moon },
  { value: 'system', label: 'System', icon: Monitor },
];

const ROW =
  'flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-sm transition-colors';

export function AppearanceMenuItems() {
  // One at a time: opening the colours closes the modes, so the two flyouts
  // never overlap each other.
  const [openMenu, setOpenMenu] = useState<'mode' | 'colour' | null>(null);
  const appearance = useAppearance();

  const activeMode = MODES.find((mode) => mode.value === appearance.mode) ?? MODES[2]!;
  const activeColour = PALETTES.find((palette) => palette.id === appearance.palette);

  return (
    <>
      <Submenu
        open={openMenu === 'mode'}
        onOpen={() => setOpenMenu('mode')}
        onClose={() => setOpenMenu(null)}
        icon={<activeMode.icon className="size-4" aria-hidden="true" />}
        label="Display mode"
      >
        {MODES.map((mode) => {
          const Icon = mode.icon;
          const selected = mode.value === appearance.mode;
          return (
            <button
              key={mode.value}
              type="button"
              role="menuitemradio"
              aria-checked={selected}
              onClick={() => appearance.set({ mode: mode.value })}
              className={cn(
                ROW,
                selected ? 'text-ink font-medium' : 'text-ink-soft hover:text-ink',
                'hover:bg-sunken',
              )}
            >
              <Icon className="text-ink-muted size-4 shrink-0" aria-hidden="true" />
              <span className="flex-1 truncate">{mode.label}</span>
              {selected && (
                <Check className="text-brand-500 size-4 shrink-0" aria-hidden="true" />
              )}
            </button>
          );
        })}
      </Submenu>

      <Submenu
        open={openMenu === 'colour'}
        onOpen={() => setOpenMenu('colour')}
        onClose={() => setOpenMenu(null)}
        icon={<Palette className="size-4" aria-hidden="true" />}
        label="Theme colour"
        trailing={
          activeColour && (
            <span
              aria-hidden="true"
              className="size-3 shrink-0 rounded-full"
              style={{ backgroundColor: activeColour.swatch }}
            />
          )
        }
      >
        {PALETTES.map((palette) => {
          const selected = palette.id === appearance.palette;
          return (
            <button
              key={palette.id}
              type="button"
              role="menuitemradio"
              aria-checked={selected}
              onClick={() => appearance.set({ palette: palette.id })}
              className={cn(
                ROW,
                selected ? 'text-ink font-medium' : 'text-ink-soft hover:text-ink',
                'hover:bg-sunken',
              )}
            >
              <span
                aria-hidden="true"
                className="size-4 shrink-0 rounded-[0.3rem] ring-1 ring-black/10 ring-inset dark:ring-white/15"
                style={{ backgroundColor: palette.swatch }}
              />
              <span className="flex-1 truncate">{palette.name}</span>
              {selected && (
                <Check className="text-brand-500 size-4 shrink-0" aria-hidden="true" />
              )}
            </button>
          );
        })}
      </Submenu>
    </>
  );
}

function Submenu({
  open,
  onOpen,
  onClose,
  icon,
  label,
  trailing,
  children,
}: {
  open: boolean;
  onOpen: () => void;
  onClose: () => void;
  icon: ReactNode;
  label: string;
  trailing?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="relative" onMouseEnter={onOpen}>
      <button
        type="button"
        role="menuitem"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => (open ? onClose() : onOpen())}
        onKeyDown={(event) => {
          if (event.key === 'ArrowLeft') {
            event.preventDefault();
            onOpen();
          }
        }}
        className={cn(
          ROW,
          open ? 'bg-sunken text-ink' : 'text-ink-soft hover:bg-sunken hover:text-ink',
        )}
      >
        <span className="text-ink-muted shrink-0">{icon}</span>
        <span className="flex-1 truncate">{label}</span>
        {trailing}
        <ChevronRight
          className={cn(
            'text-ink-muted size-4 shrink-0 transition-transform duration-200',
            open && '-rotate-180',
          )}
          aria-hidden="true"
        />
      </button>

      {open && (
        <div
          role="menu"
          aria-label={label}
          style={{ animation: 'scale-in var(--duration-fast) var(--ease-out-soft)' }}
          className={cn(
            'border-line glass-strong shadow-overlay edge-light',
            'absolute top-0 right-full z-40 mr-1.5 w-44 origin-top-right',
            'rounded-[var(--radius-card)] border p-1.5',
          )}
        >
          {children}
        </div>
      )}
    </div>
  );
}
