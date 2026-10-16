/**
 * Modal dialog and confirmation prompt, built on the native `<dialog>` element
 * so focus trapping, Escape handling and the backdrop come from the platform
 * rather than from hand-written key handlers.
 */

import { X } from 'lucide-react';
import { useEffect, useRef, type ReactNode } from 'react';

import { Button, type ButtonVariant } from '@/components/ui/Button';
import { cn } from '@/lib/utils';

export interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string | undefined;
  children?: ReactNode | undefined;
  footer?: ReactNode | undefined;
  className?: string | undefined;
}

export function Modal({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  className,
}: ModalProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      dialog.showModal();
    } else if (!open && dialog.open) {
      dialog.close();
    }
  }, [open]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    // Fires for Escape as well as a programmatic close, so parent state cannot
    // drift out of step with the dialog's own state.
    const handleCancel = (event: Event) => {
      event.preventDefault();
      onClose();
    };
    dialog.addEventListener('cancel', handleCancel);
    return () => dialog.removeEventListener('cancel', handleCancel);
  }, [onClose]);

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby="modal-title"
      className={cn(
        'border-line bg-raised m-auto w-[calc(100vw-2rem)] max-w-lg rounded-lg border p-0',
        'text-ink shadow-overlay backdrop:bg-black/40',
        className,
      )}
    >
      <div className="border-line flex items-start justify-between gap-4 border-b px-5 py-4">
        <div className="min-w-0">
          <h2 id="modal-title" className="text-base font-semibold">
            {title}
          </h2>
          {description && <p className="text-ink-soft mt-0.5 text-sm">{description}</p>}
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close dialog"
          className="text-ink-muted hover:bg-sunken hover:text-ink rounded p-1 transition-colors"
        >
          <X className="size-4" aria-hidden="true" />
        </button>
      </div>

      {children && <div className="px-5 py-4">{children}</div>}

      {footer && (
        <div className="border-line flex flex-wrap justify-end gap-2 border-t px-5 py-4">
          {footer}
        </div>
      )}
    </dialog>
  );
}

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  confirmVariant?: ButtonVariant;
  isSubmitting?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/** Used before any destructive or hard-to-reverse action. */
export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  confirmVariant = 'primary',
  isSubmitting = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  return (
    <Modal
      open={open}
      onClose={onCancel}
      title={title}
      footer={
        <>
          <Button variant="secondary" onClick={onCancel} disabled={isSubmitting}>
            {cancelLabel}
          </Button>
          <Button variant={confirmVariant} onClick={onConfirm} isLoading={isSubmitting}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <p className="text-ink-soft text-sm">{message}</p>
    </Modal>
  );
}
