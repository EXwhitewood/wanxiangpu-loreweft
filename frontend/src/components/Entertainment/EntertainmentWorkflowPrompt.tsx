import { useEffect } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Gamepad2, Sparkles, X } from "lucide-react";
import { useEntertainmentStore } from "@/stores/entertainmentStore";

interface EntertainmentWorkflowPromptProps {
  visible: boolean;
  onDismiss: () => void;
}

const PROMPT_DURATION_MS = 9000;

/**
 * A short-lived invitation shown only when an editor workflow starts.
 * It replaces the old persistent bottom-right entertainment trigger.
 */
export default function EntertainmentWorkflowPrompt({
  visible,
  onDismiss,
}: EntertainmentWorkflowPromptProps) {
  const {
    settings,
    isModalOpen,
    openModal,
    fetchSettings,
  } = useEntertainmentStore();

  useEffect(() => {
    if (!settings) void fetchSettings();
  }, [fetchSettings, settings]);

  const shouldShow = Boolean(
    visible &&
    settings?.enabled &&
    settings.show_trigger_button &&
    !isModalOpen,
  );

  useEffect(() => {
    if (!shouldShow) return;
    const timer = window.setTimeout(onDismiss, PROMPT_DURATION_MS);
    return () => window.clearTimeout(timer);
  }, [onDismiss, shouldShow]);

  const handleOpen = () => {
    onDismiss();
    window.setTimeout(() => openModal("workflow_wait"), 0);
  };

  return (
    <AnimatePresence>
      {shouldShow && (
        <motion.aside
          data-testid="entertainment-workflow-prompt"
          role="status"
          aria-live="polite"
          initial={{ opacity: 0, x: "-50%", y: -14, scale: 0.97 }}
          animate={{ opacity: 1, x: "-50%", y: 0, scale: 1 }}
          exit={{ opacity: 0, x: "-50%", y: -10, scale: 0.985 }}
          transition={{ type: "spring", stiffness: 360, damping: 30 }}
          className="pointer-events-none absolute left-1/2 top-2 z-40 w-[min(460px,calc(100%-1.5rem))]"
        >
          <div className="entertainment-prompt-surface pointer-events-auto relative overflow-hidden rounded-[18px] border border-white/80 bg-[#f7fbf8]/90 shadow-[0_18px_50px_rgba(31,72,61,0.16),0_2px_8px_rgba(31,72,61,0.08)] backdrop-blur-xl">
            <div className="entertainment-prompt-decor pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_12%_0%,rgba(130,180,159,0.20),transparent_40%),linear-gradient(120deg,rgba(255,255,255,0.28),transparent_58%)]" />

            <div className="relative flex min-h-[54px] items-center gap-2.5 px-3 py-2">
              <div className="entertainment-prompt-icon relative flex h-8 w-8 shrink-0 items-center justify-center rounded-[11px] border border-magic-500/15 bg-magic-600 text-white shadow-[0_7px_18px_rgba(39,101,84,0.24)]">
                <Sparkles className="h-4 w-4" />
                <motion.span
                  className="absolute inset-0 rounded-xl border border-white/50"
                  animate={{ opacity: [0.2, 0.7, 0.2], scale: [1, 1.08, 1] }}
                  transition={{ duration: 2.6, repeat: Infinity, ease: "easeInOut" }}
                />
              </div>

              <div className="min-w-0 flex-1">
                <div className="flex items-center">
                  <p className="whitespace-nowrap font-serif text-sm font-semibold tracking-wide text-pine-900">
                    灵感正在酝酿
                  </p>
                </div>
                <p className="mt-0.5 truncate text-[11px] text-pine-700/75">
                  工作流已启动，章节生成期间不妨让思绪暂歇片刻。
                </p>
              </div>

              <button
                type="button"
                onClick={handleOpen}
                className="entertainment-prompt-action group flex shrink-0 items-center gap-1.5 rounded-xl border border-magic-600/15 bg-white/70 px-2.5 py-1.5 text-[11px] font-medium text-magic-700 shadow-sm transition-all hover:-translate-y-0.5 hover:border-magic-600/25 hover:bg-white hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-magic-500/35"
              >
                <Gamepad2 className="h-3.5 w-3.5 transition-transform group-hover:-rotate-6" />
                <span className="whitespace-nowrap">稍作休息</span>
              </button>

              <button
                type="button"
                onClick={onDismiss}
                aria-label="关闭等待娱乐提示"
                className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-pine-700/45 transition-colors hover:bg-pine-900/5 hover:text-pine-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-magic-500/35"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>

            <motion.div
              className="absolute inset-x-0 bottom-0 h-px origin-left bg-gradient-to-r from-transparent via-magic-500/65 to-transparent"
              initial={{ scaleX: 1 }}
              animate={{ scaleX: 0 }}
              transition={{ duration: PROMPT_DURATION_MS / 1000, ease: "linear" }}
            />
          </div>
        </motion.aside>
      )}
    </AnimatePresence>
  );
}
