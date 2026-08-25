import { useEffect, useRef, useState } from "react";
import { CheckCircle2, Download, Loader2, X } from "lucide-react";
import type { Update } from "@tauri-apps/plugin-updater";
import { isTauri, restartDesktopApp } from "@/utils/platform";
import {
  checkDesktopUpdate,
  describeDesktopUpdateError,
  type DesktopUpdateInstallStage,
  desktopUpdatesEnabled,
  installDesktopUpdate,
  releaseDesktopUpdate,
} from "@/utils/updater";

type NoticeState = "available" | "installing" | "installed" | "error";

/** Passive startup check: it never blocks the editor and only appears when an update exists. */
export default function DesktopUpdateNotice() {
  const [update, setUpdate] = useState<Update | null>(null);
  const [state, setState] = useState<NoticeState>("available");
  const [progress, setProgress] = useState<number | null>(null);
  const [installStage, setInstallStage] = useState<DesktopUpdateInstallStage>("downloading");
  const [message, setMessage] = useState("");
  const updateRef = useRef<Update | null>(null);
  const mountedRef = useRef(false);

  useEffect(() => {
    if (!isTauri() || !desktopUpdatesEnabled) return;

    mountedRef.current = true;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void checkDesktopUpdate().then((result) => {
        if (result.kind !== "available") return;
        if (cancelled) {
          void releaseDesktopUpdate(result.update);
          return;
        }
        updateRef.current = result.update;
        setUpdate(result.update);
      });
    }, 6000);

    return () => {
      cancelled = true;
      mountedRef.current = false;
      window.clearTimeout(timer);
      const pending = updateRef.current;
      updateRef.current = null;
      if (pending) void releaseDesktopUpdate(pending);
    };
  }, []);

  if (!update) return null;

  const dismiss = () => {
    if (state === "installing") return;
    const pending = updateRef.current;
    updateRef.current = null;
    setUpdate(null);
    if (pending) void releaseDesktopUpdate(pending);
  };

  const install = async () => {
    setState("installing");
    setProgress(0);
    setInstallStage("downloading");
    try {
      await installDesktopUpdate(update, setProgress, setInstallStage);
      await releaseDesktopUpdate(update);
      updateRef.current = null;
      if (mountedRef.current) {
        setState("installed");
        setMessage("更新已安装，正在重启万象谱。");
      }
      window.setTimeout(() => void restartDesktopApp(), 300);
    } catch (error) {
      if (!mountedRef.current) return;
      setState("error");
      setMessage(describeDesktopUpdateError(error));
    }
  };

  return (
    <aside
      className="fixed right-5 top-16 z-[120] w-[min(390px,calc(100vw-2.5rem))] overflow-hidden rounded-xl border border-[var(--border-emphasis)] bg-[var(--surface-paper)]/95 p-4 text-[var(--color-ink)] shadow-[var(--shadow-overlay)] backdrop-blur-xl"
      role="status"
      aria-live="polite"
    >
      {state !== "installing" && (
        <button
          type="button"
          onClick={dismiss}
          className="absolute right-3 top-3 rounded-md p-1 text-[var(--color-ink-muted)] transition-colors hover:bg-[var(--surface-tool)] hover:text-[var(--color-ink)]"
          aria-label="关闭更新提示"
        >
          <X className="h-4 w-4" />
        </button>
      )}

      {state === "available" && (
        <>
          <p className="pr-6 text-sm font-semibold text-[var(--color-ink-strong)]">发现新版本 v{update.version}</p>
          <p className="mt-1 text-xs leading-5 text-[var(--color-ink-muted)]">更新会保留作品数据、API 配置和创作目录。</p>
          {update.body && (
            <p className="mt-3 max-h-16 overflow-auto whitespace-pre-wrap text-xs leading-5 text-[var(--color-ink-muted)]">{update.body}</p>
          )}
          <button
            type="button"
            onClick={() => void install()}
            className="mt-4 inline-flex min-h-9 items-center gap-2 rounded-lg bg-[var(--color-accent)] px-3 text-xs font-semibold text-white transition-opacity hover:opacity-90"
          >
            <Download className="h-3.5 w-3.5" />
            下载并重启
          </button>
        </>
      )}

      {state === "installing" && (
        <div>
          <p className="flex items-center gap-2 text-sm font-semibold text-[var(--color-ink-strong)]">
            <Loader2 className="h-4 w-4 animate-spin text-[var(--color-accent)]" />
            {installStage === "downloading"
              ? `正在下载更新 ${progress === null ? "" : `${Math.round(progress)}%`}`
              : installStage === "retrying"
                ? `网络中断，正在自动重试 ${progress === null ? "" : `${Math.round(progress)}%`}`
              : installStage === "preparing"
                ? "正在安全关闭本地后台"
                : "正在启动安装程序"}
          </p>
          <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-[var(--border-subtle)]">
            <div className="h-full rounded-full bg-[var(--color-accent)] transition-[width] duration-200" style={{ width: `${progress === null ? 35 : Math.max(2, progress)}%` }} />
          </div>
        </div>
      )}

      {state === "installed" && (
        <p className="flex items-center gap-2 pr-5 text-xs font-medium text-[var(--color-accent)]">
          <CheckCircle2 className="h-4 w-4" />
          {message}
        </p>
      )}

      {state === "error" && (
        <div className="pr-5">
          <p className="text-xs leading-5 text-[var(--color-error)]">{message}</p>
          <button
            type="button"
            onClick={() => void install()}
            className="mt-3 inline-flex min-h-9 items-center gap-2 rounded-lg border border-[var(--border-emphasis)] bg-[var(--surface-raised)] px-3 text-xs font-semibold text-[var(--color-ink)] transition-colors hover:border-[var(--color-accent)] hover:text-[var(--color-accent)]"
          >
            <Download className="h-3.5 w-3.5" />
            重新尝试下载
          </button>
        </div>
      )}
    </aside>
  );
}
