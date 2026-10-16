/**
 * Appearance settings: the data and the DOM.
 *
 * Two settings only - how light the interface is, and what colour it is. Both
 * sit in the account menu beside the profile and sign-out items, because that
 * is where people look for them and because a preference worth one click should
 * not cost a settings screen.
 *
 * Deliberately free of React. `applyStoredAppearanceEarly` runs before the
 * first paint, from `main.tsx`, and nothing on that path should drag a provider
 * - or React itself - in with it. The provider that makes these changeable
 * lives in `theme.tsx`.
 *
 * Both settings are written as attributes on the document element, which is
 * what the token definitions in `index.css` key off. No component subscribes to
 * a colour: changing one is a single attribute write and the browser
 * recalculates every derived custom property. That is why switching is instant
 * and does not re-render the application.
 *
 * "System" mode removes the attribute entirely rather than resolving it to a
 * value, so the CSS media query stays in charge and the interface follows the
 * operating system as it changes during the day - without any component
 * re-rendering at dusk.
 *
 * The preference is stored per browser, not per account: which theme suits a
 * screen is a property of where somebody is sitting, not of who they are. It is
 * also why this is not a server-side setting - it would be a row per person per
 * device, carrying no business meaning.
 */

export type Mode = 'light' | 'dark' | 'system';

export type PaletteId =
  'violet' | 'blue' | 'teal' | 'green' | 'orange' | 'red' | 'pink' | 'slate';

export interface Appearance {
  mode: Mode;
  palette: PaletteId;
}

export const DEFAULT_APPEARANCE: Appearance = {
  mode: 'system',
  palette: 'violet',
};

/**
 * The colours offered, in the order the menu lists them.
 *
 * `swatch` repeats the hue from `index.css` so the menu can draw a dot without
 * mounting the palette first. The two files have to agree; a mismatch would be
 * cosmetic - a slightly wrong dot - rather than a fault.
 */
export const PALETTES: { id: PaletteId; name: string; swatch: string }[] = [
  { id: 'violet', name: 'Violet', swatch: 'oklch(0.606 0.173 277)' },
  { id: 'blue', name: 'Blue', swatch: 'oklch(0.606 0.173 248)' },
  { id: 'teal', name: 'Teal', swatch: 'oklch(0.606 0.159 193)' },
  { id: 'green', name: 'Green', swatch: 'oklch(0.606 0.152 156)' },
  { id: 'orange', name: 'Orange', swatch: 'oklch(0.646 0.164 52)' },
  { id: 'red', name: 'Red', swatch: 'oklch(0.606 0.179 25)' },
  { id: 'pink', name: 'Pink', swatch: 'oklch(0.63 0.182 348)' },
  { id: 'slate', name: 'Slate', swatch: 'oklch(0.606 0.042 265)' },
];

const PALETTE_IDS = new Set<string>(PALETTES.map((palette) => palette.id));

const STORAGE_KEY = 'iams.appearance';

export function readStoredAppearance(): Appearance {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw === null) return DEFAULT_APPEARANCE;
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== 'object' || parsed === null) return DEFAULT_APPEARANCE;
    const record = parsed as Record<string, unknown>;

    // Each field is validated separately rather than trusting the shape: this
    // is browser storage, which an older version of the application, another
    // tab, or a curious user may have written.
    return {
      mode:
        record.mode === 'light' || record.mode === 'dark' || record.mode === 'system'
          ? record.mode
          : DEFAULT_APPEARANCE.mode,
      palette:
        typeof record.palette === 'string' && PALETTE_IDS.has(record.palette)
          ? (record.palette as PaletteId)
          : DEFAULT_APPEARANCE.palette,
    };
  } catch {
    // Private browsing, blocked storage, or malformed JSON. The default is
    // correct in all three cases.
    return DEFAULT_APPEARANCE;
  }
}

export function storeAppearance(appearance: Appearance | null): void {
  try {
    if (appearance === null) localStorage.removeItem(STORAGE_KEY);
    else localStorage.setItem(STORAGE_KEY, JSON.stringify(appearance));
  } catch {
    // A preference that cannot be stored still applies for this session.
  }
}

export function applyAppearance(appearance: Appearance): void {
  const root = document.documentElement;

  if (appearance.mode === 'system') root.removeAttribute('data-mode');
  else root.setAttribute('data-mode', appearance.mode);

  // The default colour is expressed by the absence of an attribute, so the
  // markup stays clean for anyone who never changes it.
  if (appearance.palette === DEFAULT_APPEARANCE.palette) root.removeAttribute('data-palette');
  else root.setAttribute('data-palette', appearance.palette);

  // Tell the browser which scheme the page is in, so form controls, scrollbars
  // and the window chrome match. Without this, a dark page keeps light
  // scrollbars whenever dark mode was chosen rather than inherited.
  root.style.colorScheme = appearance.mode === 'system' ? 'light dark' : appearance.mode;
}

/**
 * Paint a short crossfade over a change.
 *
 * The class is attached for the duration and then removed, rather than left on
 * permanently: a global colour transition makes every hover feel laggy and
 * makes a table visibly repaint when its data arrives.
 */
export function withAppearanceTransition(change: () => void): void {
  const root = document.documentElement;
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    change();
    return;
  }
  root.classList.add('theme-transition');
  change();
  window.setTimeout(() => root.classList.remove('theme-transition'), 260);
}

/**
 * Applied before React mounts, from `main.tsx`.
 *
 * Without this the first paint uses the default theme and then corrects itself,
 * which is a white flash for anyone who chose dark and a colour flash for
 * anyone who chose a different colour.
 */
export function applyStoredAppearanceEarly(): void {
  applyAppearance(readStoredAppearance());
}
