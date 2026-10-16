/**
 * Authentication state and the permission helpers the UI uses.
 *
 * Important: `can()` drives *navigation and affordances only*. Every protected
 * operation is authorized again by the backend, which is the actual control. If
 * these two ever disagree, the backend wins and the user sees a clear 403 - a
 * hidden button is a convenience, never a security boundary.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  type ReactNode,
} from 'react';

import { api, ApiError, onSessionExpired, tokenStore } from '@/lib/api';

export interface RoleSummary {
  code: string;
  name: string;
}

export interface CurrentUser {
  id: number;
  username: string;
  email: string | null;
  employee_id: number | null;
  is_active: boolean;
  must_change_password: boolean;
  last_login_at: string | null;
  roles: RoleSummary[];
  permissions: string[];
}

interface LoginResult {
  access_token: string;
  refresh_token: string;
  expires_at: string;
  refresh_expires_at: string;
  must_change_password: boolean;
}

interface AuthContextValue {
  user: CurrentUser | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  mustChangePassword: boolean;
  signIn: (identifier: string, password: string) => Promise<LoginResult>;
  signOut: () => Promise<void>;
  refreshUser: () => Promise<void>;
  can: (...permissions: string[]) => boolean;
  canAny: (...permissions: string[]) => boolean;
  hasRole: (role: string) => boolean;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export const CURRENT_USER_QUERY_KEY = ['auth', 'me'] as const;

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();

  const {
    data: user,
    isLoading,
    isFetched,
  } = useQuery({
    queryKey: CURRENT_USER_QUERY_KEY,
    queryFn: () => api.get<CurrentUser>('/auth/me'),
    // Only ask the server who we are if we hold a token at all.
    enabled: tokenStore.get() !== null,
    retry: (failureCount, error) =>
      // Re-asking after a 401/403 cannot help; anything else may be transient.
      !(error instanceof ApiError && (error.isAuthProblem || error.status === 403)) &&
      failureCount < 2,
    staleTime: 60_000,
  });

  // When a refresh ultimately fails, drop cached user data so the app cannot
  // keep rendering a signed-in shell against a dead session.
  useEffect(
    () =>
      onSessionExpired(() => {
        queryClient.setQueryData(CURRENT_USER_QUERY_KEY, null);
        queryClient.clear();
      }),
    [queryClient],
  );

  const signInMutation = useMutation({
    mutationFn: async ({ identifier, password }: { identifier: string; password: string }) => {
      const result = await api.post<LoginResult>(
        '/auth/login',
        { identifier, password },
        { anonymous: true },
      );
      tokenStore.set({
        accessToken: result.access_token,
        refreshToken: result.refresh_token,
      });
      return result;
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: CURRENT_USER_QUERY_KEY });
    },
  });

  const signIn = useCallback(
    (identifier: string, password: string) =>
      signInMutation.mutateAsync({ identifier, password }),
    [signInMutation],
  );

  const signOut = useCallback(async () => {
    const tokens = tokenStore.get();
    if (tokens) {
      try {
        await api.post('/auth/logout', { refresh_token: tokens.refreshToken });
      } catch {
        // Sign-out must always succeed locally. If the server call fails the
        // token may still be live, but holding the user in a session they asked
        // to leave is worse; it expires on its own.
      }
    }
    tokenStore.clear();
    queryClient.setQueryData(CURRENT_USER_QUERY_KEY, null);
    queryClient.clear();
  }, [queryClient]);

  const refreshUser = useCallback(async () => {
    await queryClient.invalidateQueries({ queryKey: CURRENT_USER_QUERY_KEY });
  }, [queryClient]);

  const value = useMemo<AuthContextValue>(() => {
    const granted = new Set(user?.permissions ?? []);
    const roles = new Set(user?.roles.map((role) => role.code) ?? []);

    return {
      user: user ?? null,
      // Treat "no token at all" as resolved rather than perpetually loading.
      isLoading: tokenStore.get() !== null && isLoading && !isFetched,
      isAuthenticated: Boolean(user),
      mustChangePassword: Boolean(user?.must_change_password),
      signIn,
      signOut,
      refreshUser,
      can: (...permissions: string[]) => permissions.every((code) => granted.has(code)),
      canAny: (...permissions: string[]) => permissions.some((code) => granted.has(code)),
      hasRole: (role: string) => roles.has(role),
    };
  }, [user, isLoading, isFetched, signIn, signOut, refreshUser]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used inside an <AuthProvider>.');
  }
  return context;
}
