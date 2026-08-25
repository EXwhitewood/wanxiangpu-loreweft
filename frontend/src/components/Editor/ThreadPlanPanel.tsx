import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Flag,
  GitBranch,
  Loader2,
  RefreshCw,
  Save,
  TrendingUp,
  X,
} from "lucide-react";
import clsx from "clsx";
import * as api from "@/api/client";
import type { ChapterSpineItem, ThreadItem, ThreadPlan } from "@/types";

interface ThreadPlanPanelProps {
  projectId: string;
  spineData: Array<Partial<ChapterSpineItem>>;
}

type ChapterField = "plant_chapters" | "escalation_chapters" | "reveal_chapters";

const THREAD_COLORS = [
  "bg-pine-600",
  "bg-blue-500",
  "bg-emerald-500",
  "bg-violet-500",
  "bg-crimson-500",
  "bg-amber-500",
];

const THREAD_TYPE_STYLES: Record<ThreadItem["thread_type"], string> = {
  main: "border-pine-200 bg-pine-50 text-pine-800",
  subplot: "border-blue-200 bg-blue-50 text-blue-700",
  hidden: "border-monet-200 bg-monet-50 text-monet-700",
  suspense: "border-crimson-200 bg-crimson-50 text-crimson-600",
  foreshadowing: "border-amber-200 bg-amber-50 text-amber-700",
};

const THREAD_TYPE_LABELS: Record<ThreadItem["thread_type"], string> = {
  main: "主线",
  subplot: "支线",
  hidden: "隐藏线",
  suspense: "悬念线",
  foreshadowing: "伏笔引用",
};

const THREAD_TYPE_OPTIONS: Array<{
  value: Exclude<ThreadItem["thread_type"], "foreshadowing">;
  label: string;
}> = [
  { value: "main", label: "主线" },
  { value: "subplot", label: "支线" },
  { value: "hidden", label: "隐藏线" },
  { value: "suspense", label: "悬念线" },
];

const THREAD_STATUS_STYLES: Record<ThreadItem["status"], string> = {
  planned: "border-pine-200 bg-white text-pine-600",
  active: "border-emerald-200 bg-emerald-50 text-emerald-700",
  resolved: "border-blue-200 bg-blue-50 text-blue-700",
  abandoned: "border-crimson-200 bg-crimson-50 text-crimson-600",
};

const THREAD_STATUS_LABELS: Record<ThreadItem["status"], string> = {
  planned: "待推进",
  active: "推进中",
  resolved: "已收束",
  abandoned: "已终止",
};

const PHASE_META: Array<{
  field: ChapterField;
  label: string;
  icon: typeof GitBranch;
  chipClass: string;
}> = [
  {
    field: "plant_chapters",
    label: "建立",
    icon: GitBranch,
    chipClass: "border-pine-200 bg-pine-50 text-pine-800",
  },
  {
    field: "escalation_chapters",
    label: "推进",
    icon: TrendingUp,
    chipClass: "border-amber-200 bg-amber-50 text-amber-800",
  },
  {
    field: "reveal_chapters",
    label: "收束",
    icon: Flag,
    chipClass: "border-blue-200 bg-blue-50 text-blue-800",
  },
];

function normalizeThreadPlan(value: unknown): ThreadPlan {
  const plan = value && typeof value === "object" ? (value as Record<string, unknown>) : {};
  const rawThreads = Array.isArray(plan.threads) ? plan.threads : [];

  const threads = rawThreads
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    .map((item, index): ThreadItem => {
      const threadId = String(
        item.thread_id || item.name || `thread_${String(index + 1).padStart(3, "0")}`
      );
      const rawType = String(item.thread_type || item.type || "subplot");
      const rawStatus = String(item.status || "planned");
      const threadType: ThreadItem["thread_type"] = [
        "main",
        "subplot",
        "hidden",
        "foreshadowing",
        "suspense",
      ].includes(rawType)
        ? (rawType as ThreadItem["thread_type"])
        : "subplot";
      const status: ThreadItem["status"] = [
        "planned",
        "active",
        "resolved",
        "abandoned",
      ].includes(rawStatus)
        ? (rawStatus as ThreadItem["status"])
        : "planned";
      const numberList = (candidate: unknown) =>
        Array.isArray(candidate)
          ? Array.from(
              new Set(
                candidate
                  .map(Number)
                  .filter((chapter) => Number.isInteger(chapter) && chapter > 0)
              )
            ).sort((a, b) => a - b)
          : [];
      const stringList = (candidate: unknown) =>
        Array.isArray(candidate) ? Array.from(new Set(candidate.map(String).filter(Boolean))) : [];

      return {
        thread_id: threadId,
        name: String(item.name || threadId),
        thread_type: threadType,
        status,
        description: String(item.description || ""),
        plant_chapters: numberList(item.plant_chapters),
        escalation_chapters: numberList(item.escalation_chapters || item.develop_chapters),
        reveal_chapters: numberList(item.reveal_chapters || item.payoff_chapters),
        depends_on_threads: stringList(item.depends_on_threads),
      };
    });

  return { threads };
}

function ChapterStageEditor({
  label,
  icon: Icon,
  values,
  spineData,
  chipClass,
  onChange,
}: {
  label: string;
  icon: typeof GitBranch;
  values: number[];
  spineData: Array<Partial<ChapterSpineItem>>;
  chipClass: string;
  onChange: (chapters: number[]) => void;
}) {
  const chapterOptions = spineData
    .map((chapter) => ({
      number: Number(chapter.chapter_number || 0),
      title: String(chapter.title || ""),
    }))
    .filter((chapter) => chapter.number > 0 && !values.includes(chapter.number));

  const addChapter = (chapterNumber: number) => {
    if (!chapterNumber || values.includes(chapterNumber)) return;
    onChange([...values, chapterNumber].sort((a, b) => a - b));
  };

  const removeChapter = (chapterNumber: number) => {
    onChange(values.filter((chapter) => chapter !== chapterNumber));
  };

  return (
    <div className="border-b border-pine-900/10 py-3 last:border-b-0">
      <div className="mb-2 flex items-center justify-between">
        <span className="flex items-center gap-1.5 text-xs font-medium text-pine-800">
          <Icon className="h-3.5 w-3.5 text-pine-500" />
          {label}
        </span>
        <span className="text-[10px] tabular-nums text-pine-500">{values.length} 章</span>
      </div>
      <div className="flex min-h-8 flex-wrap items-center gap-1.5">
        {values.map((chapterNumber) => {
          const chapter = spineData.find((item) => item.chapter_number === chapterNumber);
          return (
            <button
              key={chapterNumber}
              type="button"
              onClick={() => removeChapter(chapterNumber)}
              title={`移除第${chapterNumber}章${chapter?.title ? ` ${chapter.title}` : ""}`}
              className={clsx(
                "inline-flex h-7 items-center gap-1 rounded-md border px-2 text-[11px] font-medium transition-colors hover:border-crimson-300 hover:bg-crimson-50 hover:text-crimson-600",
                chipClass
              )}
            >
              第{chapterNumber}章
              <X className="h-3 w-3" />
            </button>
          );
        })}
        {chapterOptions.length > 0 && (
          <select
            value=""
            onChange={(event) => addChapter(Number(event.target.value))}
            aria-label={`添加${label}章节`}
            className="h-7 min-w-24 rounded-md border border-pine-900/15 bg-white px-2 text-[11px] text-pine-700 outline-none transition-colors hover:border-pine-600/35 focus:border-pine-600 focus:ring-2 focus:ring-pine-600/15"
          >
            <option value="">+ 添加章节</option>
            {chapterOptions.map((chapter) => (
              <option key={chapter.number} value={chapter.number}>
                第{chapter.number}章{chapter.title ? ` ${chapter.title}` : ""}
              </option>
            ))}
          </select>
        )}
      </div>
    </div>
  );
}

export default function ThreadPlanPanel({ projectId, spineData }: ThreadPlanPanelProps) {
  const [threadPlan, setThreadPlan] = useState<ThreadPlan | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const [savingIds, setSavingIds] = useState<Set<string>>(new Set());
  const [localEdits, setLocalEdits] = useState<Map<string, Partial<ThreadItem>>>(new Map());
  const [orphanResults, setOrphanResults] = useState<Array<Record<string, unknown>> | null>(null);
  const [checkingOrphans, setCheckingOrphans] = useState(false);
  const [savedThreadId, setSavedThreadId] = useState<string | null>(null);

  const fetchThreadPlan = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    setError(null);
    try {
      const data = await api.getThreadPlan(projectId);
      setThreadPlan(normalizeThreadPlan(data));
      setLocalEdits(new Map());
      setSavedThreadId(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    fetchThreadPlan();
  }, [fetchThreadPlan]);

  const metrics = useMemo(() => {
    const threads = threadPlan?.threads ?? [];
    return {
      total: threads.length,
      active: threads.filter((thread) => thread.status === "active").length,
      open: threads.filter(
        (thread) =>
          !["resolved", "abandoned"].includes(thread.status)
      ).length,
    };
  }, [threadPlan]);

  const toggleExpand = (threadId: string) => {
    setExpandedIds((previous) => {
      const next = new Set(previous);
      if (next.has(threadId)) next.delete(threadId);
      else next.add(threadId);
      return next;
    });
  };

  const handleLocalEdit = (threadId: string, field: keyof ThreadItem, value: unknown) => {
    setSavedThreadId(null);
    setLocalEdits((previous) => {
      const next = new Map(previous);
      next.set(threadId, { ...next.get(threadId), [field]: value });
      return next;
    });
  };

  const getEffectiveThread = (thread: ThreadItem): ThreadItem => {
    const edit = localEdits.get(thread.thread_id);
    return edit ? { ...thread, ...edit } : thread;
  };

  const handleCancel = (threadId: string) => {
    setLocalEdits((previous) => {
      const next = new Map(previous);
      next.delete(threadId);
      return next;
    });
    setError(null);
  };

  const handleSave = async (threadId: string) => {
    if (!threadPlan) return;
    const edit = localEdits.get(threadId);
    if (!edit) return;

    setSavingIds((previous) => new Set(previous).add(threadId));
    setError(null);
    try {
      const updatedThreads = threadPlan.threads.map((thread) =>
        thread.thread_id === threadId ? { ...thread, ...edit } : thread
      );
      await api.updateStoryPlanLayer(projectId, "thread_plan", { threads: updatedThreads });
      setThreadPlan({ ...threadPlan, threads: updatedThreads });
      setLocalEdits((previous) => {
        const next = new Map(previous);
        next.delete(threadId);
        return next;
      });
      setSavedThreadId(threadId);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSavingIds((previous) => {
        const next = new Set(previous);
        next.delete(threadId);
        return next;
      });
    }
  };

  const handleCheckOrphans = async () => {
    if (!projectId || checkingOrphans) return;
    setCheckingOrphans(true);
    setError(null);
    try {
      const results = await api.checkOrphanThreads(projectId);
      setOrphanResults(results);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setCheckingOrphans(false);
    }
  };

  const handleRefresh = () => {
    if (
      localEdits.size > 0 &&
      !window.confirm("当前有未保存的剧情线修改，确定刷新吗？")
    ) {
      return;
    }
    fetchThreadPlan();
  };

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-pine-600">
        <Loader2 className="mr-2 h-5 w-5 animate-spin" />
        正在加载剧情线…
      </div>
    );
  }

  if (error && !threadPlan) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 text-sm text-crimson-600">
        <p>加载失败：{error}</p>
        <button
          type="button"
          onClick={fetchThreadPlan}
          className="inline-flex items-center gap-1.5 rounded-md border border-pine-900/15 bg-white px-3 py-2 text-pine-700 hover:bg-pine-50"
        >
          <RefreshCw className="h-4 w-4" />
          重试
        </button>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center justify-between gap-4 border-b border-[var(--workspace-border)] bg-[var(--workspace-chrome)] px-5 py-3">
        <div className="flex min-w-0 items-center gap-4">
          <div className="flex items-center gap-2">
            <GitBranch className="h-4 w-4 text-pine-700" />
            <h3 className="text-sm font-semibold text-pine-950">剧情线</h3>
          </div>
          <div className="flex items-center gap-3 text-[11px] text-pine-600">
            <span>共 <strong className="font-semibold text-pine-900">{metrics.total}</strong> 条</span>
            <span>推进中 <strong className="font-semibold text-pine-900">{metrics.active}</strong></span>
            <span>待收束 <strong className="font-semibold text-pine-900">{metrics.open}</strong></span>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <button
            type="button"
            onClick={handleCheckOrphans}
            disabled={checkingOrphans}
            className="inline-flex h-8 items-center gap-1.5 rounded-md border border-amber-300 bg-amber-50 px-2.5 text-xs font-medium text-amber-800 transition-colors hover:border-amber-400 hover:bg-amber-100 disabled:opacity-50"
          >
            {checkingOrphans ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <AlertTriangle className="h-3.5 w-3.5" />
            )}
            检查断线
          </button>
          <button
            type="button"
            onClick={handleRefresh}
            title="刷新剧情线"
            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-pine-600 transition-colors hover:bg-pine-900/[0.05] hover:text-pine-950"
          >
            <RefreshCw className="h-4 w-4" />
          </button>
        </div>
      </div>

      {error && threadPlan && (
        <div className="mx-4 mt-3 shrink-0 rounded-md border border-crimson-200 bg-crimson-50 px-3 py-2 text-xs text-crimson-700">
          {error}
        </div>
      )}

      {orphanResults && (
        <div
          className={clsx(
            "mx-4 mt-3 shrink-0 rounded-md border px-3 py-2 text-xs",
            orphanResults.length > 0
              ? "border-amber-200 bg-amber-50 text-amber-800"
              : "border-emerald-200 bg-emerald-50 text-emerald-700"
          )}
        >
          <div className="flex items-center gap-1.5 font-medium">
            {orphanResults.length > 0 ? (
              <AlertTriangle className="h-3.5 w-3.5" />
            ) : (
              <CheckCircle2 className="h-3.5 w-3.5" />
            )}
            {orphanResults.length > 0
              ? `发现 ${orphanResults.length} 条断线剧情线`
              : "剧情线引用完整"}
          </div>
          {orphanResults.length > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[11px]">
              {orphanResults.map((orphan, index) => (
                <span key={index}>
                  {String(orphan.name ?? orphan.thread_id ?? `剧情线${index + 1}`)}
                  {orphan.reason ? `：${String(orphan.reason)}` : ""}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {!threadPlan || threadPlan.threads.length === 0 ? (
          <div className="flex h-40 items-center justify-center text-sm text-pine-600">
            暂无剧情线数据
          </div>
        ) : (
          <div className="space-y-2">
            {threadPlan.threads.map((thread, index) => {
              const effective = getEffectiveThread(thread);
              const isExpanded = expandedIds.has(thread.thread_id);
              const isSaving = savingIds.has(thread.thread_id);
              const hasEdit = localEdits.has(thread.thread_id);
              const isSaved = savedThreadId === thread.thread_id;

              return (
                <section
                  key={thread.thread_id}
                  className={clsx(
                    "overflow-hidden rounded-md border bg-[var(--surface-raised)] transition-colors",
                    isExpanded ? "border-pine-700/25" : "border-pine-900/10 hover:border-pine-700/20"
                  )}
                >
                  <button
                    type="button"
                    onClick={() => toggleExpand(thread.thread_id)}
                    className="flex min-h-12 w-full items-center gap-2.5 px-3 py-2.5 text-left"
                  >
                    {isExpanded ? (
                      <ChevronDown className="h-4 w-4 shrink-0 text-pine-600" />
                    ) : (
                      <ChevronRight className="h-4 w-4 shrink-0 text-pine-600" />
                    )}
                    <span className={clsx("h-2.5 w-2.5 shrink-0 rounded-full", THREAD_COLORS[index % THREAD_COLORS.length])} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium text-pine-900">
                        {effective.name}
                      </span>
                      <span className="mt-0.5 block truncate text-[11px] text-pine-500">
                        建立 {effective.plant_chapters.length} · 推进 {effective.escalation_chapters.length} · 收束 {effective.reveal_chapters.length}
                      </span>
                    </span>
                    {hasEdit && (
                      <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-800">
                        未保存
                      </span>
                    )}
                    {isSaved && !hasEdit && (
                      <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" />
                    )}
                    <span className={clsx("rounded-md border px-1.5 py-0.5 text-[10px] font-medium", THREAD_TYPE_STYLES[effective.thread_type])}>
                      {THREAD_TYPE_LABELS[effective.thread_type]}
                    </span>
                    <span className={clsx("rounded-md border px-1.5 py-0.5 text-[10px] font-medium", THREAD_STATUS_STYLES[effective.status])}>
                      {THREAD_STATUS_LABELS[effective.status]}
                    </span>
                  </button>

                  {isExpanded && (
                    <div className="border-t border-pine-900/10 bg-pine-900/[0.015] px-4 py-4">
                      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                        <label className="sm:col-span-2">
                          <span className="mb-1 block text-[11px] font-medium text-pine-700">名称</span>
                          <input
                            value={effective.name}
                            onChange={(event) => handleLocalEdit(thread.thread_id, "name", event.target.value)}
                            className="ui-field px-3 py-2 text-sm"
                          />
                        </label>
                        <label>
                          <span className="mb-1 block text-[11px] font-medium text-pine-700">类型</span>
                          <select
                            value={effective.thread_type}
                            onChange={(event) =>
                              handleLocalEdit(
                                thread.thread_id,
                                "thread_type",
                                event.target.value as ThreadItem["thread_type"]
                              )
                            }
                            className="ui-field px-3 py-2 text-sm"
                          >
                            {effective.thread_type === "foreshadowing" && (
                              <option value="foreshadowing">伏笔引用（旧数据）</option>
                            )}
                            {THREAD_TYPE_OPTIONS.map((option) => (
                              <option key={option.value} value={option.value}>{option.label}</option>
                            ))}
                          </select>
                        </label>
                        <label>
                          <span className="mb-1 block text-[11px] font-medium text-pine-700">状态</span>
                          <select
                            value={effective.status}
                            onChange={(event) =>
                              handleLocalEdit(
                                thread.thread_id,
                                "status",
                                event.target.value as ThreadItem["status"]
                              )
                            }
                            className="ui-field px-3 py-2 text-sm"
                          >
                            {Object.entries(THREAD_STATUS_LABELS).map(([value, label]) => (
                              <option key={value} value={value}>{label}</option>
                            ))}
                          </select>
                        </label>
                        <label className="sm:col-span-2">
                          <span className="mb-1 block text-[11px] font-medium text-pine-700">剧情线概要</span>
                          <textarea
                            value={effective.description}
                            onChange={(event) => handleLocalEdit(thread.thread_id, "description", event.target.value)}
                            rows={3}
                            className="ui-field resize-y px-3 py-2 text-sm leading-6"
                          />
                        </label>
                      </div>

                      <div className="mt-4 border-y border-pine-900/10">
                        {PHASE_META.map((phase) => (
                          <ChapterStageEditor
                            key={phase.field}
                            label={phase.label}
                            icon={phase.icon}
                            values={effective[phase.field]}
                            spineData={spineData}
                            chipClass={phase.chipClass}
                            onChange={(chapters) => handleLocalEdit(thread.thread_id, phase.field, chapters)}
                          />
                        ))}
                      </div>

                      <div className="mt-4">
                        <div className="mb-2 flex items-center justify-between">
                          <span className="text-[11px] font-medium text-pine-700">依赖剧情线</span>
                          <span className="text-[10px] text-pine-500">{effective.depends_on_threads.length} 条</span>
                        </div>
                        <div className="flex min-h-8 flex-wrap items-center gap-1.5">
                          {effective.depends_on_threads.map((dependencyId) => {
                            const dependency = threadPlan.threads.find((item) => item.thread_id === dependencyId);
                            return (
                              <button
                                key={dependencyId}
                                type="button"
                                onClick={() =>
                                  handleLocalEdit(
                                    thread.thread_id,
                                    "depends_on_threads",
                                    effective.depends_on_threads.filter((id) => id !== dependencyId)
                                  )
                                }
                                title="移除依赖"
                                className="inline-flex h-7 items-center gap-1 rounded-md border border-monet-200 bg-monet-50 px-2 text-[11px] text-monet-700 hover:border-crimson-300 hover:bg-crimson-50 hover:text-crimson-600"
                              >
                                {dependency?.name ?? dependencyId}
                                <X className="h-3 w-3" />
                              </button>
                            );
                          })}
                          {threadPlan.threads.some(
                            (candidate) =>
                              candidate.thread_id !== thread.thread_id &&
                              !effective.depends_on_threads.includes(candidate.thread_id)
                          ) && (
                            <select
                              value=""
                              onChange={(event) => {
                                if (!event.target.value) return;
                                handleLocalEdit(thread.thread_id, "depends_on_threads", [
                                  ...effective.depends_on_threads,
                                  event.target.value,
                                ]);
                              }}
                              aria-label="添加依赖剧情线"
                              className="h-7 min-w-28 rounded-md border border-pine-900/15 bg-white px-2 text-[11px] text-pine-700 outline-none hover:border-pine-600/35 focus:border-pine-600 focus:ring-2 focus:ring-pine-600/15"
                            >
                              <option value="">+ 添加依赖</option>
                              {threadPlan.threads
                                .filter(
                                  (candidate) =>
                                    candidate.thread_id !== thread.thread_id &&
                                    !effective.depends_on_threads.includes(candidate.thread_id)
                                )
                                .map((candidate) => (
                                  <option key={candidate.thread_id} value={candidate.thread_id}>
                                    {candidate.name}
                                  </option>
                                ))}
                            </select>
                          )}
                        </div>
                      </div>

                      <div className="sticky bottom-0 z-10 -mx-4 -mb-4 mt-4 flex items-center justify-end gap-2 border-t border-pine-900/10 bg-[var(--surface-raised)]/95 px-4 py-3 backdrop-blur-md">
                        {hasEdit && (
                          <button
                            type="button"
                            onClick={() => handleCancel(thread.thread_id)}
                            disabled={isSaving}
                            className="inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium text-pine-600 transition-colors hover:bg-pine-900/[0.05] hover:text-pine-950 disabled:opacity-50"
                          >
                            <X className="h-3.5 w-3.5" />
                            取消
                          </button>
                        )}
                        <button
                          type="button"
                          onClick={() => handleSave(thread.thread_id)}
                          disabled={!hasEdit || isSaving}
                          className="inline-flex items-center gap-1.5 rounded-md bg-pine-700 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-pine-800 disabled:cursor-not-allowed disabled:opacity-40"
                        >
                          {isSaving ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <Save className="h-3.5 w-3.5" />
                          )}
                          保存剧情线
                        </button>
                      </div>
                    </div>
                  )}
                </section>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
