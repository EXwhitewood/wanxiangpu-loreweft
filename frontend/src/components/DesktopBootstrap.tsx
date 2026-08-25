/* Hallmark · pre-emit critique: P5 H5 E5 S5 R5 V4 */
/* Hallmark · component: desktop bootstrap · genre: editorial utility · theme: project DESIGN.md */
import { useEffect, useState, type ReactNode } from "react";
import appIcon from "@/assets/loreweft-app-icon.png";
import { setDesktopBackendPort } from "@/api/client";
import { getDesktopStorageSettings, isTauri } from "@/utils/platform";

type BootstrapState = "starting" | "ready" | "failed";

const DEFAULT_BACKEND_PORT = 18000;

async function backendIsReady(port: number, expectedVersion: string | null): Promise<boolean> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 1500);
  try {
    const response = await fetch(`http://127.0.0.1:${port}/api/health`, {
      cache: "no-store",
      signal: controller.signal,
    });
    if (!response.ok) return false;
    const payload = await response.json();
    return payload?.status === "ok" && (!expectedVersion || payload?.version === expectedVersion);
  } catch {
    return false;
  } finally {
    window.clearTimeout(timeout);
  }
}

async function desktopBackendPort(): Promise<number> {
  const invoke = window.__TAURI__?.core?.invoke;
  if (!invoke) return DEFAULT_BACKEND_PORT;
  try {
    const port = await invoke<number>("backend_port");
    return Number.isInteger(port) && port > 0 ? port : DEFAULT_BACKEND_PORT;
  } catch {
    return DEFAULT_BACKEND_PORT;
  }
}

async function backendProcessStatus(): Promise<string | null> {
  const invoke = window.__TAURI__?.core?.invoke;
  if (!invoke) return null;
  try {
    return await invoke<string>("backend_process_status");
  } catch {
    return null;
  }
}

async function desktopAppVersion(): Promise<string | null> {
  const invoke = window.__TAURI__?.core?.invoke;
  if (!invoke) return null;
  try {
    return await invoke<string>("desktop_app_version");
  } catch {
    return null;
  }
}

export default function DesktopBootstrap({ children }: { children: ReactNode }) {
  const desktop = isTauri();
  const [state, setState] = useState<BootstrapState>(desktop ? "starting" : "ready");
  const [detail, setDetail] = useState("");
  const [diagnosticPath, setDiagnosticPath] = useState(
    "AppData\\Roaming\\com.loreweft.desktop\\startup.log"
  );
  const [retryToken, setRetryToken] = useState(0);

  useEffect(() => {
    if (!desktop) return;

    let cancelled = false;
    let timer = 0;
    let attempt = 0;
    setState("starting");
    setDetail("");

    void getDesktopStorageSettings()
      .then((storage) => setDiagnosticPath(`${storage.cacheDir}\\startup.log`))
      .catch(() => {});

    const poll = async () => {
      attempt += 1;
      const expectedVersion = await desktopAppVersion();
      const port = await desktopBackendPort();
      setDesktopBackendPort(port);
      if (await backendIsReady(port, expectedVersion)) {
        if (!cancelled) setState("ready");
        return;
      }

      const processState = await backendProcessStatus();
      if (processState?.startsWith("failed:") || processState?.startsWith("exited:")) {
        if (!cancelled) {
          setDetail(processState.replace(/^(failed|exited):/, ""));
          setState("failed");
        }
        return;
      }

      if (!cancelled) {
        const delay = Math.min(250 + attempt * 125, 1500);
        timer = window.setTimeout(poll, delay);
      }
    };

    void poll();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [desktop, retryToken]);

  if (state === "ready") return <>{children}</>;

  return (
    <div className="fixed inset-0 flex items-center justify-center overflow-hidden bg-[var(--surface-atmosphere)] px-6 text-[var(--color-ink-strong)]">
      <section className="relative w-full max-w-[440px] overflow-hidden rounded-xl border border-[var(--border-emphasis)] bg-[var(--surface-raised)] px-8 py-8 shadow-[var(--shadow-overlay)] sm:px-10 sm:py-9">
        <div className="mb-7 flex items-center gap-4">
          <img
            src={appIcon}
            alt=""
            aria-hidden="true"
            className="size-12 shrink-0"
          />
          <div className="min-w-0">
            <p className="font-serif text-xl tracking-[0.12em] text-[var(--color-ink-strong)]">万象谱</p>
            <p className="mt-1 truncate text-[10px] uppercase tracking-[0.28em] text-[var(--color-ink-muted)]">Loreweft Studio</p>
          </div>
        </div>

        {state === "starting" ? (
          <div aria-live="polite">
            <p className="text-sm font-medium tracking-[0.08em] text-[var(--color-ink)]">正在唤醒本地创作引擎</p>
            <p className="mt-2 text-xs leading-6 text-[var(--color-ink-muted)]">首次打开可能需要一些时间，写作资料仍只保存在这台电脑上。</p>
            <div className="mt-7 h-px overflow-hidden bg-[var(--border-emphasis)]">
              <div className="h-full w-1/3 animate-[bootstrap-travel_1.4s_ease-in-out_infinite] bg-[var(--color-accent)] motion-reduce:animate-none" />
            </div>
          </div>
        ) : (
          <div aria-live="assertive">
            <p className="text-sm font-medium tracking-[0.08em] text-[var(--color-error)]">本地创作引擎未能启动</p>
            <p className="mt-2 break-words text-xs leading-6 text-[var(--color-ink-muted)]">
              {detail || "服务暂时没有响应。你可以重新检测；若仍失败，请关闭软件后再次打开。"}
            </p>
            <details className="mt-3 text-[11px] leading-5 text-[var(--color-ink-muted)]">
              <summary className="w-fit cursor-pointer rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)]">
                查看诊断记录位置
              </summary>
              <p className="mt-1 break-all font-mono">{diagnosticPath}</p>
            </details>
            <button
              type="button"
              onClick={() => setRetryToken((value) => value + 1)}
              className="mt-6 inline-flex min-h-11 items-center justify-center whitespace-nowrap rounded-md border border-[var(--border-emphasis)] bg-[var(--surface-tool)] px-4 text-xs font-medium tracking-[0.12em] text-[var(--color-ink)] transition-colors duration-150 hover:border-[var(--color-accent)] hover:text-[var(--color-accent)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)] active:translate-y-px"
            >
              重新检测
            </button>
          </div>
        )}
      </section>
    </div>
  );
}
