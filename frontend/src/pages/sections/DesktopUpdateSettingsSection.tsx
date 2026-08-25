import { useEffect, useRef, useState } from "react";
import { CheckCircle2, Download, Loader2, RefreshCw, ShieldCheck, XCircle } from "lucide-react";
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

type UpdateState = "idle" | "checking" | "available" | "installing" | "success" | "error";

export default function DesktopUpdateSettingsSection() {
  const desktop = isTauri();
  const [state, setState] = useState<UpdateState>("idle");
  const [update, setUpdate] = useState<Update | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [installStage, setInstallStage] = useState<DesktopUpdateInstallStage>("downloading");
  const [message, setMessage] = useState("");
  const updateRef = useRef<Update | null>(null);
  const mountedRef = useRef(false);
  const checkRequestRef = useRef(0);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      checkRequestRef.current += 1;
      const pending = updateRef.current;
      updateRef.current = null;
      if (pending) void releaseDesktopUpdate(pending);
    };
  }, []);

  if (!desktop) return null;

  const handleCheck = async () => {
    const requestId = checkRequestRef.current + 1;
    checkRequestRef.current = requestId;
    setState("checking");
    setMessage("");
    const previous = updateRef.current;
    updateRef.current = null;
    setUpdate(null);
    if (previous) void releaseDesktopUpdate(previous);
    const result = await checkDesktopUpdate();

    if (!mountedRef.current || requestId !== checkRequestRef.current) {
      if (result.kind === "available") void releaseDesktopUpdate(result.update);
      return;
    }

    if (result.kind === "available") {
      updateRef.current = result.update;
      setUpdate(result.update);
      setState("available");
    } else if (result.kind === "up-to-date") {
      setState("success");
      setMessage("当前已经是最新版本。");
    } else if (result.kind === "disabled") {
      setState("error");
      setMessage("当前安装包尚未配置 GitHub 更新源；仍可手动覆盖安装新版。");
    } else {
      setState("error");
      setMessage(result.message);
    }
  };

  const handleInstall = async () => {
    if (!update) return;
    setState("installing");
    setProgress(0);
    setInstallStage("downloading");
    setMessage("");
    try {
      await installDesktopUpdate(update, setProgress, setInstallStage);
      if (!mountedRef.current) return;
      setState("success");
      setMessage("更新包已安装，正在重启万象谱。");
      await releaseDesktopUpdate(update);
      if (updateRef.current === update) updateRef.current = null;
      setUpdate(null);
      window.setTimeout(() => {
        void restartDesktopApp();
      }, 300);
    } catch (error) {
      await releaseDesktopUpdate(update);
      if (updateRef.current === update) updateRef.current = null;
      if (!mountedRef.current) return;
      setUpdate(null);
      setState("error");
      setMessage(describeDesktopUpdateError(error));
    }
  };

  const busy = state === "checking" || state === "installing";

  return (
    <section
      className="group relative overflow-hidden rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-tool)] p-6 sm:p-8"
      aria-labelledby="desktop-update-title"
    >
      <ShieldCheck
        className="pointer-events-none absolute -right-5 top-1 h-44 w-44 text-[var(--color-ink)] opacity-[0.04] transition-transform duration-300 group-hover:-translate-x-1 group-hover:translate-y-1 motion-reduce:transform-none motion-reduce:transition-none"
        aria-hidden="true"
      />
      <div className="relative z-10">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h2 id="desktop-update-title" className="flex items-center gap-2.5 text-base font-semibold text-[var(--color-ink-strong)]">
              <span className="h-5 w-1 rounded-full bg-[var(--color-accent)]" aria-hidden="true" />
              软件更新
            </h2>
            <p className="mt-2 max-w-2xl text-xs leading-6 text-[var(--color-ink-muted)]">
              通过经过签名的 GitHub 更新包覆盖当前程序，作品数据、API 配置和创作目录不会被删除。
            </p>
          </div>
          <button
            type="button"
            onClick={() => void handleCheck()}
            disabled={busy}
            className="inline-flex min-h-10 shrink-0 items-center justify-center gap-2 rounded-lg border border-[var(--border-emphasis)] bg-[var(--surface-raised)] px-4 text-xs font-semibold text-[var(--color-ink)] transition-colors hover:border-[var(--color-accent)] hover:text-[var(--color-accent)] disabled:cursor-wait disabled:opacity-60"
          >
            {state === "checking" ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            检查更新
          </button>
        </div>

        <div className="mt-6 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-paper)] px-4 py-4">
          {state === "available" && update ? (
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <p className="flex items-center gap-2 text-sm font-semibold text-[var(--color-ink-strong)]">
                  <Download className="h-4 w-4 text-[var(--color-accent)]" />
                  发现新版本 v{update.version}
                </p>
                {update.body && (
                  <p className="mt-2 max-h-20 overflow-auto whitespace-pre-wrap text-xs leading-5 text-[var(--color-ink-muted)]">
                    {update.body}
                  </p>
                )}
              </div>
              <button
                type="button"
                onClick={() => void handleInstall()}
                className="inline-flex min-h-10 shrink-0 items-center justify-center gap-2 rounded-lg bg-[var(--color-accent)] px-4 text-xs font-semibold text-white transition-opacity hover:opacity-90"
              >
                <Download className="h-4 w-4" />
                下载并重启安装
              </button>
            </div>
          ) : state === "installing" ? (
            <div aria-live="polite">
              <div className="flex items-center justify-between text-xs font-medium text-[var(--color-ink)]">
                <span>
                  {installStage === "downloading"
                    ? "正在下载更新"
                    : installStage === "retrying"
                      ? "网络中断，正在自动重试下载"
                    : installStage === "preparing"
                      ? "正在安全关闭本地后台"
                      : "正在启动安装程序"}
                </span>
                <span>{installStage === "downloading" || installStage === "retrying" ? (progress === null ? "处理中" : `${Math.round(progress)}%`) : "处理中"}</span>
              </div>
              <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-[var(--border-subtle)]">
                <div
                  className="h-full rounded-full bg-[var(--color-accent)] transition-[width] duration-200"
                  style={{ width: `${progress === null ? 35 : Math.max(2, progress)}%` }}
                />
              </div>
            </div>
          ) : state === "success" ? (
            <p className="flex items-center gap-2 text-xs font-medium text-[var(--color-accent)]" aria-live="polite">
              <CheckCircle2 className="h-4 w-4" />
              {message}
            </p>
          ) : state === "error" ? (
            <p className="flex items-start gap-2 text-xs leading-5 text-[var(--color-error)]" aria-live="assertive">
              <XCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{message}</span>
            </p>
          ) : (
            <p className="text-xs leading-5 text-[var(--color-ink-muted)]">
              {desktopUpdatesEnabled
                ? "发行版会从 GitHub 检查签名更新；你也可以随时手动检查。"
                : "当前构建尚未配置 GitHub 更新源。配置发行仓库后，这里会启用自动覆盖升级。"}
            </p>
          )}
        </div>
      </div>
    </section>
  );
}
