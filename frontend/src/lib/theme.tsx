/**
 * The appearance provider.
 *
 * Thin by design: the settings, their validation and the DOM writes all live in
 * `appearance.ts`, which has no React dependency. This file exists only to make
 * the current settings readable from components and changeable from the picker.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import {
  DEFAULT_APPEARANCE,
  applyAppearance,
  readStoredAppearance,
  storeAppearance,
  withAppearanceTransition,
  type Appearance,
} from '@/lib/appearance';

interface AppearanceContextValue extends Appearance {
  set: (patch: Partial<Appearance>) => void;
  reset: () => void;
  /** The mode actually in force, with `system` resolved. For icons and labels. */
  resolvedMode: 'light' | 'dark';
}

const AppearanceContext = createContext<AppearanceContextValue | null>(null);

export function AppearanceProvider({ children }: { children: ReactNode }) {
  const [appearance, setAppearance] = useState<Appearance>(readStoredAppearance);
  const [systemDark, setSystemDark] = useState(
    () => window.matchMedia('(prefers-color-scheme: dark)').matches,
  );

  // Applied in an effect rather than during render: touching the DOM while
  // rendering is a side effect React is entitled to run twice.
  useEffect(() => {
    applyAppearance(appearance);
  }, [appearance]);

  // Tracked only so `resolvedMode` can label the toggle correctly; the colours
  // themselves follow the media query without any JavaScript.
  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const handler = (event: MediaQueryListEvent) => setSystemDark(event.matches);
    media.addEventListener('change', handler);
    return () => media.removeEventListener('change', handler);
  }, []);

  const set = useCallback((patch: Partial<Appearance>) => {
    withAppearanceTransition(() => {
      setAppearance((current) => {
        const next = { ...current, ...patch };
        storeAppearance(next);
        return next;
      });
    });
  }, []);

  const reset = useCallback(() => {
    withAppearanceTransition(() => {
      setAppearance(DEFAULT_APPEARANCE);
      storeAppearance(null);
    });
  }, []);

  const value = useMemo<AppearanceContextValue>(
    () => ({
      ...appearance,
      set,
      reset,
      resolvedMode:
        appearance.mode === 'system' ? (systemDark ? 'dark' : 'light') : appearance.mode,
    }),
    [appearance, set, reset, systemDark],
  );

  return <AppearanceContext.Provider value={value}>{children}</AppearanceContext.Provider>;
}

export function useAppearance(): AppearanceContextValue {
  const context = useContext(AppearanceContext);
  if (context === null) {
    throw new Error('useAppearance must be used inside an AppearanceProvider.');
  }
  return context;
}
