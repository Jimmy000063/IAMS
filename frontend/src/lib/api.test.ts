/**
 * Tests for the API client's error normalisation and refresh behaviour.
 *
 * The refresh-coalescing test matters: without it, parallel queries each rotate
 * the refresh token, the backend reads the replay as theft, and the user is
 * signed out of every device by their own dashboard loading.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, api, tokenStore } from '@/lib/api';

function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  });
}

describe('tokenStore', () => {
  it('round-trips a token pair', () => {
    tokenStore.set({ accessToken: 'a', refreshToken: 'r' });
    expect(tokenStore.get()).toEqual({ accessToken: 'a', refreshToken: 'r' });
  });

  it('reports no session when cleared', () => {
    tokenStore.set({ accessToken: 'a', refreshToken: 'r' });
    tokenStore.clear();
    expect(tokenStore.get()).toBeNull();
  });

  it('reports no session when only one token is present', () => {
    sessionStorage.setItem('iams.access_token', 'orphan');
    expect(tokenStore.get()).toBeNull();
  });
});

describe('request error handling', () => {
  beforeEach(() => {
    tokenStore.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('turns the error envelope into a typed ApiError', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse(
        422,
        {
          error: {
            code: 'policy_violation',
            message: 'Leave balance is insufficient.',
            details: [{ field: 'from_date', message: 'Outside the allowed window.' }],
          },
        },
        { 'X-Request-ID': 'req-123' },
      ),
    );

    const error = await api.get('/leave').catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    const apiError = error as ApiError;
    expect(apiError.code).toBe('policy_violation');
    expect(apiError.status).toBe(422);
    expect(apiError.requestId).toBe('req-123');
    expect(apiError.fieldErrors).toEqual({ from_date: 'Outside the allowed window.' });
  });

  it('reports a network failure without leaking the underlying exception', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('Failed to fetch'));

    const error = (await api.get('/health').catch((caught: unknown) => caught)) as ApiError;

    expect(error.code).toBe('network_error');
    expect(error.message).toContain('Could not reach the server');
  });

  it('handles a non-JSON gateway response', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('<html>502 Bad Gateway</html>', { status: 502 }),
    );

    const error = (await api.get('/health').catch((caught: unknown) => caught)) as ApiError;

    expect(error.status).toBe(502);
    expect(error.code).toBe('http_error');
  });

  it('returns undefined for a 204 instead of failing to parse', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null, { status: 204 }));
    await expect(api.post('/auth/logout', {})).resolves.toBeUndefined();
  });

  it('attaches the bearer token when a session exists', async () => {
    tokenStore.set({ accessToken: 'access-1', refreshToken: 'refresh-1' });
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(jsonResponse(200, { ok: true }));

    await api.get('/auth/me');

    const init = fetchSpy.mock.calls[0]?.[1] as RequestInit;
    expect((init.headers as Record<string, string>)['Authorization']).toBe('Bearer access-1');
  });

  it('omits the bearer token for anonymous requests', async () => {
    tokenStore.set({ accessToken: 'access-1', refreshToken: 'refresh-1' });
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(jsonResponse(200, { ok: true }));

    await api.post('/auth/login', { identifier: 'x', password: 'y' }, { anonymous: true });

    const init = fetchSpy.mock.calls[0]?.[1] as RequestInit;
    expect((init.headers as Record<string, string>)['Authorization']).toBeUndefined();
  });

  it('serialises query parameters and drops empty ones', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(200, []));

    await api.get('/settings', {
      query: { category: 'leave', page: 2, unused: undefined, blank: '' },
    });

    const url = String(fetchSpy.mock.calls[0]?.[0]);
    expect(url).toContain('category=leave');
    expect(url).toContain('page=2');
    expect(url).not.toContain('unused');
    expect(url).not.toContain('blank');
  });
});

describe('token refresh', () => {
  beforeEach(() => {
    tokenStore.set({ accessToken: 'stale', refreshToken: 'refresh-1' });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    tokenStore.clear();
  });

  it('refreshes once and retries the original request', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse(401, { error: { code: 'not_authenticated' } }))
      .mockResolvedValueOnce(
        jsonResponse(200, {
          access_token: 'fresh',
          refresh_token: 'refresh-2',
          expires_at: '',
          refresh_expires_at: '',
          must_change_password: false,
        }),
      )
      .mockResolvedValueOnce(jsonResponse(200, { username: 'hradmin' }));

    await expect(api.get<{ username: string }>('/auth/me')).resolves.toEqual({
      username: 'hradmin',
    });

    expect(fetchSpy).toHaveBeenCalledTimes(3);
    expect(tokenStore.get()?.accessToken).toBe('fresh');
    // The rotated refresh token must replace the old one, or the next refresh
    // would present a spent token and trip the backend's reuse detection.
    expect(tokenStore.get()?.refreshToken).toBe('refresh-2');
  });

  it('coalesces concurrent refreshes into a single rotation', async () => {
    let refreshCalls = 0;
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      const url = String(input);
      if (url.includes('/auth/refresh')) {
        refreshCalls += 1;
        return Promise.resolve(
          jsonResponse(200, {
            access_token: 'fresh',
            refresh_token: `refresh-${refreshCalls + 1}`,
            expires_at: '',
            refresh_expires_at: '',
            must_change_password: false,
          }),
        );
      }
      // A protected call fails while the stale token is current and succeeds
      // once the (single) refresh has replaced it.
      return Promise.resolve(
        tokenStore.get()?.accessToken === 'fresh'
          ? jsonResponse(200, { ok: true })
          : jsonResponse(401, { error: { code: 'not_authenticated' } }),
      );
    });

    await Promise.all([api.get('/a'), api.get('/b'), api.get('/c'), api.get('/d')]);

    expect(refreshCalls).toBe(1);
  });

  it('clears the session when the refresh itself is rejected', async () => {
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse(401, { error: { code: 'not_authenticated' } }))
      .mockResolvedValueOnce(jsonResponse(401, { error: { code: 'authentication_failed' } }));

    await api.get('/auth/me').catch(() => undefined);

    expect(tokenStore.get()).toBeNull();
  });

  it('keeps the session when the refresh fails for network reasons', async () => {
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse(401, { error: { code: 'not_authenticated' } }))
      .mockRejectedValueOnce(new TypeError('Failed to fetch'));

    await api.get('/auth/me').catch(() => undefined);

    // A connectivity blip is not an expired session; signing the user out here
    // would be wrong and they would lose unsaved work.
    expect(tokenStore.get()).not.toBeNull();
  });
});
