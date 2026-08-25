/* Hallmark · pre-emit critique: P5 H5 E5 S5 R5 V5 */
/* Hallmark · genre: editorial · macrostructure: existing settings workbench · design-system: DESIGN.md · designed-as-app */
import { useEffect, useState } from "react";
import {
  Eye,
  EyeOff,
  Loader2,
  Check,
  Copy,
  Monitor,
  Save,
  Sun,
  Moon,
  Laptop,
} from "lucide-react";
import { useSettingsStore } from "@/stores/settingsStore";
import * as api from "@/api/client";
import Toast from "@/components/Common/Toast";
import type { AppSettings, APIConfig, APIFormat, SystemInfo } from "@/types";
import { FadeInWrapper } from "@/components/UI/FadeInWrapper";
import EntertainmentSettingsSection from "@/pages/sections/EntertainmentSettingsSection";
import DesktopStorageSettingsSection from "@/pages/sections/DesktopStorageSettingsSection";
import DesktopUpdateSettingsSection from "@/pages/sections/DesktopUpdateSettingsSection";
import {
  getThemeTransitionOrigin,
  useTheme,
  type ThemePreference,
} from "@/hooks/useTheme";

function ApiConfigForm({
  label,
  config,
  onChange,
  onTest,
  onSave,
  testing,
  saving,
  testResult,
}: {
  label: string;
  config: APIConfig | null;
  onChange: (config: APIConfig) => void;
  onTest: () => void;
  onSave: () => void;
  testing: boolean;
  saving: boolean;
  testResult: { success: boolean; message: string } | null;
}) {
  const [showKey, setShowKey] = useState(false);
  const [copied, setCopied] = useState(false);

  const current: APIConfig = config || {
    api_format: label.includes("OpenAI") ? "openai_compatible" : "anthropic_compatible",
    api_key: "",
    base_url: "",
    model: "",
  };

  const update = (field: keyof APIConfig, value: string) => {
    onChange({ ...current, [field]: value });
  };

  const storedKeyIsMasked = current.api_key.includes("****");

  const handleRevealKey = () => {
    if (storedKeyIsMasked) return;
    setShowKey((value) => !value);
  };

  const handleCopyKey = async () => {
    const keyToCopy = current.api_key;
    if (!keyToCopy || storedKeyIsMasked) return;
    try {
      await navigator.clipboard.writeText(keyToCopy);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // ignore
    }
  };

  return (
    <div className="rounded-2xl border border-pine-200/50 bg-white/40 p-6 sm:p-8 shadow-sm backdrop-blur-md relative overflow-hidden group transition-all duration-300 hover:shadow-md">
      <div className="absolute top-0 right-0 p-8 opacity-5 pointer-events-none transition-transform duration-500 group-hover:rotate-12 group-hover:scale-110">
        <Monitor className="h-40 w-40 text-pine-900" />
      </div>
      <div className="relative z-10">
        <h3 className="mb-6 text-sm font-bold text-pine-900 tracking-wide flex items-center gap-2">
          <span className="w-1.5 h-4 bg-magic-500 rounded-full"></span>
          {label}
        </h3>
        
        <div className="space-y-5 rounded-xl bg-pine-50/50 p-6 border border-pine-100/50">
          <div>
            <label className="mb-2 block text-[13px] font-bold text-pine-800">API 密钥 (API Key)</label>
            <div className="relative">
              <input
                type={showKey ? "text" : "password"}
                value={current.api_key}
                onChange={(e) => {
                  update("api_key", e.target.value);
                }}
                className="w-full rounded-xl border border-pine-200/80 bg-white px-4 py-3 pr-24 text-sm text-pine-800 outline-none transition-all focus:border-magic-500/50 focus:ring-4 focus:ring-magic-500/10 shadow-sm"
                placeholder="sk-..."
              />
              <div className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-1">
                <button
                  onClick={handleRevealKey}
                  disabled={!current.api_key || storedKeyIsMasked}
                  className="p-2 text-pine-400 hover:text-magic-500 hover:bg-magic-50 rounded-lg transition-colors disabled:opacity-50 disabled:hover:bg-transparent"
                  title={storedKeyIsMasked ? "已保存密钥不会回传明文；输入新密钥时可在保存前查看" : showKey ? "隐藏密钥" : "显示密钥"}
                >
                  {showKey ? (
                    <EyeOff className="h-4 w-4" />
                  ) : (
                    <Eye className="h-4 w-4" />
                  )}
                </button>
                <button
                  onClick={handleCopyKey}
                  disabled={!current.api_key}
                  className="p-2 text-pine-400 hover:text-magic-500 hover:bg-magic-50 rounded-lg transition-colors disabled:opacity-50 disabled:hover:bg-transparent"
                  title="复制密钥"
                >
                  {copied ? (
                    <Check className="h-4 w-4 text-jade-500" />
                  ) : (
                    <Copy className="h-4 w-4" />
                  )}
                </button>
              </div>
            </div>
          </div>
          <div>
            <label className="mb-2 block text-[13px] font-bold text-pine-800">接口地址 (Base URL)</label>
            <input
              type="text"
              value={current.base_url}
              onChange={(e) => update("base_url", e.target.value)}
              className="w-full rounded-xl border border-pine-200/80 bg-white px-4 py-3 text-sm text-pine-800 outline-none transition-all focus:border-magic-500/50 focus:ring-4 focus:ring-magic-500/10 shadow-sm"
              placeholder={
                current.api_format === "openai_compatible"
                  ? "https://api.openai.com"
                  : "https://api.anthropic.com"
              }
            />
          </div>
          <div>
            <label className="mb-2 block text-[13px] font-bold text-pine-800">默认模型 (Model)</label>
            <input
              type="text"
              value={current.model}
              onChange={(e) => update("model", e.target.value)}
              className="w-full rounded-xl border border-pine-200/80 bg-white px-4 py-3 text-sm text-pine-800 outline-none transition-all focus:border-magic-500/50 focus:ring-4 focus:ring-magic-500/10 shadow-sm"
              placeholder={
                current.api_format === "openai_compatible" ? "gpt-4o" : "claude-sonnet-4-20250514"
              }
            />
          </div>
          
          <div className="pt-4 mt-4 border-t border-pine-200/50 flex flex-col sm:flex-row sm:items-center gap-4 justify-between">
            <div className="flex items-center gap-3">
              <button
                onClick={onTest}
                disabled={testing || !current.api_key || !current.base_url}
                className="flex items-center gap-2 rounded-xl bg-white border border-pine-200 px-4 py-2.5 text-[13px] font-bold text-pine-700 shadow-sm transition-all hover:bg-pine-50 hover:border-pine-300 hover:-translate-y-0.5 disabled:opacity-50 disabled:hover:translate-y-0"
              >
                {testing ? (
                  <Loader2 className="h-4 w-4 animate-spin text-magic-500" />
                ) : (
                  <Check className="h-4 w-4 text-magic-500" />
                )}
                测试连接
              </button>
              {testResult && (
                <span
                  className={`text-xs font-bold px-3 py-1.5 rounded-lg border ${
                    testResult.success 
                      ? "bg-jade-500/10 text-jade-600 border-jade-500/20" 
                      : "bg-crimson-500/10 text-crimson-600 border-crimson-500/20"
                  }`}
                >
                  {testResult.message}
                </span>
              )}
            </div>
            <button
              type="button"
              onClick={onSave}
              disabled={saving}
              className="inline-flex items-center justify-center gap-2 rounded-xl bg-pine-900 px-5 py-2.5 text-[13px] font-bold text-white shadow-sm shadow-pine-900/20 transition-all hover:-translate-y-0.5 hover:bg-pine-800 hover:shadow-md disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:translate-y-0"
            >
              {saving ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Save className="h-4 w-4" />
              )}
              {saving ? "保存中..." : "保存 API 配置"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function SystemInfoPanel() {
  const [sysInfo, setSysInfo] = useState<SystemInfo | null>(null);

  useEffect(() => {
    api.getSystemInfo()
      .then(setSysInfo)
      .catch(() => {});
  }, []);

  const BACKEND_LABELS: Record<string, string> = {
    sqlite: "SQLite + networkx",
    sqlite_vss: "SQLite + sqlite-vss",
    persistent: "PersistentDAG",
    asyncio: "SimpleDAG",
  };

  const isTauri = !!(window as any).__TAURI_INTERNALS__;

  return (
    <div className="mt-6 overflow-hidden rounded-lg border border-[var(--border-emphasis)] bg-[var(--surface-paper)]">
      <div className="flex items-center gap-3 border-b border-[var(--border-subtle)] px-5 py-5">
        <div className="grid size-10 shrink-0 place-items-center rounded-lg border border-[var(--border-subtle)] bg-[var(--color-accent-soft)] text-[var(--color-accent)]">
          <Monitor className="h-5 w-5" aria-hidden="true" />
        </div>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-base font-semibold text-[var(--color-ink-strong)]">
              普通模式 (Standard Mode)
            </span>
            {isTauri && (
              <span className="rounded-md border border-[var(--border-emphasis)] bg-[var(--color-accent-soft)] px-2 py-0.5 text-[10px] font-semibold tracking-wide text-[var(--color-accent)]">
                桌面端
              </span>
            )}
          </div>
          <p className="mt-1 text-xs text-[var(--color-ink-muted)]">本地轻量级运行环境</p>
        </div>
      </div>
      <div className="divide-y divide-[var(--border-subtle)]">
        <SystemInfoRow label="运行模式" value="普通（本地嵌入式）" />
        <SystemInfoRow
          label="核心数据层"
          value={BACKEND_LABELS[sysInfo?.core_backend || ""] || sysInfo?.core_backend || "加载中..."}
        />
        <SystemInfoRow
          label="细节检索层"
          value={BACKEND_LABELS[sysInfo?.shell_backend || ""] || sysInfo?.shell_backend || "加载中..."}
        />
        <SystemInfoRow
          label="工作流引擎"
          value={BACKEND_LABELS[sysInfo?.workflow_backend || ""] || sysInfo?.workflow_backend || "加载中..."}
        />
      </div>
    </div>
  );
}

function SystemInfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-2 px-5 py-4 transition-colors duration-150 hover:bg-[var(--surface-raised)] sm:flex-row sm:items-center sm:justify-between">
      <span className="text-[13px] font-semibold text-[var(--color-ink)]">{label}</span>
      <span className="w-fit rounded-md border border-[var(--border-subtle)] bg-[var(--surface-tool)] px-3 py-1 text-[13px] font-semibold text-[var(--color-ink-strong)]">
        {value}
      </span>
    </div>
  );
}

function isMaskedKey(key: string | null | undefined): boolean {
  if (!key) return false;
  return key.includes("****");
}

function mergeSettingsPreservingKeys(
  server: AppSettings,
  local: AppSettings
): AppSettings {
  const result = { ...server, global: { ...server.global }, agent_overrides: { ...server.agent_overrides } };

  for (const fmt of ["openai_compatible", "anthropic_compatible"] as const) {
    const serverCfg = server.global[fmt];
    const localCfg = local.global[fmt];
    if (serverCfg && localCfg && localCfg.api_key && !isMaskedKey(localCfg.api_key)) {
      result.global[fmt] = { ...serverCfg, api_key: localCfg.api_key };
    }
  }

  for (const name of Object.keys(local.agent_overrides)) {
    const serverOverride = server.agent_overrides[name];
    const localOverride = local.agent_overrides[name];
    if (serverOverride && localOverride && localOverride.api_key && !isMaskedKey(localOverride.api_key)) {
      result.agent_overrides[name] = { ...serverOverride, api_key: localOverride.api_key };
    }
  }

  return result;
}

export default function SettingsPage() {
  const { settings, fetchSettings, updateSettings, testConnection } = useSettingsStore();
  const [localSettings, setLocalSettings] = useState<AppSettings>(settings);
  const [activeFormat, setActiveFormat] = useState<APIFormat>("openai_compatible");
  const [testingApi, setTestingApi] = useState(false);
  const [apiTestResult, setApiTestResult] = useState<{
    success: boolean;
    message: string;
  } | null>(null);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<{
    type: "success" | "error";
    message: string;
  } | null>(null);
  const { theme, setTheme } = useTheme();

  useEffect(() => {
    fetchSettings();
  }, [fetchSettings]);

  useEffect(() => {
    setLocalSettings((prev) => mergeSettingsPreservingKeys(settings, prev));
    if (settings.global.openai_compatible) {
      setActiveFormat("openai_compatible");
    } else if (settings.global.anthropic_compatible) {
      setActiveFormat("anthropic_compatible");
    }
  }, [settings]);

  const handleFormatChange = (format: APIFormat) => {
    setActiveFormat(format);
    const currentConfig = format === "openai_compatible"
      ? localSettings.global.openai_compatible
      : localSettings.global.anthropic_compatible;

    if (!currentConfig) {
      setLocalSettings((s) => ({
        ...s,
        global: {
          ...s.global,
          [format]: {
            api_format: format,
            api_key: "",
            base_url: format === "openai_compatible" ? "https://api.openai.com" : "https://api.anthropic.com",
            model: format === "openai_compatible" ? "gpt-4o" : "claude-sonnet-4-20250514",
          },
        },
      }));
    }
  };

  const handleTestApi = async () => {
    const config = activeFormat === "openai_compatible"
      ? localSettings.global.openai_compatible
      : localSettings.global.anthropic_compatible;
    if (!config) return;

    setTestingApi(true);
    setApiTestResult(null);
    const result = await testConnection({
      api_format: config.api_format,
      api_key: config.api_key,
      base_url: config.base_url,
      model: config.model,
    });
    setApiTestResult(result);
    setTestingApi(false);
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await updateSettings(localSettings);
      setToast({ type: "success", message: "设置已保存" });
    } catch {
      setToast({ type: "error", message: "保存失败" });
    } finally {
      setSaving(false);
    }
  };

  const activeConfig = activeFormat === "openai_compatible"
    ? localSettings.global.openai_compatible
    : localSettings.global.anthropic_compatible;

  return (
    <FadeInWrapper className="settings-page mx-auto h-full w-full min-w-0 max-w-5xl overflow-x-hidden overflow-y-auto px-6 py-12 lg:px-12 relative z-10 scrollbar-thin">
      <div className="mb-10 flex flex-col items-start gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="settings-title text-3xl font-extrabold text-transparent bg-clip-text bg-gradient-to-r from-pine-900 to-pine-600 tracking-tight drop-shadow-sm">
            系统偏好与配置
          </h1>
          <p className="text-[13px] font-bold tracking-widest uppercase text-pine-500 mt-2">
            System Preferences & API Configurations
          </p>
        </div>
        <button
          onClick={handleSave}
          disabled={saving}
          className="settings-save-button flex w-full items-center justify-center gap-2 rounded-xl bg-pine-900 px-6 py-3 text-sm font-bold text-white shadow-lg shadow-pine-900/20 transition-all hover:bg-pine-800 hover:shadow-pine-900/40 hover:-translate-y-0.5 disabled:opacity-50 disabled:hover:translate-y-0 sm:w-auto"
        >
          {saving ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              保存中...
            </>
          ) : (
            <>
              <Save className="h-4 w-4" />
              保存全部配置
            </>
          )}
        </button>
      </div>

      <div className="space-y-8 pb-12">
        <section className="border-y border-[var(--border-subtle)] py-6">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h2 className="text-sm font-semibold text-[var(--color-ink-strong)]">外观主题</h2>
            </div>
            <div className="grid w-full grid-cols-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-tool)] p-1 sm:w-auto" role="group" aria-label="外观主题">
              {([
                ["light", "浅色", Sun],
                ["night", "暗夜", Moon],
                ["system", "跟随系统", Laptop],
              ] as Array<[ThemePreference, string, typeof Sun]>).map(([value, label, Icon]) => {
                const selected = theme === value;
                return (
                  <button
                    key={value}
                    type="button"
                    onClick={(event) => setTheme(value, getThemeTransitionOrigin(event.currentTarget))}
                    aria-pressed={selected}
                    className={`inline-flex min-w-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-md px-2 py-2 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)] sm:min-w-24 sm:px-3 ${
                      selected
                        ? "bg-[var(--surface-raised)] text-[var(--color-accent)] shadow-sm"
                        : "text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]"
                    }`}
                  >
                    <Icon className="h-3.5 w-3.5" />
                    {label}
                  </button>
                );
              })}
            </div>
          </div>
        </section>

        <DesktopStorageSettingsSection />

        <DesktopUpdateSettingsSection />

        <div className="rounded-2xl border border-pine-200/50 bg-white/40 p-6 sm:p-8 shadow-sm backdrop-blur-md relative overflow-hidden group transition-all duration-300 hover:shadow-md">
          <div className="absolute top-0 right-0 p-8 opacity-5 pointer-events-none transition-transform duration-500 group-hover:rotate-12 group-hover:scale-110">
            <Monitor className="h-40 w-40 text-pine-900" />
          </div>
          <div className="relative z-10">
            <h2 className="mb-6 text-base font-bold tracking-wide text-pine-900 flex items-center gap-2">
              <span className="w-1.5 h-4 bg-magic-500 rounded-full shadow-[0_0_8px_rgba(245,158,11,0.6)]"></span>
              基础通信设置 (Basic Communication Settings)
            </h2>

            <div className="mb-6">
              <label className="mb-3 block text-sm font-bold text-pine-900">
                API 服务提供商 (API Provider)
              </label>
              <div className="flex flex-col sm:flex-row gap-3">
                <button
                  onClick={() => handleFormatChange("openai_compatible")}
                  className={`flex-1 rounded-xl border px-5 py-3 text-sm font-bold transition-all duration-300 hover:-translate-y-0.5 ${
                    activeFormat === "openai_compatible"
                      ? "border-magic-500/50 bg-magic-500/10 text-magic-600 shadow-sm ring-1 ring-magic-500/20"
                      : "border-pine-200/60 bg-white/50 text-pine-700 hover:border-pine-300/60 hover:bg-white/70 hover:shadow-sm"
                  }`}
                >
                  OpenAI 兼容
                </button>
                <button
                  onClick={() => handleFormatChange("anthropic_compatible")}
                  className={`flex-1 rounded-xl border px-5 py-3 text-sm font-bold transition-all duration-300 hover:-translate-y-0.5 ${
                    activeFormat === "anthropic_compatible"
                      ? "border-magic-500/50 bg-magic-500/10 text-magic-600 shadow-sm ring-1 ring-magic-500/20"
                      : "border-pine-200/60 bg-white/50 text-pine-700 hover:border-pine-300/60 hover:bg-white/70 hover:shadow-sm"
                  }`}
                >
                  Anthropic 兼容
                </button>
              </div>
            </div>

            <ApiConfigForm
              label={activeFormat === "openai_compatible" ? "OpenAI 兼容格式" : "Anthropic 兼容格式"}
              config={activeConfig}
              onChange={(config) =>
                setLocalSettings((s) => ({
                  ...s,
                  global: { ...s.global, [activeFormat]: config },
                }))
              }
              onTest={handleTestApi}
              onSave={handleSave}
              testing={testingApi}
              saving={saving}
              testResult={apiTestResult}
            />
          </div>
        </div>

        <section className="group relative overflow-hidden rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-tool)] p-6 sm:p-8" aria-labelledby="system-environment-title">
          <Monitor className="pointer-events-none absolute -right-6 top-2 h-44 w-44 text-[var(--color-ink)] opacity-[0.04] transition-transform duration-300 group-hover:-translate-x-1 group-hover:translate-y-1 motion-reduce:transform-none motion-reduce:transition-none" aria-hidden="true" />
          <div className="relative z-10">
            <h2 id="system-environment-title" className="flex items-center gap-2.5 text-base font-semibold text-[var(--color-ink-strong)]">
              <span className="h-5 w-1 rounded-full bg-[var(--color-accent)]" aria-hidden="true" />
              系统环境信息 (System Environment)
            </h2>
            <SystemInfoPanel />
          </div>
        </section>

        {/* 等待娱乐设置：跳转外部平台 + 经典小游戏，独立存储 */}
        <EntertainmentSettingsSection />
      </div>

      {toast && (
        <Toast
          type={toast.type}
          message={toast.message}
          onClose={() => setToast(null)}
        />
      )}
    </FadeInWrapper>
  );
}
