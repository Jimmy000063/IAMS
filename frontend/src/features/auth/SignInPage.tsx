/**
 * Sign in.
 *
 * The first screen anybody sees, and the only one shown to a person who is not
 * yet authenticated - so it carries the whole first impression of the system on
 * its own.
 *
 * There is no "create account" link, because there is no public registration.
 * Saying so plainly is better than leaving somebody hunting for a link that was
 * never built.
 *
 * The page follows whatever display mode and colour were last chosen, since
 * those are stored in the browser rather than against an account - so somebody
 * who set dark mode yesterday is not handed a white screen today.
 */

import { zodResolver } from '@hookform/resolvers/zod';
import {
  BadgeCheck,
  CalendarClock,
  Eye,
  EyeOff,
  KeyRound,
  LogIn,
  ShieldCheck,
  UserRound,
  Wallet,
} from 'lucide-react';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { useLocation, useNavigate } from 'react-router-dom';
import { z } from 'zod';

import { Button } from '@/components/ui/Button';
import { TextField } from '@/components/ui/Field';
import { Alert } from '@/components/ui/Feedback';
import { ApiError } from '@/lib/api';
import { useAuth } from '@/lib/auth';

const schema = z.object({
  identifier: z.string().trim().min(1, 'Enter your employee ID, username or work email.'),
  password: z.string().min(1, 'Enter your password.'),
});

type FormValues = z.infer<typeof schema>;

interface LocationState {
  from?: string;
}

const HIGHLIGHTS = [
  { icon: CalendarClock, text: 'Punch in and out, and see your month at a glance' },
  { icon: BadgeCheck, text: 'Apply for leave and follow every approval step' },
  { icon: Wallet, text: 'Download payslips once a payroll run is finalised' },
];

export function SignInPage() {
  const { signIn } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [formError, setFormError] = useState<string | null>(null);
  const [revealed, setRevealed] = useState(false);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { identifier: '', password: '' },
  });

  const onSubmit = async (values: FormValues) => {
    setFormError(null);
    try {
      const result = await signIn(values.identifier, values.password);
      if (result.must_change_password) {
        navigate('/change-password', { replace: true });
        return;
      }
      const state = location.state as LocationState | null;
      navigate(state?.from ?? '/dashboard', { replace: true });
    } catch (error) {
      // Show the server's message verbatim. It is written to be safe to
      // display and does not reveal whether the account exists.
      setFormError(
        error instanceof ApiError
          ? error.message
          : 'Could not sign in. Please check your connection and try again.',
      );
    }
  };

  return (
    <div className="relative grid min-h-dvh lg:grid-cols-[1.05fr_1fr]">
      {/* Brand panel, hidden on small screens where it would just push the form
          down. Built from the brand ramp rather than fixed colours, so it turns
          with the palette like everything else. */}
      <div className="relative hidden flex-col justify-between overflow-hidden p-10 text-white lg:flex">
        <div
          aria-hidden="true"
          className="from-brand-800 via-brand-600 to-brand-900 absolute inset-0 bg-linear-to-br"
        />
        {/* Two soft lights and a hairline grid. The grid is what stops a large
            flat gradient reading as an empty area of the page. */}
        <div
          aria-hidden="true"
          className="absolute inset-0"
          style={{
            background:
              'radial-gradient(38rem 30rem at 18% 14%, color-mix(in oklab, var(--color-cool-300) 38%, transparent), transparent 62%),' +
              'radial-gradient(32rem 26rem at 84% 88%, color-mix(in oklab, var(--color-accent-500) 30%, transparent), transparent 58%)',
          }}
        />
        <div
          aria-hidden="true"
          className="absolute inset-0 opacity-[0.07]"
          style={{
            backgroundImage:
              'linear-gradient(to right, #fff 1px, transparent 1px), linear-gradient(to bottom, #fff 1px, transparent 1px)',
            backgroundSize: '3.5rem 3.5rem',
            maskImage: 'radial-gradient(70% 60% at 50% 40%, black, transparent)',
          }}
        />

        <div className="relative flex items-center gap-3">
          <span
            aria-hidden="true"
            className="grid size-10 place-items-center rounded-xl bg-white/15 text-lg font-bold ring-1 ring-white/25 backdrop-blur-sm ring-inset"
          >
            I
          </span>
          <div>
            <p className="text-base leading-tight font-semibold tracking-tight">Ishvara AMS</p>
            <p className="text-xs text-white/60">Attendance Management System</p>
          </div>
        </div>

        <div className="relative max-w-md">
          <h2 className="text-[2rem] leading-[1.15] font-semibold tracking-tight">
            Attendance, leave and payroll in one place.
          </h2>
          <ul className="mt-7 space-y-3.5">
            {HIGHLIGHTS.map((item) => {
              const Icon = item.icon;
              return (
                <li key={item.text} className="flex items-start gap-3">
                  <span
                    aria-hidden="true"
                    className="mt-px grid size-7 shrink-0 place-items-center rounded-lg bg-white/15 ring-1 ring-white/20 ring-inset"
                  >
                    <Icon className="size-3.5" />
                  </span>
                  <span className="text-sm text-white/85">{item.text}</span>
                </li>
              );
            })}
          </ul>
        </div>

        <div className="relative flex items-center gap-2 text-xs text-white/60">
          <ShieldCheck className="size-3.5 shrink-0" aria-hidden="true" />
          Ishvara Tech Consulting LLP. Accounts are created by HR.
        </div>
      </div>

      <div className="flex items-center justify-center p-6 sm:p-10">
        <div className="animate-in w-full max-w-sm">
          <div className="mb-8 flex items-center gap-2.5 lg:hidden">
            <span
              aria-hidden="true"
              className="from-brand-500 to-brand-700 grid size-9 place-items-center rounded-lg bg-linear-to-b font-bold text-white"
            >
              I
            </span>
            <span className="text-ink text-lg font-semibold tracking-tight">Ishvara AMS</span>
          </div>

          <h1 className="text-ink text-[1.75rem] font-semibold tracking-tight">Welcome back</h1>
          <p className="text-ink-soft mt-1.5 text-sm">Use the credentials HR issued to you.</p>

          <form onSubmit={handleSubmit(onSubmit)} className="mt-7 space-y-5" noValidate>
            {formError && (
              <Alert variant="danger" title="Sign-in failed">
                {formError}
              </Alert>
            )}

            <TextField
              label="Employee ID, username or email"
              autoComplete="username"
              autoFocus
              required
              leadingIcon={<UserRound className="size-4" />}
              error={errors.identifier?.message}
              {...register('identifier')}
            />

            <TextField
              label="Password"
              type={revealed ? 'text' : 'password'}
              autoComplete="current-password"
              required
              leadingIcon={<KeyRound className="size-4" />}
              error={errors.password?.message}
              trailingButton={
                <button
                  type="button"
                  onClick={() => setRevealed((value) => !value)}
                  aria-label={revealed ? 'Hide password' : 'Show password'}
                  className="text-ink-muted hover:text-ink hover:bg-sunken grid size-7 place-items-center rounded-md transition-colors"
                >
                  {revealed ? (
                    <EyeOff className="size-4" aria-hidden="true" />
                  ) : (
                    <Eye className="size-4" aria-hidden="true" />
                  )}
                </button>
              }
              {...register('password')}
            />

            <Button
              type="submit"
              fullWidth
              size="lg"
              isLoading={isSubmitting}
              leadingIcon={<LogIn className="size-4" aria-hidden="true" />}
            >
              Sign in
            </Button>
          </form>

          <p className="text-ink-muted border-line mt-7 border-t pt-5 text-xs">
            Forgotten your password? Contact HR to have a temporary password issued. There is no
            public self-registration for this system.
          </p>
        </div>
      </div>
    </div>
  );
}
