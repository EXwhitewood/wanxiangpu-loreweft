import { useCallback, useLayoutEffect, useMemo, useState } from "react";
import { Minus, X } from "lucide-react";
import { isTauri } from "@/utils/platform";
import "./DesktopTitleBar.css";

interface DesktopWindowHandle {
  close: () => Promise<void>;
  isMaximized: () => Promise<boolean>;
  minimize: () => Promise<void>;
  toggleMaximize: () => Promise<void>;
}

interface TauriWindowGlobal {
  window?: {
    getCurrentWindow?: () => DesktopWindowHandle;
  };
}

function getDesktopWindow(): DesktopWindowHandle | null {
  if (!isTauri()) return null;
  return ((window.__TAURI__ as TauriWindowGlobal | undefined)?.window?.getCurrentWindow?.()) ?? null;
}

function WindowStateGlyph({ maximized }: { maximized: boolean }) {
  return maximized ? (
    <svg viewBox="0 0 16 16" aria-hidden="true">
      <rect x="3.5" y="5.5" width="7" height="7" rx="0.5" />
      <path d="M5.5 5.5V3.5h7v7h-2" />
    </svg>
  ) : (
    <svg viewBox="0 0 16 16" aria-hidden="true">
      <rect x="3.5" y="3.5" width="9" height="9" rx="0.5" />
    </svg>
  );
}

function LoreweftWindowMark() {
  return (
    <span className="desktop-titlebar__mark" aria-hidden="true">
      <svg viewBox="0 0 20 20">
        <path d="M10 2.4 16.5 6v8L10 17.6 3.5 14V6L10 2.4Z" />
        <path d="M10 5.3 13.9 7.5v5L10 14.7l-3.9-2.2v-5L10 5.3Z" />
        <path d="M10 5.3v9.4M6.1 7.5l7.8 5M13.9 7.5l-7.8 5" />
      </svg>
    </span>
  );
}

export default function DesktopTitleBar() {
  const desktopWindow = useMemo(getDesktopWindow, []);
  const [maximized, setMaximized] = useState(false);

  const syncMaximizedState = useCallback(async () => {
    if (!desktopWindow) return;
    try {
      setMaximized(await desktopWindow.isMaximized());
    } catch (error) {
      console.warn("[desktop-titlebar] 无法读取窗口状态", error);
    }
  }, [desktopWindow]);

  useLayoutEffect(() => {
    if (!desktopWindow) return;
    document.documentElement.dataset.desktopChrome = "custom";
    void syncMaximizedState();

    window.addEventListener("resize", syncMaximizedState);
    return () => {
      window.removeEventListener("resize", syncMaximizedState);
      delete document.documentElement.dataset.desktopChrome;
    };
  }, [desktopWindow, syncMaximizedState]);

  if (!desktopWindow) return null;

  const runWindowCommand = (command: () => Promise<void>) => {
    void command().catch((error) => {
      console.error("[desktop-titlebar] 窗口操作失败", error);
    });
  };

  const handleToggleMaximize = () => {
    void desktopWindow
      .toggleMaximize()
      .then(syncMaximizedState)
      .catch((error) => console.error("[desktop-titlebar] 切换窗口大小失败", error));
  };

  return (
    <header
      className={`desktop-titlebar${maximized ? " desktop-titlebar--maximized" : ""}`}
      data-tauri-drag-region
    >
      <div className="desktop-titlebar__identity" data-tauri-drag-region>
        <LoreweftWindowMark />
        <span className="desktop-titlebar__name" data-tauri-drag-region>
          万象谱
        </span>
        <span className="desktop-titlebar__divider" aria-hidden="true" />
        <span className="desktop-titlebar__descriptor" data-tauri-drag-region>
          智能小说创作引擎
        </span>
      </div>

      <div className="desktop-titlebar__controls" aria-label="窗口控制">
        <button
          type="button"
          className="desktop-titlebar__button"
          aria-label="最小化窗口"
          title="最小化"
          onClick={() => runWindowCommand(() => desktopWindow.minimize())}
        >
          <Minus aria-hidden="true" />
        </button>
        <button
          type="button"
          className="desktop-titlebar__button"
          aria-label={maximized ? "还原窗口" : "最大化窗口"}
          title={maximized ? "还原" : "最大化"}
          onClick={handleToggleMaximize}
        >
          <WindowStateGlyph maximized={maximized} />
        </button>
        <button
          type="button"
          className="desktop-titlebar__button desktop-titlebar__button--close"
          aria-label="关闭窗口"
          title="关闭"
          onClick={() => runWindowCommand(() => desktopWindow.close())}
        >
          <X aria-hidden="true" />
        </button>
      </div>
    </header>
  );
}
