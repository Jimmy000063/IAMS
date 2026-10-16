/**
 * HTTP client for the IAMS API.
 *
 * Responsibilities, all in one place so no component has to think about them:
 *
 *  - Attach the bearer access token.
 *  - Normalise the backend's error envelope into a typed `ApiError`.
 *  - Refresh an expired access token once, transparently, and retry the request.
 *  - Coalesce concurrent refreshes so a page that fires five queries at once
 *    does not perform five rotations (which the backend would treat as token
 *    reuse and respond to by revoking every session).
 */

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? '/api/v1';

/** Error codes the UI branches on. Kept in step with `app/core/errors.py`. */
export type ApiErrorCode =
  | 'validation_error'
  | 'authentication_failed'
  | 'not_authenticated'
  | 'permission_denied'
  | 'password_change_required'
  | 'account_inactive'
  | 'account_locked'
  | 'rate_limited'
  | 'not_found'
  | 'conflict'
  | 'policy_violation'
  | 'configuration_missing'
  | 'internal_error'
  | 'network_error'
  | 'http_error';

export interface ApiErrorDetail {
  field: string | null;
  message: string;
  type?: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: ApiErrorCode | string;
  readonly details: ApiErrorDetail[];
  readonly requestId: string | null;

  constructor(args: {
    status: number;
    code: string;
    message: string;
    details?: ApiErrorDetail[];
    requestId?: string | null;
  }) {
    super(args.message);
    this.name = 'ApiError';
    this.status = args.status;
    this.code = args.code;
    this.details = args.details ?? [];
    this.requestId = args.requestId ?? null;
  }

  /** Field-level messages, for feeding back into react-hook-form. */
  get fieldErrors(): Record<string, string> {
    const result: Record<string, string> = {};
    for (const detail of this.details) {
      if (detail.field) result[detail.field] = detail.message;
    }
    return result;
  }

  get isAuthProblem(): boolean {
    return this.code === 'not_authenticated' || this.code === 'authentication_failed';
  }
}

/* -------------------------------------------------------------------------- */
/* Token storage                                                              */
/* -------------------------------------------------------------------------- */

/**
 * Tokens live in `sessionStorage`, not `localStorage`: they are cleared when the
 * tab closes, which is the right default for a shared or office machine.
 *
 * This is a deliberate, documented trade-off. Any browser-readable storage is
 * exposed to script injection; the stronger option is an httpOnly refresh
 * cookie, which needs CSRF protection and a cookie-aware backend. The backend
 * supports the bearer scheme today, so the frontend uses it and keeps the
 * blast radius small via short access-token lifetimes and rotation.
 */
const ACCESS_TOKEN_KEY = 'iams.access_token';
const REFRESH_TOKEN_KEY = 'iams.refresh_token';

export interface StoredTokens {
  accessToken: string;
  refreshToken: string;
}

function safeGet(key: string): string | null {
  try {
    return sessionStorage.getItem(key);
  } catch {
    // Private mode or blocked site data: behave as signed out rather than crash.
    return null;
  }
}

export const tokenStore = {
  get(): StoredTokens | null {
    const accessToken = safeGet(ACCESS_TOKEN_KEY);
    const refreshToken = safeGet(REFRESH_TOKEN_KEY);
    return accessToken && refreshToken ? { accessToken, refreshToken } : null;
  },

  set(tokens: StoredTokens): void {
    try {
      sessionStorage.setItem(ACCESS_TOKEN_KEY, tokens.accessToken);
      sessionStorage.setItem(REFRESH_TOKEN_KEY, tokens.refreshToken);
    } catch {
      /* Storage unavailable; the session lasts only as long as this page. */
    }
  },

  clear(): void {
    try {
      sessionStorage.removeItem(ACCESS_TOKEN_KEY);
      sessionStorage.removeItem(REFRESH_TOKEN_KEY);
    } catch {
      /* nothing to do */
    }
  },
};

/* -------------------------------------------------------------------------- */
/* Session expiry notification                                                */
/* -------------------------------------------------------------------------- */

type SessionExpiredListener = () => void;
const sessionExpiredListeners = new Set<SessionExpiredListener>();

/** Lets the auth provider react when a refresh finally fails. */
export function onSessionExpired(listener: SessionExpiredListener): () => void {
  sessionExpiredListeners.add(listener);
  return () => sessionExpiredListeners.delete(listener);
}

function notifySessionExpired(): void {
  tokenStore.clear();
  for (const listener of sessionExpiredListeners) listener();
}

/* -------------------------------------------------------------------------- */
/* Refresh coalescing                                                         */
/* -------------------------------------------------------------------------- */

interface TokenResponse {
  access_token: string;
  refresh_token: string;
  expires_at: string;
  refresh_expires_at: string;
  must_change_password: boolean;
}

/**
 * In-flight refresh, shared by every request that discovers a 401 at the same
 * moment. Without this, parallel queries would each rotate the refresh token;
 * the backend treats replay of a rotated token as theft and revokes all
 * sessions, so the user would be signed out by their own dashboard loading.
 */
/**
 * Outcome of a refresh attempt. The three cases must be distinguished:
 *
 *  - `refreshed`  - retry the original request with the new token.
 *  - `rejected`   - the server refused; the session is genuinely over.
 *  - `unavailable`- the request never reached the server. The session may well
 *                   still be valid, so the tokens are kept and the user is not
 *                   signed out over a connectivity blip.
 */
type RefreshOutcome =
  | { status: 'refreshed'; tokens: StoredTokens }
  | { status: 'rejected' }
  | { status: 'unavailable' };

let refreshInFlight: Promise<RefreshOutcome> | null = null;

async function refreshAccessToken(): Promise<RefreshOutcome> {
  if (refreshInFlight) return refreshInFlight;

  refreshInFlight = (async () => {
    const current = tokenStore.get();
    if (!current) return { status: 'rejected' as const };

    try {
      const response = await fetch(`${API_BASE}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: current.refreshToken }),
      });

      if (!response.ok) return { status: 'rejected' as const };

      const body = (await response.json()) as TokenResponse;
      const tokens: StoredTokens = {
        accessToken: body.access_token,
        refreshToken: body.refresh_token,
      };
      // Store the rotated refresh token immediately. The previous one is spent;
      // presenting it again would look like token theft to the backend.
      tokenStore.set(tokens);
      return { status: 'refreshed' as const, tokens };
    } catch {
      return { status: 'unavailable' as const };
    } finally {
      refreshInFlight = null;
    }
  })();

  return refreshInFlight;
}

/* -------------------------------------------------------------------------- */
/* Request                                                                    */
/* -------------------------------------------------------------------------- */

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined | null>;
  signal?: AbortSignal;
  /** Skip the bearer header and the refresh dance, e.g. for sign-in itself. */
  anonymous?: boolean;
}

function buildUrl(path: string, query?: RequestOptions['query']): string {
  const url = `${API_BASE}${path}`;
  if (!query) return url;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== '') {
      params.append(key, String(value));
    }
  }
  const serialised = params.toString();
  return serialised ? `${url}?${serialised}` : url;
}

async function toApiError(response: Response): Promise<ApiError> {
  const requestId = response.headers.get('X-Request-ID');
  try {
    const body = (await response.json()) as {
      error?: { code?: string; message?: string; details?: ApiErrorDetail[] };
    };
    return new ApiError({
      status: response.status,
      code: body.error?.code ?? 'http_error',
      message: body.error?.message ?? `Request failed with status ${response.status}.`,
      details: body.error?.details ?? [],
      requestId,
    });
  } catch {
    // A non-JSON body means a proxy or gateway responded, not our API.
    return new ApiError({
      status: response.status,
      code: 'http_error',
      message:
        response.status >= 500
          ? 'The server could not complete the request. Please try again.'
          : `Request failed with status ${response.status}.`,
      requestId,
    });
  }
}

async function send(
  path: string,
  options: RequestOptions,
  token: string | null,
): Promise<Response> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  if (token) headers['Authorization'] = `Bearer ${token}`;

  const init: RequestInit = {
    method: options.method ?? 'GET',
    headers,
  };
  if (options.body !== undefined) init.body = JSON.stringify(options.body);
  if (options.signal) init.signal = options.signal;

  return fetch(buildUrl(path, options.query), init);
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const tokens = options.anonymous ? null : tokenStore.get();

  let response: Response;
  try {
    response = await send(path, options, tokens?.accessToken ?? null);
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError({
      status: 0,
      code: 'network_error',
      message: 'Could not reach the server. Check your connection and try again.',
    });
  }

  // One transparent refresh-and-retry on an expired access token.
  if (response.status === 401 && !options.anonymous && tokens) {
    const outcome = await refreshAccessToken();

    if (outcome.status === 'refreshed') {
      try {
        response = await send(path, options, outcome.tokens.accessToken);
      } catch {
        throw new ApiError({
          status: 0,
          code: 'network_error',
          message: 'Could not reach the server. Check your connection and try again.',
        });
      }
    } else if (outcome.status === 'rejected') {
      // Only a refusal from the server ends the session. An unreachable server
      // leaves the tokens in place so the next attempt can succeed.
      notifySessionExpired();
    }
  }

  if (!response.ok) throw await toApiError(response);

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

export const api = {
  get: <T>(path: string, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    request<T>(path, { ...options, method: 'GET' }),

  post: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    request<T>(path, { ...options, method: 'POST', body }),

  put: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    request<T>(path, { ...options, method: 'PUT', body }),

  patch: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    request<T>(path, { ...options, method: 'PATCH', body }),

  delete: <T>(path: string, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    request<T>(path, { ...options, method: 'DELETE' }),
};
