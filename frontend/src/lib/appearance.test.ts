/**
 * Appearance settings.
 *
 * Worth testing because the input is browser storage, which is to say
 * untrusted: an older version of the application, a second tab, or a person
 * editing localStorage by hand can all leave something there that is not an
 * `Appearance`. The failure if validation is skipped is not an exception - it
 * is `data-palette="<junk>"` on the document element, which matches no colour
 * rule, leaves `--brand-hue` undefined, and renders every brand colour as
 * nothing at all. A blank interface is a far worse outcome than a default one,
 * so each field is checked individually and falls back on its own.
 */

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import {
  DEFAULT_APPEARANCE,
  PALETTES,
  applyAppearance,
  readStoredAppearance,
  storeAppearance,
  type Appearance,
} from '@/lib/appearance';

const KEY = 'iams.appearance';

function attributes(): { mode: string | null; palette: string | null } {
  const root = document.documentElement;
  return {
    mode: root.getAttribute('data-mode'),
    palette: root.getAttribute('data-palette'),
  };
}

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute('data-mode');
  document.documentElement.removeAttribute('data-palette');
});

afterEach(() => {
  localStorage.clear();
});

describe('readStoredAppearance', () => {
  it('returns the defaults when nothing has been stored', () => {
    expect(readStoredAppearance()).toEqual(DEFAULT_APPEARANCE);
  });

  it('returns the defaults rather than throwing on malformed JSON', () => {
    localStorage.setItem(KEY, '{not json');
    expect(readStoredAppearance()).toEqual(DEFAULT_APPEARANCE);
  });

  it('returns the defaults when the stored value is not an object', () => {
    localStorage.setItem(KEY, '"dark"');
    expect(readStoredAppearance()).toEqual(DEFAULT_APPEARANCE);
  });

  it('round-trips a complete, valid setting', () => {
    const stored: Appearance = { mode: 'dark', palette: 'orange' };
    storeAppearance(stored);
    expect(readStoredAppearance()).toEqual(stored);
  });

  it('rejects an unknown colour without discarding the valid field beside it', () => {
    // What an older stored value looks like after the palette list was renamed.
    localStorage.setItem(KEY, JSON.stringify({ mode: 'dark', palette: 'ember' }));
    const result = readStoredAppearance();
    expect(result.palette).toBe(DEFAULT_APPEARANCE.palette);
    expect(result.mode).toBe('dark');
  });

  it('rejects an unknown mode without discarding the colour beside it', () => {
    localStorage.setItem(KEY, JSON.stringify({ mode: 'sepia', palette: 'teal' }));
    expect(readStoredAppearance()).toEqual({ mode: DEFAULT_APPEARANCE.mode, palette: 'teal' });
  });

  it('ignores settings that no longer exist rather than choking on them', () => {
    localStorage.setItem(
      KEY,
      JSON.stringify({ mode: 'light', palette: 'green', radius: 'round', ambient: false }),
    );
    expect(readStoredAppearance()).toEqual({ mode: 'light', palette: 'green' });
  });
});

describe('applyAppearance', () => {
  it('writes no attribute at all for the defaults', () => {
    applyAppearance(DEFAULT_APPEARANCE);
    expect(attributes()).toEqual({ mode: null, palette: null });
  });

  it('writes both attributes once the choices differ from the defaults', () => {
    applyAppearance({ mode: 'dark', palette: 'pink' });
    expect(attributes()).toEqual({ mode: 'dark', palette: 'pink' });
  });

  it('clears a previous choice rather than leaving it behind', () => {
    applyAppearance({ mode: 'dark', palette: 'red' });
    applyAppearance(DEFAULT_APPEARANCE);
    expect(attributes()).toEqual({ mode: null, palette: null });
  });

  it('declares the colour scheme so native controls follow the chosen mode', () => {
    applyAppearance({ ...DEFAULT_APPEARANCE, mode: 'dark' });
    expect(document.documentElement.style.colorScheme).toBe('dark');

    applyAppearance({ ...DEFAULT_APPEARANCE, mode: 'system' });
    expect(document.documentElement.style.colorScheme).toBe('light dark');
  });
});

describe('PALETTES', () => {
  it('includes the default colour, so the menu always shows a selection', () => {
    expect(PALETTES.map((palette) => palette.id)).toContain(DEFAULT_APPEARANCE.palette);
  });

  it('offers a choice worth opening a submenu for', () => {
    expect(PALETTES.length).toBeGreaterThanOrEqual(5);
  });

  it('has a unique id, a name and a drawable swatch for every entry', () => {
    const ids = PALETTES.map((palette) => palette.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const palette of PALETTES) {
      expect(palette.name).not.toBe('');
      expect(palette.swatch).toMatch(/^oklch\(/);
    }
  });
});
