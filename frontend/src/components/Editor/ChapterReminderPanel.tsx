import { useState } from "react";
import {
  AlertTriangle,
  BellRing,
  Check,
  Clock3,
  Loader2,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";

import { GlassButton } from "@/components/UI/GlassButton";
import type {
  AIEditPermissionMode,
  ChapterAdvisoryCheckResponse,
  ChapterAdvisoryFinding,
  ChapterDiagnosticRecord,
  ChapterSettlementStatus,
  WritingAssistanceDimension,
  WritingAssistanceSettings,
} from "@/types";

const DIMENSION_LABELS: Record<WritingAssistanceDimension, string> = {
  fact: "事实",
  character: "人物",
  timeline: "时间线",
  world_rule: "世界规则",
  pov_knowledge: "角色认知",
  foreshadowing: "伏笔",
  outline_deviation: "大纲偏离",
  pacing: "节奏",
  clarity: "清晰度",
  character_expression: "人物表达",
  dialogue: "对话",
  hook: "钩子",
  prose_style: "文风",
};

const DIMENSIONS = Object.keys(DIMENSION_LABELS) as WritingAssistanceDimension[];

const DIAGNOSTIC_SEVERITY_LABELS: Record<string, string> = {
  high: "重要冲突",
  medium: "需要关注",
  low: "一般建议",
};

const DIAGNOSTIC_STATUS_LABELS: Record<string, string> = {
  open: "待处理",
  pending_recheck: "正在复检",
  degraded: "诊断未完整完成",
};

const SEVERITY_STYLES: Record<ChapterAdvisoryFinding["severity"], string> = {
  high: "border-amber-400/50 bg-[var(--color-highlight-soft)] text-[var(--color-ink-strong)]",
  medium: "border-[var(--border-emphasis)] bg-[var(--surface-raised)] text-[var(--color-ink-strong)]",
  low: "border-[var(--border-subtle)] bg-[var(--surface-raised)] text-[var(--color-ink)]",
};

interface ChapterReminderPanelProps {
  settings: WritingAssistanceSettings | null;
  result: ChapterAdvisoryCheckResponse | null;
  checkedContent: string | null;
  currentContent: string;
  loading: boolean;
  checking: boolean;
  error: string | null;
  diagnostics: ChapterDiagnosticRecord[];
  settlement: ChapterSettlementStatus | null;
  onSettingsChange: (settings: WritingAssistanceSettings) => Promise<void>;
  onCheck: () => void;
  onDiagnosticDecision: (
    diagnosticId: string,
    action: "mark_fixed" | "ignore" | "ignore_and_amend_outline",
  ) => Promise<void>;
  onRetrySettlement: () => void;
}

function Toggle({
  checked,
  disabled,
  label,
  onChange,
}: {
  checked: boolean;
  disabled?: boolean;
  label: string;
  onChange: (checked: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative h-6 w-11 shrink-0 rounded-full border transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
        checked
          ? "border-[var(--color-accent)] bg-[var(--color-accent)]"
          : "border-[var(--border-emphasis)] bg-[var(--surface-tool)]"
      }`}
    >
      <span
        className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-transform ${
          checked ? "translate-x-5" : "translate-x-1"
        }`}
      />
    </button>
  );
}

export default function ChapterReminderPanel({
  settings,
  result,
  checkedContent,
  currentContent,
  loading,
  checking,
  error,
  diagnostics,
  settlement,
  onSettingsChange,
  onCheck,
  onDiagnosticDecision,
  onRetrySettlement,
}: ChapterReminderPanelProps) {
  const [savingSettings, setSavingSettings] = useState(false);
  const [settingsError, setSettingsError] = useState<string | null>(null);
  const stale = checkedContent !== null && checkedContent !== currentContent;

  const commitSettings = async (next: WritingAssistanceSettings) => {
    setSavingSettings(true);
    setSettingsError(null);
    try {
      await onSettingsChange(next);
    } catch (cause) {
      setSettingsError((cause as Error).message || "设置保存失败");
    } finally {
      setSavingSettings(false);
    }
  };

  const patchReminders = (
    patch: Partial<WritingAssistanceSettings["consistency_reminders"]>
  ) => {
    if (!settings) return;
    void commitSettings({
      ...settings,
      consistency_reminders: {
        ...settings.consistency_reminders,
        ...patch,
      },
    });
  };

  const setEditMode = (mode: AIEditPermissionMode) => {
    if (!settings) return;
    void commitSettings({
      ...settings,
      ai_edit_permission: { mode },
    });
  };

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-pine-700">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        正在读取提醒设置
      </div>
    );
  }

  if (!settings) {
    return (
      <div className="p-5 text-sm text-crimson-500">
        {error || "提醒设置暂时不可用。正文编辑和保存不受影响。"}
      </div>
    );
  }

  const reminders = settings.consistency_reminders;

  return (
    <div className="h-full overflow-y-auto p-4 text-[var(--color-ink)]">
      {settlement && settlement.status !== "completed" && settlement.status !== "superseded" && (
        <section className="mb-4 rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-4 shadow-[var(--shadow-raised)]">
          <div className="flex items-center justify-between gap-3 text-xs text-[var(--color-ink)]">
            <div className="flex min-w-0 items-center gap-2">
              {settlement.status === "pending" || settlement.status === "running" ? (
                <Loader2 className="h-4 w-4 shrink-0 animate-spin text-[var(--color-accent)]" />
              ) : (
                <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" />
              )}
              <span>
                {settlement.status === "pending" || settlement.status === "running"
                  ? `正文已保存，正在同步世界状态（${settlement.phase}）`
                  : "正文已保存，附属状态同步需要重试"}
              </span>
            </div>
            {(settlement.status === "retryable_failed" || settlement.status === "degraded") && (
              <button
                type="button"
                onClick={onRetrySettlement}
                className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-[var(--border-emphasis)] px-2 py-1 font-medium text-[var(--color-accent)]"
              >
                <RefreshCw className="h-3.5 w-3.5" />重新同步
              </button>
            )}
          </div>
          {settlement.error && (
            <p className="mt-2 break-words text-xs text-[var(--color-danger)]">{settlement.error}</p>
          )}
        </section>
      )}
      <section className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-4 shadow-[var(--shadow-raised)]">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 text-sm font-semibold text-[var(--color-ink-strong)]">
              <BellRing className="h-4 w-4 text-[var(--color-accent)]" />
              一致性提醒
            </div>
            <p className="mt-1 text-xs leading-5 text-[var(--color-ink-muted)]">
              只在明确的检查动作后出现，不根据停顿或离开键盘触发。
            </p>
          </div>
          <Toggle
            label="开启一致性提醒"
            checked={reminders.enabled}
            disabled={savingSettings}
            onChange={(enabled) => patchReminders({ enabled })}
          />
        </div>

        <div className="mt-4 flex items-center justify-between gap-3 rounded-xl bg-[var(--surface-tool)] px-3 py-2.5">
          <div>
            <div className="text-xs font-medium text-[var(--color-ink-strong)]">保存后检查</div>
            <div className="mt-0.5 text-[11px] leading-4 text-[var(--color-ink-muted)]">
              保存完成后在后台运行，不延长或阻断保存。
            </div>
          </div>
          <Toggle
            label="保存后检查"
            checked={reminders.check_on_save}
            disabled={!reminders.enabled || savingSettings}
            onChange={(check_on_save) => patchReminders({ check_on_save })}
          />
        </div>

        <div className="mt-4">
          <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
            检查维度
          </div>
          <div className="flex flex-wrap gap-2">
            {DIMENSIONS.map((dimension) => {
              const selected = reminders.dimensions.includes(dimension);
              return (
                <button
                  key={dimension}
                  type="button"
                  disabled={!reminders.enabled || savingSettings}
                  onClick={() => {
                    const dimensions = selected
                      ? reminders.dimensions.filter((item) => item !== dimension)
                      : [...reminders.dimensions, dimension];
                    patchReminders({ dimensions });
                  }}
                  className={`rounded-full border px-2.5 py-1 text-[11px] transition-colors disabled:cursor-not-allowed disabled:opacity-45 ${
                    selected
                      ? "border-[var(--border-emphasis)] bg-[var(--color-accent-soft)] text-[var(--color-ink-strong)]"
                      : "border-[var(--border-subtle)] bg-[var(--surface-tool)] text-[var(--color-ink-muted)]"
                  }`}
                >
                  {selected && <Check className="mr-1 inline h-3 w-3" />}
                  {DIMENSION_LABELS[dimension]}
                </button>
              );
            })}
          </div>
        </div>

        {(settingsError || error) && (
          <p className="mt-3 text-xs leading-5 text-crimson-500">
            {settingsError || error}
          </p>
        )}

        <GlassButton
          variant="primary"
          className="mt-4 w-full"
          disabled={
            !reminders.enabled ||
            reminders.dimensions.length === 0 ||
            checking ||
            savingSettings
          }
          onClick={onCheck}
        >
          {checking ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <RefreshCw className="h-4 w-4" />
          )}
          {checking ? "正在后台检查…" : "检查本章"}
        </GlassButton>
        <p className="mt-2 text-center text-[11px] text-[var(--color-ink-muted)]">
          提醒始终可忽略，也不会进入 FBI 修订或提交闸门。
        </p>
      </section>

      <section className="mt-4 rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-4 shadow-[var(--shadow-raised)]">
        <div className="flex items-center gap-2 text-sm font-semibold text-[var(--color-ink-strong)]">
          <ShieldCheck className="h-4 w-4 text-[var(--color-accent)]" />
          AI 正文权限
        </div>
        <p className="mt-1 text-xs leading-5 text-[var(--color-ink-muted)]">
          该选项独立于提醒开关。它只规定交互方式，不等于已经授权一次具体改写。
        </p>
        <select
          value={settings.ai_edit_permission.mode}
          disabled={savingSettings}
          onChange={(event) => setEditMode(event.target.value as AIEditPermissionMode)}
          className="mt-3 w-full rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-tool)] px-3 py-2 text-xs text-[var(--color-ink-strong)] outline-none focus:border-[var(--color-accent)]"
        >
          <option value="proposal_only">只给修改方案</option>
          <option value="ask_every_time">每次应用前确认</option>
          <option value="chapter_session">本章授权会话（开启时仍需确认）</option>
        </select>
      </section>

      <section className="mt-4 rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-4 shadow-[var(--shadow-raised)]">
        <div className="flex items-center justify-between px-1">
          <div className="text-xs font-semibold text-[var(--color-ink-strong)]">保存后诊断表</div>
          <span className="text-[11px] text-[var(--color-ink-muted)]">
            {diagnostics.length} 条待处理
          </span>
        </div>
        <p className="mt-1 px-1 text-[11px] leading-5 text-[var(--color-ink-muted)]">
          这些问题不会阻止正文保存；由你决定是否已经修复、忽略，或确认这是对大纲的有意修改。
        </p>
        <div className="mt-3 space-y-3">
          {diagnostics.map((diagnostic) => {
            const canAmendOutline =
              diagnostic.category === "outline_deviation" && diagnostic.severity === "high";
            const amendmentProposal = diagnostic.decision_payload?.proposed_outline_amendment as
              | { preview?: Array<{ field: string; before?: unknown; after?: unknown }> }
              | undefined;
            const busy = diagnostic.status === "pending_recheck";
            return (
              <article
                key={diagnostic.diagnostic_id}
                className={`rounded-xl border p-3 ${
                  diagnostic.severity === "high"
                    ? "border-amber-400/50 bg-[var(--color-highlight-soft)]"
                    : "border-[var(--border-subtle)] bg-[var(--surface-tool)]"
                }`}
              >
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="text-xs font-semibold text-[var(--color-ink-strong)]">
                      {diagnostic.title}
                    </div>
                    <div className="mt-1 text-[10px] text-[var(--color-ink-muted)]">
                      {DIMENSION_LABELS[diagnostic.category as WritingAssistanceDimension] || diagnostic.category}
                      {" · "}
                      {DIAGNOSTIC_SEVERITY_LABELS[diagnostic.severity] || diagnostic.severity}
                      {" · "}
                      {DIAGNOSTIC_STATUS_LABELS[diagnostic.status] || diagnostic.status}
                    </div>
                  </div>
                  <span className="text-[10px] text-[var(--color-ink-muted)]">
                    {Math.round((diagnostic.confidence || 0) * 100)}%
                  </span>
                </div>
                <p className="mt-2 text-xs leading-5 text-[var(--color-ink)]">{diagnostic.detail}</p>
                {diagnostic.evidence_quote && (
                  <blockquote className="mt-2 rounded-lg border-l-2 border-current/30 bg-[var(--surface-tool)] px-3 py-2 text-[11px] leading-5">
                    “{diagnostic.evidence_quote}”
                  </blockquote>
                )}
                {canAmendOutline && amendmentProposal?.preview && amendmentProposal.preview.length > 0 && (
                  <details className="mt-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-tool)] px-3 py-2 text-[11px]">
                    <summary className="cursor-pointer font-medium text-[var(--color-accent)]">
                      查看拟修改的大纲字段（{amendmentProposal.preview.length} 处）
                    </summary>
                    <div className="mt-2 space-y-2">
                      {amendmentProposal.preview.map((item, index) => (
                        <div key={`${item.field}-${index}`} className="break-words">
                          <strong>{item.field}</strong>
                          <div className="mt-1 text-[var(--color-ink-muted)]">
                            {JSON.stringify(item.before ?? null)} → {JSON.stringify(item.after ?? null)}
                          </div>
                        </div>
                      ))}
                    </div>
                  </details>
                )}
                <div className="mt-3 flex flex-wrap gap-2">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void onDiagnosticDecision(diagnostic.diagnostic_id, "mark_fixed")}
                    className="rounded-lg border border-pine-700/20 bg-pine-700 px-2.5 py-1.5 text-[11px] font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    已修复
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void onDiagnosticDecision(diagnostic.diagnostic_id, "ignore")}
                    className="rounded-lg border border-current/20 bg-[var(--surface-tool)] px-2.5 py-1.5 text-[11px] font-medium disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    忽略
                  </button>
                  {canAmendOutline && (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void onDiagnosticDecision(diagnostic.diagnostic_id, "ignore_and_amend_outline")}
                      className="rounded-lg border border-amber-500/40 bg-amber-100 px-2.5 py-1.5 text-[11px] font-medium text-amber-900 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      忽略并修改大纲
                    </button>
                  )}
                </div>
              </article>
            );
          })}
          {diagnostics.length === 0 && (
            <div className="rounded-xl border border-dashed border-[var(--border-subtle)] bg-[var(--surface-tool)] p-4 text-center text-xs text-[var(--color-ink-muted)]">
              保存后没有待处理诊断。
            </div>
          )}
        </div>
      </section>

      <section className="mt-4 space-y-3 pb-4">
        <div className="flex items-center justify-between px-1">
          <div className="text-xs font-semibold text-[var(--color-ink-strong)]">本次检查</div>
          {result && (
            <span className="text-[11px] text-[var(--color-ink-muted)]">
              {result.findings.length} 条提醒
            </span>
          )}
        </div>

        {checking && (
          <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-tool)] p-3 text-xs leading-5 text-[var(--color-ink)]">
            <Clock3 className="mr-1.5 inline h-3.5 w-3.5" />
            检查在后台进行。你可以继续写作、切换面板或保存。
          </div>
        )}

        {stale && result && (
          <div className="rounded-xl border border-amber-300/60 bg-amber-50/70 p-3 text-xs leading-5 text-amber-800">
            正文已在检查后发生变化，以下提醒属于旧版本。可重新检查刷新结果。
          </div>
        )}

        {result?.diagnostics.map((diagnostic) => (
          <div
            key={`${diagnostic.code}-${diagnostic.message}`}
            className={`rounded-xl border p-3 text-xs leading-5 ${
              diagnostic.level === "warning"
                ? "border-amber-300/60 bg-amber-50/70 text-amber-800"
                : "border-[var(--border-subtle)] bg-[var(--surface-tool)] text-[var(--color-ink)]"
            }`}
          >
            {diagnostic.level === "warning" && (
              <AlertTriangle className="mr-1.5 inline h-3.5 w-3.5" />
            )}
            {diagnostic.message}
          </div>
        ))}

        {!checking && !result && (
          <div className="rounded-xl border border-dashed border-[var(--border-subtle)] bg-[var(--surface-tool)] p-5 text-center text-xs leading-5 text-[var(--color-ink-muted)]">
            {reminders.enabled
              ? "需要时点击“检查本章”。停笔、思考或离开不会触发检查。"
              : "开启后可手动检查，也可选择仅在保存完成后检查。"}
          </div>
        )}

        {!checking && result && result.findings.length === 0 && (
          <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-tool)] p-4 text-center text-xs leading-5 text-[var(--color-ink)]">
            本次没有发现有明确正文证据的一致性问题。
          </div>
        )}

        {result?.findings.map((finding) => (
          <article
            key={finding.finding_id}
            className={`rounded-2xl border p-3.5 shadow-sm transition-opacity ${
              SEVERITY_STYLES[finding.severity]
            } ${stale ? "opacity-60" : ""}`}
          >
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="text-xs font-semibold">{finding.title}</div>
                <div className="mt-1 text-[10px] opacity-70">
                  {DIMENSION_LABELS[finding.category]} · 置信度 {Math.round(finding.confidence * 100)}%
                </div>
              </div>
              <span className="rounded-full border border-current/20 bg-[var(--surface-tool)] px-2 py-0.5 text-[10px]">
                非阻断
              </span>
            </div>
            <p className="mt-2 text-xs leading-5">{finding.detail}</p>
            <blockquote className="mt-3 rounded-lg border-l-2 border-current/30 bg-[var(--surface-tool)] px-3 py-2 text-[11px] leading-5">
              “{finding.evidence_quote}”
            </blockquote>
            <div className="mt-2 rounded-lg bg-[var(--surface-tool)] px-3 py-2 text-[11px] leading-5">
              <span className="font-medium">参照 · {finding.source.label}</span>
              <div className="mt-0.5 opacity-80">{finding.source.excerpt}</div>
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                disabled={stale || !finding.diagnostic_id}
                onClick={() => finding.diagnostic_id && void onDiagnosticDecision(finding.diagnostic_id, "mark_fixed")}
                className="rounded-lg border border-pine-700/20 bg-pine-700 px-2.5 py-1.5 text-[11px] font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
              >
                已修复
              </button>
              <button
                type="button"
                disabled={stale || !finding.diagnostic_id}
                onClick={() => finding.diagnostic_id && void onDiagnosticDecision(finding.diagnostic_id, "ignore")}
                className="rounded-lg border border-current/20 bg-[var(--surface-tool)] px-2.5 py-1.5 text-[11px] font-medium disabled:cursor-not-allowed disabled:opacity-50"
              >
                忽略
              </button>
              {finding.category === "outline_deviation" && finding.severity === "high" && (
                <button
                  type="button"
                  disabled={stale || !finding.diagnostic_id}
                  onClick={() => finding.diagnostic_id && void onDiagnosticDecision(finding.diagnostic_id, "ignore_and_amend_outline")}
                  className="rounded-lg border border-amber-500/40 bg-amber-100 px-2.5 py-1.5 text-[11px] font-medium text-amber-900 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  忽略并修改大纲
                </button>
              )}
            </div>
          </article>
        ))}
      </section>
    </div>
  );
}
