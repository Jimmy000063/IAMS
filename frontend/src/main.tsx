import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { RouterProvider } from 'react-router-dom';

import { router } from '@/app/router';
import { ApiError } from '@/lib/api';
import { AuthProvider } from '@/lib/auth';
import { applyStoredAppearanceEarly } from '@/lib/appearance';
import { AppearanceProvider } from '@/lib/theme';
import { ToastProvider } from '@/lib/toast';

import '@/index.css';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Retrying an authorization failure cannot succeed and only delays the
      // error the user needs to see.
      retry: (failureCount, error) => {
        if (error instanceof ApiError) {
          if (error.status === 401 || error.status === 403 || error.status === 404)
            return false;
          if (error.status === 422) return false;
        }
        return failureCount < 2;
      },
      staleTime: 30_000,
      refetchOnWindowFocus: false,
    },
    mutations: {
      // Mutations are never retried automatically: a retried punch, leave
      // approval or payroll action could duplicate a real-world effect.
      retry: false,
    },
  },
});

// Applied before the first paint: resolving the theme inside React would show
// the default theme briefly and then correct itself - a white flash for anybody
// who chose dark, and a colour flash for anybody who chose a palette.
applyStoredAppearanceEarly();

const container = document.getElementById('root');
if (!container) throw new Error('Root element #root is missing from index.html');

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <AppearanceProvider>
        <ToastProvider>
          <AuthProvider>
            <RouterProvider router={router} />
          </AuthProvider>
        </ToastProvider>
      </AppearanceProvider>
    </QueryClientProvider>
  </StrictMode>,
);
