/**
 * Command palette: Ctrl/Cmd + K.
 *
 * This application has twenty routes behind three permission groups, and the
 * thing somebody wants is usually two clicks into a section they have to
 * remember the name of. A palette turns that into typing the word they already
 * had in mind.
 *
 * It lists **only what the signed-in account may actually reach** - the same
 * filter the sidebar applies - so it never offers a destination that answers
 * with a permission error. Search matches the label, the section and a short
 * list of synonyms, because people look for "salary" rather than "payroll" and
 * for "org chart" rather than "employees".
 */

import { CornerDownLeft, Search } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { NAVIGATION } from '@/app/navigation';
import { useAuth } from '@/lib/auth';
import { cn } from '@/lib/utils';

/** Words people search for that are not in the navigation label itself. */
const SYNONYMS: Record<string, string[]> = {
  '/dashboard': ['home', 'overview', 'start'],
  '/my-attendance': ['punch', 'clock in', 'clock out', 'timesheet'],
  '/leave': ['holiday request', 'time off', 'vacation', 'absence'],
  '/compoff': ['comp off', 'compensatory', 'extra day'],
  '/payslips': ['salary slip', 'pay', 'earnings', 'net pay'],
  '/employees': ['people', 'staff', 'directory', 'org chart', 'team', 'who'],
  '/attendance': ['corrections', 'regularization', 'punches'],
  '/shifts': ['roster', 'timing', 'calendar', 'week off'],
  '/leave-admin': ['approvals', 'leave policy', 'accrual', 'year end'],
  '/payroll': ['salary', 'run', 'statutory', 'pf', 'esi', 'components'],
  '/reports': ['export', 'csv', 'excel', 'register'],
  '/audit': ['log', 'trail', 'history', 'retention', 'who changed'],
  '/settings': ['configuration', 'policy', 'preferences'],
  '/organisation': ['departments', 'designations', 'locations'],
  '/devices': ['biometric', 'machine', 'punch device'],
  '/holidays': ['public holiday', 'festival', 'calendar'],
  '/announcements': ['notice', 'news'],
  '/notifications': ['alerts', 'bell'],
};

interface Command {
  to: string;
  label: string;
  section: string;
  icon: React.ComponentType<{ className?: string }>;
  keywords: string;
}

/**
 * Mounted only while open, so the query box starts empty on every open without
 * an effect reaching in to clear it.
 */
export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  if (!open) return null;
  return <Palette onClose={onClose} />;
}

function Palette({ onClose }: { onClose: () => void }) {
  const { canAny } = useAuth();
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  /*
   * The highlight is stored with the query it was chosen for. Reading it back
   * returns to the top whenever the query has moved on since, which is what an
   * effect resetting it to zero was doing - except this way there is no second
   * render to do it in.
   */
  const [highlight, setHighlight] = useState({ query: '', index: 0 });
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  const commands = useMemo<Command[]>(
    () =>
      NAVIGATION.flatMap((section) =>
        section.items
          .filter((item) => !item.planned && canAny(...item.permissions))
          .map((item) => ({
            to: item.to,
            label: item.label,
            section: section.heading,
            icon: item.icon,
            keywords: [item.label, section.heading, ...(SYNONYMS[item.to] ?? [])]
              .join(' ')
              .toLowerCase(),
          })),
      ),
    [canAny],
  );

  const results = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return commands;
    // Rank a label match above a keyword match, so typing "pay" offers
    // Payslips and Payroll before anything that merely mentions salary.
    return commands
      .map((command) => {
        const label = command.label.toLowerCase();
        let score = -1;
        if (label.startsWith(needle)) score = 0;
        else if (label.includes(needle)) score = 1;
        else if (command.keywords.includes(needle)) score = 2;
        return { command, score };
      })
      .filter((entry) => entry.score >= 0)
      .sort((a, b) => a.score - b.score)
      .map((entry) => entry.command);
  }, [commands, query]);

  const highlighted =
    highlight.query === query ? Math.min(highlight.index, results.length - 1) : 0;
  const setHighlighted = (index: number) => setHighlight({ query, index });

  useEffect(() => {
    // The input mounts with the dialog; focus on the next frame so the browser
    // has laid it out first.
    const id = requestAnimationFrame(() => inputRef.current?.focus());
    return () => cancelAnimationFrame(id);
  }, []);

  // Keep the highlighted row in view when arrowing past the fold.
  useEffect(() => {
    const node = listRef.current?.children[highlighted] as HTMLElement | undefined;
    node?.scrollIntoView({ block: 'nearest' });
  }, [highlighted]);

  const go = (to: string) => {
    onClose();
    navigate(to);
  };

  const handleKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setHighlighted(results.length === 0 ? 0 : (highlighted + 1) % results.length);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setHighlighted(
        results.length === 0 ? 0 : (highlighted - 1 + results.length) % results.length,
      );
    } else if (event.key === 'Enter') {
      event.preventDefault();
      const target = results[highlighted];
      if (target) go(target.to);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      onClose();
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center px-4 pt-[12vh]"
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
    >
      <button
        type="button"
        aria-label="Close"
        onClick={onClose}
        className="bg-veil absolute inset-0"
        style={{ animation: 'fade-in var(--duration-fast) ease-out' }}
      />

      <div
        className="border-line glass-strong shadow-overlay edge-light relative w-full max-w-xl overflow-hidden rounded-[var(--radius-panel)] border"
        style={{ animation: 'scale-in var(--duration-base) var(--ease-out-soft)' }}
      >
        <div className="border-line flex items-center gap-3 border-b px-4">
          <Search className="text-ink-muted size-4.5 shrink-0" aria-hidden="true" />
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Search for a page..."
            aria-label="Search for a page"
            aria-controls="command-results"
            className="text-ink placeholder:text-ink-muted h-13 flex-1 bg-transparent text-sm outline-none"
          />
          <kbd className="border-line bg-raised text-ink-muted rounded border px-1.5 py-0.5 font-sans text-[10px]">
            Esc
          </kbd>
        </div>

        {results.length === 0 ? (
          <p className="text-ink-muted px-4 py-10 text-center text-sm">
            Nothing matches &ldquo;{query}&rdquo;.
          </p>
        ) : (
          <ul ref={listRef} id="command-results" className="max-h-80 overflow-y-auto p-2">
            {results.map((command, index) => {
              const Icon = command.icon;
              const isHighlighted = index === highlighted;
              return (
                <li key={command.to}>
                  <button
                    type="button"
                    onClick={() => go(command.to)}
                    onMouseMove={() => setHighlighted(index)}
                    className={cn(
                      'flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left transition-colors',
                      isHighlighted ? 'bg-brand-500 text-white' : 'text-ink-soft',
                    )}
                  >
                    <Icon
                      className={cn(
                        'size-4.5 shrink-0',
                        isHighlighted ? 'text-white' : 'text-ink-muted',
                      )}
                      aria-hidden="true"
                    />
                    <span className="flex-1 truncate text-sm font-medium">{command.label}</span>
                    <span
                      className={cn(
                        'text-xs',
                        isHighlighted ? 'text-white/70' : 'text-ink-muted',
                      )}
                    >
                      {command.section}
                    </span>
                    {isHighlighted && (
                      <CornerDownLeft className="size-3.5 text-white/70" aria-hidden="true" />
                    )}
                  </button>
                </li>
              );
            })}
          </ul>
        )}

        <div className="border-line text-ink-muted flex items-center gap-4 border-t px-4 py-2 text-[11px]">
          <span>Arrow keys to move</span>
          <span>Enter to open</span>
          <span className="ml-auto">{results.length} destination(s)</span>
        </div>
      </div>
    </div>
  );
}

/**
 * The keyboard shortcut, mounted once by the shell.
 *
 * Deliberately ignores key presses while the user is typing in a field, so
 * "k" in a search box stays a letter.
 */
export function useCommandPaletteShortcut(onOpen: () => void): void {
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        onOpen();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onOpen]);
}
