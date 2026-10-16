/**
 * Toast notifications.
 *
 * Rendered into an `aria-live` region so a screen reader announces the outcome
 * of an action that produced no visible navigation.
 */

import { AlertCircle, CheckCircle2, Info, TriangleAlert, X } from 'lucide-react';
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import { ApiError } from '@/lib/api';
import { cn } from '@/lib/utils';

export type ToastVariant = 'success' | 'error' | 'warning' | 'info';

export interface Toast {
  id: number;
  variant: ToastVariant;
  title: string;
  description?: string;
  requestId?: string | null;
}

interface ToastContextValue {
  toasts: Toast[];
  show: (toast: Omit<Toast, 'id'>) => void;
  success: (title: string, description?: string) => void;
  error: (title: string, description?: string) => void;
  /** Surface an ApiError with its message and request id already extracted. */
  apiError: (error: unknown, fallbackTitle?: string) => void;
  dismiss: (id: number) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

const AUTO_DISMISS_MS = 6000;
let nextId = 1;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const show = useCallback(
    (toast: Omit<Toast, 'id'>) => {
      const id = nextId++;
      setToasts((current) => [...current, { ...toast, id }]);
      // Errors stay until dismissed: they often carry a request id the user may
      // need to quote, and auto-hiding them loses that.
      if (toast.variant !== 'error') {
        window.setTimeout(() => dismiss(id), AUTO_DISMISS_MS);
      }
    },
    [dismiss],
  );

  const value = useMemo<ToastContextValue>(
    () => ({
      toasts,
      show,
      dismiss,
      success: (title, description) =>
        show(
          description === undefined
            ? { variant: 'success', title }
            : { variant: 'success', title, description },
        ),
      error: (title, description) =>
        show(
          description === undefined
            ? { variant: 'error', title }
            : { variant: 'error', title, description },
        ),
      apiError: (error, fallbackTitle = 'Something went wrong') => {
        if (error instanceof ApiError) {
          show({
            variant: 'error',
            title: error.message,
            requestId: error.requestId,
            ...(error.details.length > 0 && error.details[0]
              ? { description: error.details.map((detail) => detail.message).join(' ') }
              : {}),
          });
        } else {
          show({ variant: 'error', title: fallbackTitle });
        }
      },
    }),
    [toasts, show, dismiss],
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      <ToastViewport toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  );
}

const VARIANT_STYLES: Record<ToastVariant, { wrapper: string; icon: typeof Info }> = {
  success: {
    wrapper: 'border-success-600/30 bg-success-50 text-success-900',
    icon: CheckCircle2,
  },
  error: { wrapper: 'border-danger-600/30 bg-danger-50 text-danger-900', icon: AlertCircle },
  warning: {
    wrapper: 'border-warning-600/30 bg-warning-50 text-warning-900',
    icon: TriangleAlert,
  },
  info: { wrapper: 'border-brand-600/30 bg-brand-50 text-brand-900', icon: Info },
};

function ToastViewport({
  toasts,
  onDismiss,
}: {
  toasts: Toast[];
  onDismiss: (id: number) => void;
}) {
  return (
    <div
      className="pointer-events-none fixed inset-x-4 bottom-4 z-50 flex flex-col gap-2 sm:inset-x-auto sm:right-4 sm:w-96"
      role="region"
      aria-label="Notifications"
    >
      {toasts.map((toast) => {
        const { wrapper, icon: Icon } = VARIANT_STYLES[toast.variant];
        return (
          <div
            key={toast.id}
            role={toast.variant === 'error' ? 'alert' : 'status'}
            aria-live={toast.variant === 'error' ? 'assertive' : 'polite'}
            className={cn(
              'shadow-overlay pointer-events-auto flex items-start gap-3 rounded-lg border p-3',
              wrapper,
            )}
          >
            <Icon className="mt-0.5 size-5 shrink-0" aria-hidden="true" />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium">{toast.title}</p>
              {toast.description && (
                <p className="mt-1 text-sm opacity-90">{toast.description}</p>
              )}
              {toast.requestId && (
                <p className="mt-1 font-mono text-xs opacity-70">
                  Reference: {toast.requestId}
                </p>
              )}
            </div>
            <button
              type="button"
              onClick={() => onDismiss(toast.id)}
              className="rounded p-1 transition-opacity hover:opacity-70"
              aria-label="Dismiss notification"
            >
              <X className="size-4" aria-hidden="true" />
            </button>
          </div>
        );
      })}
    </div>
  );
}

export function useToast(): ToastContextValue {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error('useToast must be used inside a <ToastProvider>.');
  }
  return context;
}
