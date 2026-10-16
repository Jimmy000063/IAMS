import { zodResolver } from '@hookform/resolvers/zod';
import { KeyRound } from 'lucide-react';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { Navigate, useNavigate } from 'react-router-dom';
import { z } from 'zod';

import { Button } from '@/components/ui/Button';
import { TextField } from '@/components/ui/Field';
import { Alert } from '@/components/ui/Feedback';
import { Card, CardBody, CardHeader } from '@/components/ui/Surface';
import { api, ApiError } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { useToast } from '@/lib/toast';

/**
 * Mirrors the server-side policy in `app/core/security.py`.
 *
 * Validating here is purely so the user gets immediate feedback. The server
 * enforces the same rules independently and its verdict is authoritative; if
 * the two drift, the server's error is displayed.
 */
const passwordRules = z
  .string()
  .min(12, 'Use at least 12 characters.')
  .max(128, 'Use at most 128 characters.')
  .regex(/[a-z]/, 'Include a lowercase letter.')
  .regex(/[A-Z]/, 'Include an uppercase letter.')
  .regex(/\d/, 'Include a digit.')
  .regex(/[^A-Za-z0-9]/, 'Include a symbol.');

const schema = z
  .object({
    current_password: z.string().min(1, 'Enter your current password.'),
    new_password: passwordRules,
    confirm_password: z.string().min(1, 'Re-enter the new password.'),
  })
  .refine((values) => values.new_password === values.confirm_password, {
    path: ['confirm_password'],
    message: 'The two passwords do not match.',
  })
  .refine((values) => values.new_password !== values.current_password, {
    path: ['new_password'],
    message: 'Choose a password different from your current one.',
  });

type FormValues = z.infer<typeof schema>;

export function ChangePasswordPage() {
  const { isAuthenticated, mustChangePassword, signOut } = useAuth();
  const navigate = useNavigate();
  const toast = useToast();
  const [formError, setFormError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { current_password: '', new_password: '', confirm_password: '' },
  });

  if (!isAuthenticated) return <Navigate to="/sign-in" replace />;

  const onSubmit = async (values: FormValues) => {
    setFormError(null);
    try {
      await api.post('/auth/me/change-password', {
        current_password: values.current_password,
        new_password: values.new_password,
      });

      // The server revokes every session on a password change, so the tokens
      // we hold are already dead. Clear them and send the user to sign in
      // again rather than leaving the app in a broken half-authenticated state.
      await signOut();
      toast.success('Password changed', 'Please sign in with your new password.');
      navigate('/sign-in', { replace: true });
    } catch (error) {
      if (error instanceof ApiError) {
        const fields = error.fieldErrors;
        if (fields['current_password']) {
          setError('current_password', { message: fields['current_password'] });
        }
        if (fields['password'] || fields['new_password']) {
          setError('new_password', {
            message: fields['new_password'] ?? fields['password'] ?? error.message,
          });
        }
        if (!Object.keys(fields).length) setFormError(error.message);
      } else {
        setFormError('Could not change your password. Please try again.');
      }
    }
  };

  return (
    <div className="bg-page flex min-h-dvh items-center justify-center p-6">
      <div className="w-full max-w-md">
        <Card>
          <CardHeader
            title={mustChangePassword ? 'Set a new password' : 'Change your password'}
            description={
              mustChangePassword
                ? 'Your account uses a temporary password issued by HR. Choose your own password to continue.'
                : 'Choose a new password for your account.'
            }
          />
          <CardBody>
            <form onSubmit={handleSubmit(onSubmit)} className="space-y-5" noValidate>
              {formError && (
                <Alert variant="danger" title="Could not change password">
                  {formError}
                </Alert>
              )}

              <TextField
                label={mustChangePassword ? 'Temporary password' : 'Current password'}
                type="password"
                autoComplete="current-password"
                autoFocus
                required
                error={errors.current_password?.message}
                {...register('current_password')}
              />

              <TextField
                label="New password"
                type="password"
                autoComplete="new-password"
                required
                hint="At least 12 characters, with upper and lower case, a digit and a symbol."
                error={errors.new_password?.message}
                {...register('new_password')}
              />

              <TextField
                label="Confirm new password"
                type="password"
                autoComplete="new-password"
                required
                error={errors.confirm_password?.message}
                {...register('confirm_password')}
              />

              <Alert variant="info" title="You will be signed out">
                Changing your password ends all of your active sessions, including this one.
                Sign in again with the new password.
              </Alert>

              <Button
                type="submit"
                fullWidth
                size="lg"
                isLoading={isSubmitting}
                leadingIcon={<KeyRound className="size-4" aria-hidden="true" />}
              >
                Change password
              </Button>
            </form>
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
