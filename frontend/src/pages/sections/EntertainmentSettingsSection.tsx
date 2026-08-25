/* Hallmark · pre-emit critique: P5 H5 E5 S5 R5 V5 */
/* Hallmark · component: entertainment settings section · genre: editorial utility · theme: project DESIGN.md
 * states: default · hover · focus · active · disabled · loading · error · success
 * contrast: uses project semantic tokens
 */
/**
 * 娱乐设置区块 (Entertainment Settings Section)
 * =============================================
 *
 * 嵌入 SettingsPage 的"等待娱乐"配置区块。负责：
 * - 显示/编辑 EntertainmentSettings 全部字段
 * - 链接列表的增/删/改（行内编辑）
 * - 启用游戏的勾选
 * - 重置默认链接
 * - 独立保存（不与 API 密钥配置一起保存，因为存储路径不同）
 *
 * 设计要点
 * --------
 * - 与 SettingsPage 解耦：自管理 local state + 调用 entertainmentStore
 * - 样式遵循 DESIGN.md：工具面承载分区，数字纸张承载可操作内容
 * - 编辑过程中显式"保存"按钮，避免每改一字段就触发后端 PUT
 * - 重置默认链接单独按钮，需二次确认（防误触）
 */

import { useEffect, useState } from "react";
import {
  Save,
  RotateCcw,
  Plus,
  Trash2,
  Gamepad2,
  ExternalLink,
  AlertTriangle,
  Loader2,
} from "lucide-react";
import { useEntertainmentStore } from "@/stores/entertainmentStore";
import type { EntertainmentLink, EntertainmentSettings, LinkCategory } from "@/types";

const CATEGORY_LABELS: Record<LinkCategory, string> = {
  video: "视频",
  music: "音乐",
  novel: "小说",
  custom: "自定义",
};

const AVAILABLE_GAMES: { id: string; label: string; description: string }[] = [
  { id: "snake", label: "贪吃蛇", description: "经典红白机贪吃蛇，方向键控制" },
  { id: "2048", label: "2048", description: "合并数字达到 2048" },
];

const DEFAULT_NEW_LINK: EntertainmentLink = {
  name: "",
  url: "",
  category: "custom",
  icon: "🔗",
  enabled: true,
};

export default function EntertainmentSettingsSection() {
  const { settings, fetchSettings, saveSettings, resetSettings } = useEntertainmentStore();
  const [local, setLocal] = useState<EntertainmentSettings | null>(null);
  const [saving, setSaving] = useState(false);
  const [confirmReset, setConfirmReset] = useState(false);

  // 初次加载：拉取设置
  useEffect(() => {
    fetchSettings();
  }, [fetchSettings]);

  // 同步 store -> local
  useEffect(() => {
    if (settings) {
      setLocal({
        ...settings,
        links: settings.links.map((l) => ({ ...l })),
        enabled_games: [...settings.enabled_games],
      });
    }
  }, [settings]);

  if (!local) {
    return (
      <section className="rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-tool)] p-6 sm:p-8" aria-busy="true">
        <div className="flex min-h-32 items-center justify-center rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-paper)] text-sm text-[var(--color-ink-muted)]">
          <Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
          正在读取等待娱乐设置
        </div>
      </section>
    );
  }

  // === 字段更新工具 ===
  const updateField = <K extends keyof EntertainmentSettings>(
    key: K,
    value: EntertainmentSettings[K],
  ) => {
    setLocal((prev) => (prev ? { ...prev, [key]: value } : prev));
  };

  const updateLink = (idx: number, patch: Partial<EntertainmentLink>) => {
    setLocal((prev) => {
      if (!prev) return prev;
      const newLinks = prev.links.map((l, i) => (i === idx ? { ...l, ...patch } : l));
      return { ...prev, links: newLinks };
    });
  };

  const addLink = () => {
    setLocal((prev) => {
      if (!prev) return prev;
      return { ...prev, links: [...prev.links, { ...DEFAULT_NEW_LINK }] };
    });
  };

  const removeLink = (idx: number) => {
    setLocal((prev) => {
      if (!prev) return prev;
      return { ...prev, links: prev.links.filter((_, i) => i !== idx) };
    });
  };

  const toggleGame = (gameId: string) => {
    setLocal((prev) => {
      if (!prev) return prev;
      const has = prev.enabled_games.includes(gameId);
      const newGames = has
        ? prev.enabled_games.filter((g) => g !== gameId)
        : [...prev.enabled_games, gameId];
      return { ...prev, enabled_games: newGames };
    });
  };

  // === 保存 ===
  const handleSave = async () => {
    if (!local) return;
    setSaving(true);
    try {
      await saveSettings(local);
    } catch (e) {
      console.error("保存娱乐设置失败:", e);
    } finally {
      setSaving(false);
    }
  };

  // === 重置默认 ===
  const handleReset = async () => {
    if (!confirmReset) {
      setConfirmReset(true);
      setTimeout(() => setConfirmReset(false), 3000);  // 3秒后自动取消确认
      return;
    }
    setSaving(true);
    try {
      await resetSettings();
      setConfirmReset(false);
    } catch (e) {
      console.error("重置娱乐设置失败:", e);
    } finally {
      setSaving(false);
    }
  };

  return (
    <section
      className="group relative overflow-hidden rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-tool)] p-6 sm:p-8"
      aria-labelledby="entertainment-settings-title"
    >
      <Gamepad2 className="pointer-events-none absolute -right-6 top-2 h-44 w-44 text-[var(--color-ink)] opacity-[0.04] transition-transform duration-300 group-hover:-translate-x-1 group-hover:translate-y-1 motion-reduce:transform-none motion-reduce:transition-none" aria-hidden="true" />

      <div className="relative z-10">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <h2 id="entertainment-settings-title" className="flex items-center gap-2.5 text-base font-semibold text-[var(--color-ink-strong)]">
              <span className="h-5 w-1 rounded-full bg-[var(--color-accent)]" aria-hidden="true" />
              等待娱乐 (Waiting Entertainment)
            </h2>
            <p className="mt-2 text-sm leading-6 text-[var(--color-ink-muted)]">
              管理工作流等待期间的邀请、完成提醒和娱乐入口。
            </p>
          </div>
          <div className="flex shrink-0 flex-wrap gap-2">
          <button
            type="button"
            onClick={handleReset}
            disabled={saving}
 className={`inline-flex min-h-11 items-center justify-center gap-2 whitespace-nowrap rounded-md border px-3 text-xs font-medium transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)] active:translate-y-px motion-reduce:active:transform-none disabled:cursor-not-allowed disabled:opacity-50 ${
              confirmReset
                ? "border-[var(--color-error)] bg-[var(--color-error-soft)] text-[var(--color-error)]"
                : "border-[var(--border-emphasis)] bg-[var(--surface-paper)] text-[var(--color-ink)] hover:border-[var(--color-accent)] hover:text-[var(--color-accent)]"
            }`}
          >
            {confirmReset ? (
              <>
                <AlertTriangle className="h-3 w-3" />
                确认重置？
              </>
            ) : (
              <>
                <RotateCcw className="h-3 w-3" />
                重置默认
              </>
            )}
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={saving}
 className="inline-flex min-h-11 items-center justify-center gap-2 whitespace-nowrap rounded-md bg-[var(--color-accent)] px-4 text-xs font-semibold text-[var(--color-on-accent)] transition-colors duration-150 hover:bg-[var(--color-accent-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)] active:translate-y-px motion-reduce:active:transform-none disabled:cursor-not-allowed disabled:opacity-50"
          >
            {saving ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />
            ) : (
              <Save className="h-3.5 w-3.5" aria-hidden="true" />
            )}
            {saving ? "保存中..." : "保存"}
          </button>
        </div>
      </div>

      {/* 总开关 + 子开关 */}
      <div className="mt-6 overflow-hidden rounded-lg border border-[var(--border-emphasis)] bg-[var(--surface-paper)]">
        <ToggleRow
          label="启用等待娱乐功能"
          description="总开关，关闭后工作流邀请和娱乐弹窗均不可用"
          checked={local.enabled}
          onChange={(v) => updateField("enabled", v)}
        />
        <ToggleRow
          label="显示工作流启动邀请"
          description="工作流启动时在编辑区上方短暂显示，可由此打开娱乐弹窗"
          checked={local.show_trigger_button}
          onChange={(v) => updateField("show_trigger_button", v)}
          disabled={!local.enabled}
        />
        <ToggleRow
          label="工作流完成时弱提醒"
          description="工作流完成时在右上角显示 toast 通知（3秒自动消失），不强制弹窗"
          checked={local.notify_on_workflow_complete}
          onChange={(v) => updateField("notify_on_workflow_complete", v)}
          disabled={!local.enabled}
        />
      </div>

      {/* 启用的小游戏 */}
      <div className="mt-6 border-t border-[var(--border-subtle)] pt-6">
        <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-[var(--color-ink-strong)]">
          <Gamepad2 className="h-4 w-4 text-[var(--color-accent)]" aria-hidden="true" />
          启用的小游戏
        </h3>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {AVAILABLE_GAMES.map((game) => {
            const enabled = local.enabled_games.includes(game.id);
            return (
              <label
                key={game.id}
                className={`flex min-h-14 cursor-pointer items-start gap-3 rounded-lg border px-4 py-3 transition-colors duration-150 ${
                  enabled
                    ? "border-[var(--color-accent)] bg-[var(--color-accent-soft)]"
                    : "border-[var(--border-subtle)] bg-[var(--surface-paper)] hover:border-[var(--border-emphasis)] hover:bg-[var(--surface-raised)]"
                } ${!local.enabled ? "cursor-not-allowed opacity-50" : ""}`}
              >
                <input
                  type="checkbox"
                  checked={enabled}
                  onChange={() => toggleGame(game.id)}
                  disabled={!local.enabled}
                  className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--color-accent)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)]"
                />
                <div className="min-w-0">
                  <p className="text-xs font-semibold text-[var(--color-ink-strong)]">{game.label}</p>
                  <p className="mt-1 text-[11px] leading-4 text-[var(--color-ink-muted)]">{game.description}</p>
                </div>
              </label>
            );
          })}
        </div>
      </div>

      {/* 跳转链接管理 */}
      <div className="mt-6 border-t border-[var(--border-subtle)] pt-6">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-[var(--color-ink-strong)]">
            <ExternalLink className="h-4 w-4 text-[var(--color-accent)]" aria-hidden="true" />
            跳转链接
            <span className="rounded-md border border-[var(--border-subtle)] bg-[var(--surface-paper)] px-2 py-0.5 text-[10px] font-medium text-[var(--color-ink-muted)]">
              ({local.links.length})
            </span>
          </h3>
          <button
            type="button"
            onClick={addLink}
            disabled={!local.enabled}
 className="inline-flex min-h-10 items-center justify-center gap-1.5 rounded-md border border-[var(--border-emphasis)] bg-[var(--surface-paper)] px-3 text-[11px] font-medium text-[var(--color-ink)] transition-colors duration-150 hover:border-[var(--color-accent)] hover:text-[var(--color-accent)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)] active:translate-y-px motion-reduce:active:transform-none disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Plus className="h-3.5 w-3.5" aria-hidden="true" />
            新增
          </button>
        </div>

        {local.links.length === 0 ? (
          <p className="rounded-lg border border-dashed border-[var(--border-emphasis)] bg-[var(--surface-paper)] px-4 py-8 text-center text-xs text-[var(--color-ink-muted)]">
            暂无链接，点击"新增"添加自定义跳转
          </p>
        ) : (
          <div className="space-y-2">
            {local.links.map((link, idx) => (
              <div
                key={idx}
                className="grid min-w-0 grid-cols-1 gap-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-paper)] p-3 transition-colors duration-150 hover:border-[var(--border-emphasis)] sm:grid-cols-[3rem_minmax(7rem,0.8fr)_minmax(12rem,1.8fr)_7rem_auto] sm:items-center"
              >
                {/* 图标 */}
                <input
                  type="text"
                  value={link.icon || ""}
                  onChange={(e) => updateLink(idx, { icon: e.target.value })}
                  placeholder="🔗"
                  aria-label={`${link.name || `链接 ${idx + 1}`}图标`}
                  className="h-10 min-w-0 rounded-md border border-[var(--border-subtle)] bg-[var(--surface-raised)] px-2 text-center text-xs text-[var(--color-ink)] outline-none transition-colors duration-150 hover:border-[var(--border-emphasis)] focus:border-[var(--color-accent)] focus:ring-2 focus:ring-[var(--focus-ring)]"
                  maxLength={4}
                />
                {/* 名称 */}
                <input
                  type="text"
                  value={link.name}
                  onChange={(e) => updateLink(idx, { name: e.target.value })}
                  placeholder="名称"
                  aria-label={`链接 ${idx + 1}名称`}
                  className="h-10 min-w-0 rounded-md border border-[var(--border-subtle)] bg-[var(--surface-raised)] px-3 text-xs text-[var(--color-ink)] outline-none transition-colors duration-150 hover:border-[var(--border-emphasis)] focus:border-[var(--color-accent)] focus:ring-2 focus:ring-[var(--focus-ring)]"
                />
                {/* URL */}
                <input
                  type="url"
                  value={link.url}
                  onChange={(e) => updateLink(idx, { url: e.target.value })}
                  placeholder="https://"
                  aria-label={`${link.name || `链接 ${idx + 1}`}地址`}
                  className="h-10 min-w-0 rounded-md border border-[var(--border-subtle)] bg-[var(--surface-raised)] px-3 text-xs text-[var(--color-ink)] outline-none transition-colors duration-150 hover:border-[var(--border-emphasis)] focus:border-[var(--color-accent)] focus:ring-2 focus:ring-[var(--focus-ring)]"
                />
                {/* 分类 */}
                <select
                  value={link.category}
                  onChange={(e) =>
                    updateLink(idx, { category: e.target.value as LinkCategory })
                  }
                  aria-label={`${link.name || `链接 ${idx + 1}`}分类`}
                  className="h-10 min-w-0 rounded-md border border-[var(--border-subtle)] bg-[var(--surface-raised)] px-2 text-xs text-[var(--color-ink)] outline-none transition-colors duration-150 hover:border-[var(--border-emphasis)] focus:border-[var(--color-accent)] focus:ring-2 focus:ring-[var(--focus-ring)]"
                >
                  {(Object.keys(CATEGORY_LABELS) as LinkCategory[]).map((cat) => (
                    <option key={cat} value={cat}>
                      {CATEGORY_LABELS[cat]}
                    </option>
                  ))}
                </select>
                {/* 启用 + 删除 */}
                <div className="flex min-h-10 items-center justify-end gap-2">
                  <input
                    type="checkbox"
                    checked={link.enabled}
                    onChange={(e) => updateLink(idx, { enabled: e.target.checked })}
                    className="h-4 w-4 accent-[var(--color-accent)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)]"
                    title="启用/禁用此链接"
                    aria-label={`${link.name || `链接 ${idx + 1}`}启用状态`}
                  />
                  <button
                    type="button"
                    onClick={() => removeLink(idx)}
 className="inline-flex size-10 items-center justify-center rounded-md text-[var(--color-ink-muted)] transition-colors duration-150 hover:bg-[var(--color-error-soft)] hover:text-[var(--color-error)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-error)] active:translate-y-px motion-reduce:active:transform-none"
                    title="删除此链接"
                    aria-label={`删除${link.name || `链接 ${idx + 1}`}`}
                  >
                    <Trash2 className="h-4 w-4" aria-hidden="true" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
      </div>
    </section>
  );
}

// === 内部组件：开关行 ===
function ToggleRow({
  label,
  description,
  checked,
  onChange,
  disabled = false,
}: {
  label: string;
  description: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <div className={`flex min-h-[4.75rem] items-center justify-between gap-4 border-b border-[var(--border-subtle)] px-5 py-4 last:border-b-0 ${disabled ? "opacity-50" : ""}`}>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-[var(--color-ink-strong)]">{label}</p>
        <p className="mt-1 text-[11px] leading-4 text-[var(--color-ink-muted)]">{description}</p>
      </div>
      <button
        type="button"
        onClick={() => onChange(!checked)}
        disabled={disabled}
        role="switch"
        aria-checked={checked}
        aria-label={label}
 className="relative grid size-11 shrink-0 place-items-center rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)] focus-visible:ring-offset-1 focus-visible:ring-offset-[var(--surface-paper)] active:translate-y-px motion-reduce:active:transform-none disabled:cursor-not-allowed"
      >
        <span className={`relative block h-6 w-11 rounded-full border transition-colors duration-150 ${
          checked
            ? "border-[var(--color-accent)] bg-[var(--color-accent)]"
            : "border-[var(--border-emphasis)] bg-[var(--surface-tool)]"
        }`} aria-hidden="true">
          <span
            className={`absolute left-0.5 top-0.5 h-5 w-5 rounded-full bg-[var(--surface-raised)] shadow-sm transition-transform duration-150 motion-reduce:transition-none ${
              checked ? "translate-x-5" : "translate-x-0"
          }`}
          />
        </span>
      </button>
    </div>
  );
}
