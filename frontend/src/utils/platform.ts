/**
 * 平台运行时工具 (Platform Runtime Utils)
 * =======================================
 *
 * 统一处理 Tauri 桌面端与 Web 浏览器端的运行时差异。
 *
 * 设计原则
 * --------
 * - 单一来源：所有"isTauri 判断"集中在此模块，避免散落在各组件内联
 * - 安全降级：Tauri API 不可用时优雅降级到 Web 行为，不抛错
 * - TypeScript 友好：通过声明 `window.__TAURI__` 类型，避免 any 滥用
 */

// Tauri v2 通过 __TAURI_INTERNALS__ 注入运行时（即使 withGlobalTauri=false 也存在）
// withGlobalTauri=true 时还会注入 window.__TAURI__，包含 shell 等模块
declare global {
  interface Window {
    __TAURI_INTERNALS__?: unknown;
    __TAURI__?: {
      core?: {
        invoke?: <T>(command: string, args?: Record<string, unknown>) => Promise<T>;
      };
      shell?: {
        open?: (url: string) => Promise<void>;
      };
    };
  }
}

/**
 * 检测当前是否运行在 Tauri 桌面端
 * 替代此前散落在 client.ts:83 / SettingsPage.tsx:230 的内联检测
 */
export function isTauri(): boolean {
  return !!(window as Window).__TAURI_INTERNALS__;
}

export interface DesktopStorageSettings {
  creativeDataDir: string;
  cacheDir: string;
  pendingCreativeDataDir: string | null;
  pendingCacheDir: string | null;
  defaultCreativeDataDir: string;
  defaultCacheDir: string;
  restartRequired: boolean;
  migrationError: string | null;
}

export interface DesktopStorageUpdate {
  creativeDataDir: string;
  cacheDir: string;
}

type TauriInvoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

function getTauriInvoke(): TauriInvoke {
  const invoke = window.__TAURI__?.core?.invoke;
  if (!isTauri() || !invoke) {
    throw new Error("该操作仅在万象谱桌面版中可用");
  }
  return invoke as TauriInvoke;
}

export async function getDesktopStorageSettings(): Promise<DesktopStorageSettings> {
  return getTauriInvoke()<DesktopStorageSettings>("get_desktop_storage_settings");
}

export async function selectDesktopStorageDirectory(
  kind: "creative" | "cache"
): Promise<string | null> {
  return getTauriInvoke()<string | null>("select_desktop_storage_directory", { kind });
}

export async function updateDesktopStorageSettings(
  settings: DesktopStorageUpdate
): Promise<DesktopStorageSettings> {
  return getTauriInvoke()<DesktopStorageSettings>("update_desktop_storage_settings", {
    settings,
  });
}

export async function restartDesktopApp(): Promise<void> {
  await getTauriInvoke()<void>("restart_desktop_app");
}

/**
 * 在桌面更新器覆盖安装目录前，关闭由当前宿主管理的内置 Python 后台，
 * 并等待其监听端口真正释放。Rust 端失败时会阻止安装继续。
 */
export async function prepareDesktopUpdate(): Promise<void> {
  await getTauriInvoke()<void>("prepare_desktop_update");
}

/** Persist a bounded, non-sensitive updater stage marker for release diagnosis. */
export async function recordDesktopUpdateEvent(stage: string, detail = ""): Promise<void> {
  await getTauriInvoke()<void>("record_desktop_update_event", { stage, detail });
}

export async function openDesktopStorageDirectory(path: string): Promise<void> {
  await getTauriInvoke()<void>("open_desktop_storage_directory", { path });
}

/**
 * 获取桌面宿主为外部更新请求解析出的系统代理。
 * 代理只在当前检查/下载流程中使用，不持久化到前端配置。
 */
export async function getDesktopSystemProxy(): Promise<string | null> {
  return getTauriInvoke()<string | null>("desktop_system_proxy");
}

/**
 * 跨平台打开外部 URL
 *
 * - Tauri 桌面端：通过 shell.open 转交系统默认浏览器打开
 *   （CSP 限制 webview 内无法直接跳转外部域名）
 * - Web 浏览器端：使用 window.open 在新标签页打开
 *
 * @param url 完整的 http(s) URL
 * @returns 是否成功触发打开行为（不代表用户一定看到了页面）
 */
export async function openExternalUrl(url: string): Promise<boolean> {
  // 输入校验：必须是 http(s) URL，防止 javascript: 等协议注入
  if (!/^https?:\/\//i.test(url)) {
    console.warn(`[platform] 拒绝打开非 http(s) URL: ${url}`);
    return false;
  }

  // Tauri 桌面端：优先走 shell.open
  if (isTauri()) {
    try {
      const tauriShell = (window as Window).__TAURI__?.shell;
      if (tauriShell?.open) {
        await tauriShell.open(url);
        return true;
      }
      // 极少数情况：__TAURI_INTERNALS__ 存在但 __TAURI__ 未注入
      console.warn("[platform] Tauri 环境检测到，但 shell.open 不可用，降级到 window.open");
    } catch (err) {
      console.error("[platform] shell.open 失败，降级到 window.open:", err);
    }
  }

  // Web 浏览器端：window.open 新标签页
  try {
    const win = window.open(url, "_blank", "noopener,noreferrer");
    return !!win;
  } catch (err) {
    console.error("[platform] window.open 失败:", err);
    return false;
  }
}
