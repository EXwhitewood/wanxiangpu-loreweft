/* Hallmark · pre-emit critique: P5 H5 E5 S5 R5 V5 */
/* Hallmark · component: storage settings section · genre: editorial utility · theme: project DESIGN.md
 * states: default · hover · focus · active · disabled · loading · error · success
 * contrast: uses project semantic tokens
 */
import { useEffect, useMemo, useState } from "react";
import {
  CheckCircle2,
  CircleAlert,
  Clock3,
  Database,
  FolderTree,
  FolderOpen,
  HardDrive,
  Loader2,
  RotateCcw,
  Save,
} from "lucide-react";
import {
  getDesktopStorageSettings,
  isTauri,
  openDesktopStorageDirectory,
  restartDesktopApp,
  selectDesktopStorageDirectory,
  updateDesktopStorageSettings,
  type DesktopStorageSettings,
} from "@/utils/platform";

type StorageKind = "creative" | "cache";
type Feedback = { tone: "error" | "success"; message: string } | null;

function messageFrom(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export default function DesktopStorageSettingsSection() {
  const desktop = isTauri();
  const [settings, setSettings] = useState<DesktopStorageSettings | null>(null);
  const [creativeDataDir, setCreativeDataDir] = useState("");
  const [cacheDir, setCacheDir] = useState("");
  const [loading, setLoading] = useState(desktop);
  const [saving, setSaving] = useState(false);
  const [selecting, setSelecting] = useState<StorageKind | null>(null);
  const [restarting, setRestarting] = useState(false);
  const [feedback, setFeedback] = useState<Feedback>(null);

  useEffect(() => {
    if (!desktop) return;
    let cancelled = false;
    getDesktopStorageSettings()
      .then((value) => {
        if (cancelled) return;
        setSettings(value);
        setCreativeDataDir(value.pendingCreativeDataDir || value.creativeDataDir);
        setCacheDir(value.pendingCacheDir || value.cacheDir);
      })
      .catch((error) => {
        if (!cancelled) {
          setFeedback({ tone: "error", message: `读取存储设置失败：${messageFrom(error)}` });
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [desktop]);

  const dirty = useMemo(() => {
    if (!settings) return false;
    return (
      creativeDataDir !== (settings.pendingCreativeDataDir || settings.creativeDataDir) ||
      cacheDir !== (settings.pendingCacheDir || settings.cacheDir)
    );
  }, [cacheDir, creativeDataDir, settings]);

  const chooseDirectory = async (kind: StorageKind) => {
    setSelecting(kind);
    setFeedback(null);
    try {
      const selected = await selectDesktopStorageDirectory(kind);
      if (!selected) return;
      if (kind === "creative") setCreativeDataDir(selected);
      else setCacheDir(selected);
    } catch (error) {
      setFeedback({ tone: "error", message: messageFrom(error) });
    } finally {
      setSelecting(null);
    }
  };

  const save = async () => {
    setSaving(true);
    setFeedback(null);
    try {
      const value = await updateDesktopStorageSettings({ creativeDataDir, cacheDir });
      setSettings(value);
      setCreativeDataDir(value.pendingCreativeDataDir || value.creativeDataDir);
      setCacheDir(value.pendingCacheDir || value.cacheDir);
      setFeedback({
        tone: "success",
        message: value.restartRequired
          ? "新位置已登记。完全退出并重新打开万象谱后生效。"
          : "存储位置未发生变化。",
      });
    } catch (error) {
      setFeedback({ tone: "error", message: `保存失败：${messageFrom(error)}` });
    } finally {
      setSaving(false);
    }
  };

  const openDirectory = async (path: string) => {
    setFeedback(null);
    try {
      await openDesktopStorageDirectory(path);
    } catch (error) {
      setFeedback({ tone: "error", message: `无法打开目录：${messageFrom(error)}` });
    }
  };

  const restartNow = async () => {
    setRestarting(true);
    setFeedback(null);
    try {
      await restartDesktopApp();
    } catch (error) {
      setRestarting(false);
      setFeedback({ tone: "error", message: `重启应用失败：${messageFrom(error)}` });
    }
  };

  const restoreDefaults = () => {
    if (!settings) return;
    setCreativeDataDir(settings.defaultCreativeDataDir);
    setCacheDir(settings.defaultCacheDir);
    setFeedback(null);
  };

  if (!desktop) {
    return (
      <section className="group relative overflow-hidden rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-tool)] p-5 sm:p-7">
        <FolderTree className="pointer-events-none absolute -right-4 top-2 h-36 w-36 text-[var(--color-ink)] opacity-[0.04] transition-transform duration-300 group-hover:-translate-x-1 group-hover:translate-y-1 motion-reduce:transform-none motion-reduce:transition-none" aria-hidden="true" />
        <div className="relative">
          <div className="flex items-center gap-2.5">
            <span className="h-5 w-1 rounded-full bg-[var(--color-accent)]" aria-hidden="true" />
            <h2 className="text-base font-semibold text-[var(--color-ink-strong)]">本机存储</h2>
          </div>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-[var(--color-ink-muted)]">
            存储位置仅能在万象谱桌面应用中调整。
          </p>
        </div>
      </section>
    );
  }

  return (
    <section
      className="group relative overflow-hidden rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-tool)] p-5 sm:p-7"
      aria-labelledby="desktop-storage-title"
    >
      <FolderTree className="pointer-events-none absolute -right-5 top-3 h-40 w-40 text-[var(--color-ink)] opacity-[0.045] transition-transform duration-300 group-hover:-translate-x-1 group-hover:translate-y-1 motion-reduce:transform-none motion-reduce:transition-none" aria-hidden="true" />

      <div className="relative z-10">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="flex items-center gap-2.5">
              <span className="h-5 w-1 rounded-full bg-[var(--color-accent)]" aria-hidden="true" />
              <h2 id="desktop-storage-title" className="text-base font-semibold text-[var(--color-ink-strong)]">
                本机存储
              </h2>
            </div>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-[var(--color-ink-muted)]">
              分别管理作品资料与软件缓存。更改后不会立即移动正在使用的数据。
            </p>
          </div>
          {(dirty || settings?.restartRequired) && (
            <span className="inline-flex min-h-8 shrink-0 items-center gap-2 whitespace-nowrap rounded-md border border-[var(--color-highlight)] bg-[var(--color-highlight-soft)] px-3 text-xs font-medium text-[var(--color-ink)]">
              <Clock3 className="h-3.5 w-3.5" aria-hidden="true" />
              {dirty ? "有未保存更改" : "重新打开后生效"}
            </span>
          )}
        </div>

        {loading ? (
          <div className="mt-6 flex min-h-36 items-center justify-center rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-paper)] text-sm text-[var(--color-ink-muted)]">
            <Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
            正在读取本机存储设置
          </div>
        ) : (
          <div className="mt-6 overflow-hidden rounded-lg border border-[var(--border-emphasis)] bg-[var(--surface-paper)]">
            <StoragePathRow
              icon={Database}
              label="创作数据"
              description="作品正文、世界观、人物与 API 配置"
              value={creativeDataDir}
              currentValue={settings?.creativeDataDir || ""}
              pendingValue={settings?.pendingCreativeDataDir || null}
              selecting={selecting === "creative"}
              disabled={saving || selecting !== null}
              onChoose={() => chooseDirectory("creative")}
              onOpen={() => (settings ? openDirectory(settings.creativeDataDir) : undefined)}
            />
            <StoragePathRow
              icon={HardDrive}
              label="缓存与日志"
              description="运行日志与临时文件，不包含作品正文"
              value={cacheDir}
              currentValue={settings?.cacheDir || ""}
              pendingValue={settings?.pendingCacheDir || null}
              selecting={selecting === "cache"}
              disabled={saving || selecting !== null}
              onChoose={() => chooseDirectory("cache")}
              onOpen={() => (settings ? openDirectory(settings.cacheDir) : undefined)}
            />
          </div>
        )}

        {settings?.migrationError && (
          <div className="mt-4 flex items-start gap-2 rounded-md border border-[var(--color-error)] bg-[var(--color-error-soft)] px-3 py-2.5 text-xs leading-5 text-[var(--color-error)]" role="alert">
            <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            <p>上次迁移没有完成：{settings.migrationError}</p>
          </div>
        )}

        <div className="mt-5 flex flex-col gap-4 border-t border-[var(--border-subtle)] pt-5 sm:flex-row sm:items-center sm:justify-between">
          <div
            className={`flex min-h-6 min-w-0 items-start gap-2 text-xs leading-5 ${
              feedback?.tone === "error"
                ? "text-[var(--color-error)]"
                : feedback?.tone === "success"
                  ? "text-[var(--color-accent)]"
                  : "text-[var(--color-ink-muted)]"
            }`}
            aria-live="polite"
          >
            {feedback?.tone === "error" ? (
              <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            ) : feedback?.tone === "success" ? (
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            ) : (
              <Clock3 className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            )}
            <p>
            {feedback?.message || "保存后请完全退出并重新打开万象谱；原创作数据目录会保留为备份。"}
            </p>
          </div>
          <div className="flex shrink-0 flex-wrap gap-2">
            {settings?.restartRequired && !dirty && (
              <button
                type="button"
                onClick={restartNow}
                disabled={restarting || saving || loading}
                className="inline-flex min-h-11 items-center justify-center gap-2 whitespace-nowrap rounded-md border border-[var(--color-highlight)] bg-[var(--color-highlight-soft)] px-3 text-xs font-semibold text-[var(--color-ink)] transition-colors duration-150 hover:border-[var(--color-accent)] hover:bg-[var(--surface-raised)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)] active:translate-y-px motion-reduce:active:transform-none disabled:cursor-not-allowed disabled:opacity-50"
              >
                {restarting ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />
                ) : (
                  <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
                )}
                {restarting ? "正在重启" : "立即重启并生效"}
              </button>
            )}
            <button
              type="button"
              onClick={restoreDefaults}
              disabled={!settings || saving || loading}
 className="inline-flex min-h-11 items-center justify-center gap-2 whitespace-nowrap rounded-md border border-[var(--border-emphasis)] bg-[var(--surface-paper)] px-3 text-xs font-medium text-[var(--color-ink)] transition-colors duration-150 hover:border-[var(--color-accent)] hover:text-[var(--color-accent)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)] active:translate-y-px motion-reduce:active:transform-none disabled:cursor-not-allowed disabled:opacity-50"
            >
              <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
              恢复默认
            </button>
            <button
              type="button"
              onClick={save}
              disabled={!settings || loading || saving || selecting !== null || !dirty}
 className="inline-flex min-h-11 items-center justify-center gap-2 whitespace-nowrap rounded-md bg-[var(--color-accent)] px-4 text-xs font-semibold text-[var(--color-on-accent)] transition-colors duration-150 hover:bg-[var(--color-accent-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)] active:translate-y-px motion-reduce:active:transform-none disabled:cursor-not-allowed disabled:opacity-50"
            >
              {saving ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />
              ) : (
                <Save className="h-3.5 w-3.5" aria-hidden="true" />
              )}
              {saving ? "正在登记" : "保存路径设置"}
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}

function StoragePathRow({
  icon: Icon,
  label,
  description,
  value,
  currentValue,
  pendingValue,
  selecting,
  disabled,
  onChoose,
  onOpen,
}: {
  icon: typeof Database;
  label: string;
  description: string;
  value: string;
  currentValue: string;
  pendingValue: string | null;
  selecting: boolean;
  disabled: boolean;
  onChoose: () => void;
  onOpen: () => void | Promise<void>;
}) {
  const committedValue = pendingValue || currentValue;
  const hasUncommittedChange = Boolean(value) && value !== committedValue;
  const hasUpcomingChange = hasUncommittedChange || Boolean(pendingValue);
  const pathStateLabel = hasUncommittedChange
    ? "尚未保存"
    : pendingValue
      ? "下次启动使用"
      : "当前使用";

  return (
    <div className="grid min-w-0 gap-5 border-b border-[var(--border-subtle)] px-5 py-6 last:border-b-0 lg:grid-cols-[13.5rem_minmax(0,1fr)] xl:grid-cols-[13.5rem_minmax(0,1fr)_auto] xl:items-center">
      <div className="flex min-w-0 items-start gap-3.5">
        <div className="grid size-10 shrink-0 place-items-center rounded-lg border border-[var(--border-subtle)] bg-[var(--color-accent-soft)] text-[var(--color-accent)]">
          <Icon className="h-[18px] w-[18px]" aria-hidden="true" />
        </div>
        <div className="min-w-0">
          <p className="text-sm font-semibold text-[var(--color-ink-strong)]">{label}</p>
          <p className="mt-1 text-xs leading-5 text-[var(--color-ink-muted)]">{description}</p>
        </div>
      </div>
      <div className="min-w-0 space-y-2">
        <div
          className={`min-w-0 rounded-lg border px-4 py-3 ${
            hasUpcomingChange
              ? "border-[var(--color-highlight)] bg-[var(--color-highlight-soft)]"
              : "border-[var(--border-subtle)] bg-[var(--surface-raised)]"
          }`}
        >
          <div className="mb-1 flex items-center gap-2">
            <span className="text-[10px] font-semibold tracking-[0.08em] text-[var(--color-ink-muted)]">
              {pathStateLabel}
            </span>
            {hasUpcomingChange && <Clock3 className="h-3 w-3 text-[var(--color-highlight)]" aria-hidden="true" />}
          </div>
          <p className="truncate font-mono text-xs text-[var(--color-ink-strong)]" title={value || "未读取"}>
            {value || "未读取"}
          </p>
        </div>
        {hasUpcomingChange && (
          <p className="truncate px-1 text-[11px] text-[var(--color-ink-muted)]" title={currentValue}>
            现在仍在使用：<span className="font-mono">{currentValue || "未读取"}</span>
          </p>
        )}
      </div>
      <div className="flex flex-wrap gap-2 lg:col-start-2 xl:col-start-auto xl:justify-end">
        <button
          type="button"
          onClick={onOpen}
          disabled={disabled || !currentValue}
 className="inline-flex min-h-11 items-center justify-center whitespace-nowrap rounded-md border border-[var(--border-emphasis)] bg-[var(--surface-paper)] px-3 text-xs font-medium text-[var(--color-ink)] transition-colors duration-150 hover:border-[var(--color-accent)] hover:text-[var(--color-accent)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)] active:translate-y-px motion-reduce:active:transform-none disabled:cursor-not-allowed disabled:opacity-50"
        >
          打开当前目录
        </button>
        <button
          type="button"
          onClick={onChoose}
          disabled={disabled}
 className="inline-flex min-h-11 items-center justify-center gap-2 whitespace-nowrap rounded-md border border-[var(--border-emphasis)] bg-[var(--surface-raised)] px-3 text-xs font-medium text-[var(--color-ink)] transition-colors duration-150 hover:border-[var(--color-accent)] hover:text-[var(--color-accent)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)] active:translate-y-px motion-reduce:active:transform-none disabled:cursor-not-allowed disabled:opacity-50"
        >
          {selecting ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />
          ) : (
            <FolderOpen className="h-3.5 w-3.5" aria-hidden="true" />
          )}
          {selecting ? "选择中" : "选择位置"}
        </button>
      </div>
    </div>
  );
}
