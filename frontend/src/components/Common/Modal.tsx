import { useEffect, useId, useRef, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { motion, AnimatePresence, useReducedMotion } from "framer-motion";

interface ModalProps {
  title: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
  onClose: () => void;
  isOpen?: boolean;
  initialFocusRef?: RefObject<HTMLElement>;
  closeDisabled?: boolean;
  /** 模态框最大宽度 Tailwind 类名，默认 max-w-lg */
  maxWidth?: string;
  /** 主体区域最大高度 Tailwind 类名，默认 max-h-[70vh] */
  bodyMaxHeight?: string;
}

export default function Modal({
  title,
  description,
  children,
  footer,
  onClose,
  isOpen = true,
  initialFocusRef,
  closeDisabled = false,
  maxWidth = "max-w-lg",
  bodyMaxHeight = "max-h-[70vh]",
}: ModalProps) {
  const titleId = useId();
  const descriptionId = useId();
  const surfaceRef = useRef<HTMLDivElement>(null);
  const reduceMotion = useReducedMotion();

  useEffect(() => {
    if (!isOpen) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const focusableSelector = [
      "button:not([disabled])",
      "[href]",
      "input:not([disabled])",
      "select:not([disabled])",
      "textarea:not([disabled])",
      "[tabindex]:not([tabindex='-1'])",
    ].join(",");

    const focusFirstControl = () => {
      const requested = initialFocusRef?.current;
      const fallback = surfaceRef.current?.querySelector<HTMLElement>(focusableSelector);
      (requested || fallback || surfaceRef.current)?.focus();
    };
    const frame = window.requestAnimationFrame(focusFirstControl);

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (!closeDisabled) onClose();
        return;
      }
      if (event.key !== "Tab" || !surfaceRef.current) return;

      const focusable = Array.from(
        surfaceRef.current.querySelectorAll<HTMLElement>(focusableSelector),
      );
      if (focusable.length === 0) {
        event.preventDefault();
        surfaceRef.current.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("keydown", handleKeyDown);
      previouslyFocused?.focus();
    };
  }, [closeDisabled, initialFocusRef, isOpen, onClose]);

  if (typeof document === "undefined") return null;

  return createPortal(
    <AnimatePresence>
      {isOpen && (
        <div className="fixed inset-0 z-[110] flex items-center justify-center p-4">
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: reduceMotion ? 0.1 : 0.18 }}
            className="app-modal-backdrop absolute inset-0 bg-[var(--overlay-backdrop)] backdrop-blur-[2px]"
            onClick={() => {
              if (!closeDisabled) onClose();
            }}
          />
          <motion.div
            ref={surfaceRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
            aria-describedby={description ? descriptionId : undefined}
            tabIndex={-1}
            initial={{ opacity: 0, y: reduceMotion ? 0 : 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: reduceMotion ? 0 : 8 }}
            transition={{ duration: reduceMotion ? 0.1 : 0.18, ease: [0.16, 1, 0.3, 1] }}
            className={`app-modal-surface relative z-10 w-full ${maxWidth} overflow-hidden rounded-[var(--radius-xl)] border border-[var(--border-subtle)] bg-[var(--surface-raised)] shadow-[var(--shadow-overlay)]`}
          >
            <div className="app-modal-header flex items-start justify-between gap-6 border-b border-[var(--border-subtle)] px-5 py-4 sm:px-6">
              <div className="min-w-0">
                <h2 id={titleId} className="app-modal-title text-xl font-semibold text-[var(--color-ink-strong)]">
                  {title}
                </h2>
                {description && (
                  <p id={descriptionId} className="mt-1.5 max-w-2xl text-sm leading-6 text-[var(--color-ink-muted)]">
                    {description}
                  </p>
                )}
              </div>
              <button
                type="button"
                onClick={onClose}
                disabled={closeDisabled}
                aria-label="关闭弹窗"
                className="app-modal-close -mr-2 flex h-11 w-11 shrink-0 items-center justify-center rounded-[var(--radius-md)] text-[var(--color-ink-muted)] transition-colors duration-150 hover:bg-[var(--color-accent-soft)] hover:text-[var(--color-accent)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[var(--focus-ring)] disabled:cursor-not-allowed disabled:opacity-50"
              >
                <X className="h-5 w-5" />
              </button>
            </div>
            <div className={`app-modal-body overflow-y-auto px-5 py-5 text-[var(--color-ink)] sm:px-6 ${bodyMaxHeight}`}>
              {children}
            </div>
            {footer && (
              <div className="app-modal-footer flex items-center justify-end gap-3 border-t border-[var(--border-subtle)] bg-[var(--surface-tool)] px-5 py-4 sm:px-6">
                {footer}
              </div>
            )}
          </motion.div>
        </div>
      )}
    </AnimatePresence>,
    document.body,
  );
}
