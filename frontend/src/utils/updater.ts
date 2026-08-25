import { check, type Update } from "@tauri-apps/plugin-updater";
import {
  getDesktopSystemProxy,
  isTauri,
  prepareDesktopUpdate,
  recordDesktopUpdateEvent,
  restartDesktopApp,
} from "@/utils/platform";

/**
 * The updater is compiled into every desktop build, but it is only enabled
 * when the release build supplies a GitHub update endpoint. This keeps local
 * development and preview builds completely offline.
 */
export const desktopUpdatesEnabled =
  isTauri() && import.meta.env.VITE_DESKTOP_UPDATES_ENABLED === "1";

export type DesktopUpdateCheckResult =
  | { kind: "disabled" }
  | { kind: "up-to-date" }
  | { kind: "available"; update: Update }
  | { kind: "error"; message: string };

export type DesktopUpdateInstallStage =
  | "downloading"
  | "retrying"
  | "preparing"
  | "installing";

// The updater plugin applies this value to the complete HTTP response body,
// not just connection establishment or periods without progress. A 75 MB
// installer needs more than ten minutes on many proxied GitHub connections.
const DOWNLOAD_TIMEOUT_MS = 30 * 60_000;
const DOWNLOAD_ATTEMPTS = 3;
const DOWNLOAD_RETRY_DELAYS_MS = [1_000, 3_000] as const;
const CHECK_TIMEOUT_MS = 12_000;
const CHECK_ATTEMPTS = 3;
const CHECK_RETRY_DELAYS_MS = [500, 1_500] as const;

let activeInstall: Promise<void> | null = null;
let activeInstallUpdate: Update | null = null;
const updateCloseTasks = new WeakMap<Update, Promise<void>>();

function errorDetail(error: unknown): string {
  return error instanceof Error
    ? error.message.trim()
    : typeof error === "string"
      ? error.trim()
      : "";
}

function sanitizedLogDetail(error: unknown): string {
  return errorDetail(error)
    .replace(/https?:\/\/\S+/gi, "<url>")
    .replace(/[\r\n\t]+/g, " ")
    .slice(0, 400);
}

function isRetryableTransportError(error: unknown): boolean {
  const detail = errorDetail(error).toLowerCase();
  if (!detail) return false;
  if (/signature|public key|invalid key|校验|签名/.test(detail)) return false;
  if (/\b(?:400|401|403|404|405|410|422)\b|not found/.test(detail)) return false;
  return /sending request|request.*(?:fail|error)|failed to fetch|network|timed? out|timeout|dns|tcp|connect|connection|tls|ssl|eof|body|stream|http2|http\/2|\b5\d\d\b/.test(detail);
}

async function recordUpdateEvent(stage: string, detail = ""): Promise<void> {
  await recordDesktopUpdateEvent(stage, detail).catch(() => {});
}

function sleep(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

export function describeDesktopUpdateError(error: unknown): string {
  const detail = errorDetail(error);
  const normalized = detail.toLowerCase();

  if (/signature|public key|invalid key|校验|签名/.test(normalized)) {
    return "更新包签名校验失败。为保护作品数据，本次更新已停止，请改用官方发布页下载安装。";
  }
  if (/404|not found/.test(normalized)) {
    return "更新清单暂未发布或地址无效，请稍后重试。";
  }
  if (/安全关闭本地后台|backend.*(?:stop|exit)|port.*(?:release|occupied)/.test(normalized)) {
    return "更新前无法安全关闭本地后台，当前版本尚未被覆盖。万象谱将自动重启；请稍后再次检查更新。";
  }
  if (/sending request|failed to fetch|network|timed? out|timeout|dns|tcp|connect|connection/.test(normalized)) {
    return "无法连接 GitHub 更新源。请确认系统代理或网络连接可用，然后重试；也可以从官方发布页手动下载安装。";
  }
  return "检查或安装更新失败，请稍后重试；若仍失败，请从官方发布页手动下载安装。";
}
/** Check the configured, signed update manifest. */
export async function checkDesktopUpdate(): Promise<DesktopUpdateCheckResult> {
  if (!desktopUpdatesEnabled) return { kind: "disabled" };

  const proxy = await getDesktopSystemProxy().catch(() => null);
  for (let attempt = 1; attempt <= CHECK_ATTEMPTS; attempt += 1) {
    await recordUpdateEvent("check_started", `attempt=${attempt}/${CHECK_ATTEMPTS}`);
    try {
      const update = await check({
        timeout: CHECK_TIMEOUT_MS,
        ...(proxy ? { proxy } : {}),
      });
      await recordUpdateEvent(
        update ? "check_available" : "check_up_to_date",
        update ? `version=${update.version} attempt=${attempt}/${CHECK_ATTEMPTS}` : `attempt=${attempt}/${CHECK_ATTEMPTS}`,
      );
      return update ? { kind: "available", update } : { kind: "up-to-date" };
    } catch (error) {
      const retryable = isRetryableTransportError(error);
      await recordUpdateEvent(
        retryable ? "check_retryable_error" : "check_terminal_error",
        `attempt=${attempt}/${CHECK_ATTEMPTS} detail=${sanitizedLogDetail(error)}`,
      );
      if (!retryable || attempt >= CHECK_ATTEMPTS) {
        return { kind: "error", message: describeDesktopUpdateError(error) };
      }
      await sleep(CHECK_RETRY_DELAYS_MS[attempt - 1] ?? 1_500);
    }
  }

  return { kind: "error", message: describeDesktopUpdateError("") };
}

async function runDesktopUpdateInstall(
  update: Update,
  onProgress?: (progress: number | null) => void,
  onStage?: (stage: DesktopUpdateInstallStage) => void,
): Promise<void> {
  let preparationStarted = false;

  for (let attempt = 1; attempt <= DOWNLOAD_ATTEMPTS; attempt += 1) {
    let total = 0;
    let received = 0;
    onStage?.(attempt === 1 ? "downloading" : "retrying");
    if (attempt > 1) onProgress?.(0);
    await recordUpdateEvent("download_started", `version=${update.version} attempt=${attempt}/${DOWNLOAD_ATTEMPTS}`);

    try {
      await update.download((event) => {
        if (event.event === "Started") {
          total = event.data.contentLength || 0;
          received = 0;
          onProgress?.(0);
        } else if (event.event === "Progress") {
          received += event.data.chunkLength;
          onProgress?.(total > 0 ? Math.min(100, (received / total) * 100) : null);
        } else if (event.event === "Finished") {
          onProgress?.(100);
        }
      }, { timeout: DOWNLOAD_TIMEOUT_MS });
      await recordUpdateEvent("download_verified", `version=${update.version} attempt=${attempt}/${DOWNLOAD_ATTEMPTS}`);
      break;
    } catch (error) {
      const retryable = isRetryableTransportError(error);
      await recordUpdateEvent(
        retryable ? "download_retryable_error" : "download_terminal_error",
        `version=${update.version} attempt=${attempt}/${DOWNLOAD_ATTEMPTS} detail=${sanitizedLogDetail(error)}`,
      );
      if (!retryable || attempt >= DOWNLOAD_ATTEMPTS) throw error;
      await sleep(DOWNLOAD_RETRY_DELAYS_MS[attempt - 1] ?? 3_000);
    }
  }

  try {
    preparationStarted = true;
    onStage?.("preparing");
    await recordUpdateEvent("backend_preparing", `version=${update.version}`);
    await prepareDesktopUpdate();
    onStage?.("installing");
    await recordUpdateEvent("installer_launching", `version=${update.version}`);
    await update.install();
  } catch (error) {
    // Once preparation starts, the embedded backend may already be stopped.
    // Restarting restores a usable current version when installation cannot
    // be launched; a successful Windows install exits the process before this
    // branch can run.
    await recordUpdateEvent(
      "installation_error",
      `version=${update.version} detail=${sanitizedLogDetail(error)}`,
    );
    if (preparationStarted) {
      await restartDesktopApp().catch(() => {});
    }
    throw error;
  }
}

/**
 * Release a Tauri Update resource without invalidating an in-flight install.
 * React routes may unmount while a download is active; closing the resource
 * before `Update.install()` would make an otherwise valid update fail.
 */
export function releaseDesktopUpdate(update: Update): Promise<void> {
  const existing = updateCloseTasks.get(update);
  if (existing) return existing;

  const activeTask = activeInstall && activeInstallUpdate === update
    ? activeInstall.catch(() => {})
    : Promise.resolve();
  const closing = activeTask
    .then(() => update.close())
    .catch(async (error) => {
      await recordUpdateEvent("resource_close_error", `detail=${sanitizedLogDetail(error)}`);
    });
  updateCloseTasks.set(update, closing);
  return closing;
}

export function installDesktopUpdate(
  update: Update,
  onProgress?: (progress: number | null) => void,
  onStage?: (stage: DesktopUpdateInstallStage) => void,
): Promise<void> {
  if (activeInstall) return activeInstall;

  const task = runDesktopUpdateInstall(update, onProgress, onStage);
  activeInstallUpdate = update;
  activeInstall = task.finally(() => {
    activeInstall = null;
    activeInstallUpdate = null;
  });
  return activeInstall;
}
