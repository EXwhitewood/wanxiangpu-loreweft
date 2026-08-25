import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  CircleDot,
  Clock3,
  Database,
  GitBranch,
  ListChecks,
  RefreshCw,
  Route,
  ShieldAlert,
  SlidersHorizontal,
  XCircle,
} from "lucide-react";
import clsx from "clsx";
import * as api from "@/api/client";
import type {
  EntityProgression,
  GenerationFeaturePolicy,
  GenerationTrace,
  ProjectHealth,
} from "@/types";
import { displayValue } from "@/utils/chineseDisplay";

type DiagnosticLevel = "critical" | "warning" | "info" | "ok";

interface DiagnosticItem {
  id: string;
  level: DiagnosticLevel;
  title: string;
  detail: string;
  actionLabel?: string;
  to?: string;
}

const QUALITY_PATTERN_LABELS: Record<string, string> = {
  ai_simile_overuse: "明喻使用偏多",
  abstraction_over_budget_trend: "抽象表达超出预算",
  ai_dialogue_tag_overuse: "对话标签使用偏多",
  ai_false_range: "虚假范围表达",
  ai_negative_parallel: "否定式排比",
  ai_punctuation_artifact: "标点格式痕迹",
};

function summaryValue(health: ProjectHealth | null, key: string): number {
  return Number(health?.summary?.[key] || 0);
}

function diagnosticTone(level: DiagnosticLevel) {
  if (level === "critical") return {
    icon: <XCircle className="h-4 w-4" />,
    label: "需处理",
    className: "border-crimson-500/25 bg-crimson-50 text-crimson-700",
  };
  if (level === "warning") return {
    icon: <AlertTriangle className="h-4 w-4" />,
    label: "需关注",
    className: "border-amber-500/25 bg-amber-50 text-amber-800",
  };
  if (level === "info") return {
    icon: <CircleDot className="h-4 w-4" />,
    label: "提示",
    className: "border-sky-500/20 bg-sky-50 text-sky-800",
  };
  return {
    icon: <CheckCircle2 className="h-4 w-4" />,
    label: "正常",
    className: "border-emerald-500/20 bg-emerald-50 text-emerald-800",
  };
}

function Metric({ icon, label, value, note }: { icon: ReactNode; label: string; value: number; note: string }) {
  return (
    <div className="min-w-0 border-b border-pine-900/10 py-4 last:border-b-0 sm:border-b-0 sm:border-r sm:px-5 sm:first:pl-0 sm:last:border-r-0">
      <div className="flex items-center gap-2 text-xs font-medium text-pine-700">
        {icon}
        {label}
      </div>
      <div className="mt-1 text-2xl font-bold text-pine-950">{value}</div>
      <div className="mt-0.5 text-xs text-pine-700">{note}</div>
    </div>
  );
}

function DiagnosticRow({ item }: { item: DiagnosticItem }) {
  const tone = diagnosticTone(item.level);
  return (
    <div className="grid gap-3 border-b border-pine-900/10 py-4 last:border-b-0 md:grid-cols-[112px_minmax(0,1fr)_auto] md:items-center">
      <div className={clsx("flex w-fit items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold", tone.className)}>
        {tone.icon}
        {tone.label}
      </div>
      <div className="min-w-0">
        <div className="[overflow-wrap:anywhere] text-sm font-semibold text-pine-950">{item.title}</div>
        <div className="mt-1 [overflow-wrap:anywhere] text-xs leading-5 text-pine-700">{item.detail}</div>
      </div>
      {item.to && item.actionLabel && (
        <Link
          to={item.to}
          className="flex w-fit items-center gap-1.5 whitespace-nowrap rounded-lg px-2 py-1.5 text-xs font-semibold text-[var(--workspace-accent)] transition-colors hover:bg-pine-900/[0.04] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-magic-500"
        >
          {item.actionLabel}
          <ArrowRight className="h-3.5 w-3.5" />
        </Link>
      )}
    </div>
  );
}

function featureLabel(value: string | undefined) {
  if (!value) return "未配置";
  return displayValue(value, value);
}

export default function ProjectHealthPage() {
  const { id } = useParams<{ id: string }>();
  const [health, setHealth] = useState<ProjectHealth | null>(null);
  const [traces, setTraces] = useState<GenerationTrace[]>([]);
  const [progressions, setProgressions] = useState<EntityProgression[]>([]);
  const [features, setFeatures] = useState<GenerationFeaturePolicy | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    if (!id) return;
    setLoading(true);
    setError(null);

    const results = await Promise.allSettled([
      api.getProjectHealth(id),
      api.getGenerationTraces(id, 20),
      api.getProgressions(id, 30),
      api.getGenerationFeatures(id),
    ]);

    if (results[0].status === "fulfilled") setHealth(results[0].value);
    if (results[1].status === "fulfilled") setTraces(results[1].value.items);
    if (results[2].status === "fulfilled") setProgressions(results[2].value.items);
    if (results[3].status === "fulfilled") setFeatures(results[3].value.effective);

    const failures = results.filter((result): result is PromiseRejectedResult => result.status === "rejected");
    if (failures.length) {
      const firstMessage = failures[0].reason instanceof Error ? failures[0].reason.message : "部分健康数据读取失败";
      setError(`${failures.length} 项健康数据暂时不可用：${firstMessage}`);
    }
    setLoading(false);
  };

  useEffect(() => {
    void load();
  }, [id]);

  const diagnostics = useMemo<DiagnosticItem[]>(() => {
    if (!health || !id) return [];
    const items: DiagnosticItem[] = [];
    const generationRuns = summaryValue(health, "generation_runs");
    const failedRuns = summaryValue(health, "blocked_or_failed_runs");
    const degradedRuns = summaryValue(health, "degraded_runs");
    const traceCount = summaryValue(health, "trace_count");
    const progressionCount = summaryValue(health, "progression_count");
    const experienceCount = summaryValue(health, "experience_quality_report_count");
    const memoryCount = summaryValue(health, "quality_memory_pattern_count");
    const latestTrace = traces[0];
    const latestViolations = Number(latestTrace?.quality_summary?.violation_count || 0);

    if (failedRuns > 0) {
      items.push({
        id: "failed-runs",
        level: "critical",
        title: `${failedRuns} 次生成被阻断或失败`,
        detail: "这些运行没有形成可直接提交的完整结果，应优先检查最近一次内容审查和失败原因。",
        actionLabel: "进入编辑器",
        to: `/project/${id}/editor`,
      });
    } else if (health.status === "degraded") {
      items.push({
        id: "health-storage",
        level: "critical",
        title: "健康数据读取不完整",
        detail: "系统未能完整读取生成记录、状态变化或体验报告，当前结论可能缺少部分依据。",
        actionLabel: "检查智能体配置",
        to: "/agents",
      });
    }

    if (degradedRuns > 0) {
      items.push({
        id: "degraded-runs",
        level: "warning",
        title: `${degradedRuns} 次生成发生能力降级`,
        detail: "部分质量层、上下文或校验能力未按完整路径执行，建议结合最近生成轨迹确认影响范围。",
        actionLabel: "检查智能体配置",
        to: "/agents",
      });
    }

    if (latestViolations > 0) {
      items.push({
        id: "latest-violations",
        level: "warning",
        title: `最近一次生成记录了 ${latestViolations} 个质量问题`,
        detail: `证据来自第 ${latestTrace.chapter_number} 章、场景 ${latestTrace.scene_index + 1} 的生成轨迹。`,
        actionLabel: "进入编辑器",
        to: `/project/${id}/editor?chapter=${latestTrace.chapter_number}`,
      });
    }

    if (generationRuns > 0 && traceCount === 0) {
      items.push({
        id: "missing-traces",
        level: "warning",
        title: "已有生成记录，但没有可追踪的生成轨迹",
        detail: "缺少轨迹会让失败定位和质量复盘失去证据链。",
        actionLabel: "检查智能体配置",
        to: "/agents",
      });
    }

    if (memoryCount > 0) {
      items.push({
        id: "quality-memory",
        level: "warning",
        title: `质量记忆中有 ${memoryCount} 个重复模式`,
        detail: "这些模式来自多次生成中的重复问题，适合在下一章生成前优先处理。",
        actionLabel: "进入编辑器",
        to: `/project/${id}/editor`,
      });
    }

    if (progressionCount === 0) {
      items.push({
        id: "no-progression",
        level: "info",
        title: "尚未形成状态演进记录",
        detail: "当前世界状态可能仍可查看，但没有可用于复盘变化来源的演进证据。",
        actionLabel: "查看状态总览",
        to: `/project/${id}/state`,
      });
    }

    if (experienceCount === 0 && generationRuns > 0) {
      items.push({
        id: "no-experience-report",
        level: "info",
        title: "尚未保存章节体验质量报告",
        detail: "已有生成记录，但没有可供跨章节比较的体验质量报告。",
      });
    }

    if (!items.some((item) => item.level === "critical" || item.level === "warning")) {
      items.unshift({
        id: "healthy",
        level: "ok",
        title: "当前没有需要优先处理的生成风险",
        detail: "已保存的生成记录、轨迹和质量信号未显示阻断性问题。",
      });
    }

    const priority: Record<DiagnosticLevel, number> = { critical: 0, warning: 1, info: 2, ok: 3 };
    return items.sort((a, b) => priority[a.level] - priority[b.level]);
  }, [health, id, traces]);

  const overallLevel: DiagnosticLevel = diagnostics.some((item) => item.level === "critical")
    ? "critical"
    : diagnostics.some((item) => item.level === "warning")
      ? "warning"
      : "ok";
  const overallTone = diagnosticTone(overallLevel);
  const patterns = health?.quality_memory?.recent_quality_patterns || [];

  return (
    <div className="flex h-full min-h-0 flex-col bg-[var(--workspace-document-surface)] text-pine-950">
      <header className="shrink-0 border-b border-[var(--workspace-border)] bg-[var(--workspace-chrome)] px-5 py-4 lg:px-7">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-xs font-semibold text-pine-700">
              <Activity className="h-4 w-4 text-[var(--workspace-accent)]" />
              项目诊断
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-3">
              <h1 className="[overflow-wrap:anywhere] text-xl font-bold text-pine-950">健康体检</h1>
              {health && (
                <span className={clsx("flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold", overallTone.className)}>
                  {overallTone.icon}
                  {overallLevel === "critical" ? "存在阻断" : overallLevel === "warning" ? "需要关注" : "状态稳定"}
                </span>
              )}
            </div>
          </div>
          <button
            type="button"
            onClick={() => void load()}
            disabled={loading}
            title="刷新诊断"
            aria-label="刷新诊断"
            className="flex h-9 w-9 items-center justify-center rounded-lg border border-pine-900/10 bg-white text-pine-700 transition-colors hover:bg-pine-50 hover:text-pine-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-magic-500 disabled:opacity-50"
          >
            <RefreshCw className={clsx("h-4 w-4", loading && "animate-spin")} />
          </button>
        </div>
      </header>

      <main className="min-h-0 flex-1 overflow-y-auto p-4 lg:p-6">
        {error && (
          <div className="mb-4 border-l-2 border-amber-500 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            {error}
          </div>
        )}

        <section className="rounded-lg border border-[var(--workspace-border)] bg-white px-5">
          <div className="grid sm:grid-cols-4">
            <Metric icon={<Route className="h-4 w-4" />} label="生成运行" value={summaryValue(health, "generation_runs")} note="已保存的运行记录" />
            <Metric icon={<AlertTriangle className="h-4 w-4" />} label="降级运行" value={summaryValue(health, "degraded_runs")} note="能力未完整执行" />
            <Metric icon={<ShieldAlert className="h-4 w-4" />} label="阻断或失败" value={summaryValue(health, "blocked_or_failed_runs")} note="需要优先处理" />
            <Metric icon={<Database className="h-4 w-4" />} label="生成轨迹" value={summaryValue(health, "trace_count")} note="可用于问题定位" />
          </div>
        </section>

        <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
          <section className="rounded-lg border border-[var(--workspace-border)] bg-white px-5 py-4">
            <div className="flex items-center gap-2 border-b border-pine-900/10 pb-3">
              <ListChecks className="h-4 w-4 text-[var(--workspace-accent)]" />
              <h2 className="text-sm font-bold text-pine-950">诊断结论</h2>
              <span className="ml-auto text-xs text-pine-700">{diagnostics.length} 项</span>
            </div>
            <div>
              {diagnostics.map((item) => <DiagnosticRow key={item.id} item={item} />)}
              {!health && !loading && (
                <div className="py-10 text-center text-sm text-pine-700">暂无可用的健康数据</div>
              )}
            </div>
          </section>

          <div className="space-y-4">
            <section className="rounded-lg border border-[var(--workspace-border)] bg-white p-4">
              <div className="flex items-center gap-2 border-b border-pine-900/10 pb-3">
                <SlidersHorizontal className="h-4 w-4 text-[var(--workspace-accent)]" />
                <h2 className="text-sm font-bold text-pine-950">当前检测范围</h2>
              </div>
              <div className="divide-y divide-pine-900/10">
                {[
                  ["生成模式", featureLabel(features?.generation_mode)],
                  ["硬正确性", featureLabel(features?.hard_correctness_mode)],
                  ["叙事体验", featureLabel(features?.narrative_experience_mode)],
                  ["状态演进", featureLabel(features?.progression_mode)],
                  ["轨迹记录", featureLabel(features?.prompt_trace_mode)],
                ].map(([label, value]) => (
                  <div key={label} className="flex items-center justify-between gap-3 py-3 text-xs">
                    <span className="text-pine-700">{label}</span>
                    <span className="font-semibold text-pine-950">{value}</span>
                  </div>
                ))}
              </div>
            </section>

            <section className="rounded-lg border border-[var(--workspace-border)] bg-white p-4">
              <div className="flex items-center gap-2 border-b border-pine-900/10 pb-3">
                <Clock3 className="h-4 w-4 text-[var(--workspace-accent)]" />
                <h2 className="text-sm font-bold text-pine-950">最近生成轨迹</h2>
              </div>
              <div className="divide-y divide-pine-900/10">
                {traces.slice(0, 5).map((trace) => (
                  <div key={trace.id} className="py-3">
                    <div className="flex items-center justify-between gap-3 text-xs">
                      <span className="font-semibold text-pine-950">第 {trace.chapter_number} 章 · 场景 {trace.scene_index + 1}</span>
                      <span className="text-pine-700">{displayValue(trace.status, trace.status || "未知")}</span>
                    </div>
                    <div className="mt-1 flex items-center justify-between gap-3 text-xs text-pine-700">
                      <span>{displayValue(trace.mode, trace.mode || "未知模式")}</span>
                      <span>{String(trace.quality_summary?.violation_count ?? 0)} 个问题</span>
                    </div>
                  </div>
                ))}
                {!traces.length && <div className="py-6 text-center text-xs text-pine-700">暂无生成轨迹</div>}
              </div>
            </section>
          </div>
        </div>

        <div className="mt-4 grid gap-4 xl:grid-cols-2">
          <section className="rounded-lg border border-[var(--workspace-border)] bg-white p-4">
            <div className="flex items-center gap-2 border-b border-pine-900/10 pb-3">
              <GitBranch className="h-4 w-4 text-[var(--workspace-accent)]" />
              <h2 className="text-sm font-bold text-pine-950">最近状态变化</h2>
              <Link to={id ? `/project/${id}/state` : "#"} className="ml-auto flex items-center gap-1 text-xs font-semibold text-[var(--workspace-accent)]">
                状态总览 <ArrowRight className="h-3.5 w-3.5" />
              </Link>
            </div>
            <div className="divide-y divide-pine-900/10">
              {progressions.slice(0, 6).map((item) => (
                <div key={item.id} className="grid gap-1 py-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:gap-4">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold text-pine-950">
                      {String(item.after_value?.entity_name || item.after_value?.alias || item.entity_id)}
                    </div>
                    <div className="mt-1 line-clamp-2 text-xs leading-5 text-pine-700">{item.evidence_text || "没有保存证据摘要"}</div>
                  </div>
                  <div className="text-xs text-pine-700">第 {item.effective_chapter} 章 · 场景 {item.effective_scene + 1}</div>
                </div>
              ))}
              {!progressions.length && <div className="py-8 text-center text-sm text-pine-700">暂无状态变化记录</div>}
            </div>
          </section>

          <section className="rounded-lg border border-[var(--workspace-border)] bg-white p-4">
            <div className="flex items-center gap-2 border-b border-pine-900/10 pb-3">
              <Activity className="h-4 w-4 text-[var(--workspace-accent)]" />
              <h2 className="text-sm font-bold text-pine-950">重复质量模式</h2>
            </div>
            <div className="divide-y divide-pine-900/10">
              {patterns.slice(0, 6).map((pattern, index) => {
                const title = String(pattern.name || pattern.pattern_type || pattern.metric || pattern.type || `质量模式 ${index + 1}`);
                const detail = pattern.description || pattern.summary || pattern.evidence || pattern.advice;
                const count = pattern.count ?? pattern.occurrences;
                return (
                  <div key={`${title}-${index}`} className="py-3">
                    <div className="flex items-center justify-between gap-3 text-sm">
                      <span className="[overflow-wrap:anywhere] font-semibold text-pine-950">{QUALITY_PATTERN_LABELS[title] || displayValue(title, title)}</span>
                      {count !== undefined && <span className="shrink-0 text-xs text-pine-700">{String(count)} 次</span>}
                    </div>
                    {detail !== undefined && <div className="mt-1 [overflow-wrap:anywhere] text-xs leading-5 text-pine-700">{String(detail)}</div>}
                  </div>
                );
              })}
              {!patterns.length && <div className="py-8 text-center text-sm text-pine-700">暂无重复质量模式</div>}
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}
