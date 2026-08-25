import { useState, useEffect } from "react";
import {
  ArrowLeft,
  Save,
  Loader2,
  Plus,
  Lock,
  AlertTriangle,
} from "lucide-react";
import * as api from "@/api/client";
import type { ChapterSpineItem, SceneBriefItem } from "@/types";
import SceneBriefCard from "@/components/Editor/SceneBriefCard";

/* Hallmark · pre-emit critique: P5 H5 E5 S5 R5 V4 */
type DetailTab = "overview" | "spine" | "scenes" | "threads";

const SCENE_SOURCE_LABELS: Record<string, string> = {
  generated: "AI 生成",
  expert_generated: "专家生成",
  auto_completed: "自动补全",
  legacy: "迁移",
  user_edited: "已编辑",
};

interface ChapterOutlineDetailProps {
  projectId: string;
  chapterNumber: number;
  outlineData: Record<string, unknown>;
  frozen: boolean;
  onBack: () => void;
  onSave: () => void;
  refreshRevision?: number;
}

export default function ChapterOutlineDetail({
  projectId,
  chapterNumber,
  frozen,
  onBack,
  onSave,
  refreshRevision = 0,
}: ChapterOutlineDetailProps) {
  const [spineItem, setSpineItem] = useState<Partial<ChapterSpineItem> | null>(null);
  const [scenes, setScenes] = useState<SceneBriefItem[]>([]);
  const [sceneSource, setSceneSource] = useState<string>("");
  const [sceneBriefMeta, setSceneBriefMeta] = useState<Record<string, unknown>>({});
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [activeTab, setActiveTab] = useState<DetailTab>("overview");
  const [relatedThreads, setRelatedThreads] = useState<Array<Record<string, unknown>>>([]);
  const [threadsLoading, setThreadsLoading] = useState(false);
  const [threadsError, setThreadsError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    const loadData = async () => {
      try {
        const plan = await api.getStoryPlan(projectId);
        if (!mounted) return;
        const spine = (plan.chapter_spine || []) as Partial<ChapterSpineItem>[];
        const item = spine.find((s) => s.chapter_number === chapterNumber);
        if (item) {
          setSpineItem(item);
        }
      } catch (e) {
        console.error("加载章节脊柱失败:", e);
      }

      try {
        const brief = await api.getSceneBrief(projectId, chapterNumber);
        if (!mounted) return;
        if (brief && brief.scenes) {
          setScenes(brief.scenes);
          setSceneSource(brief.source || "");
          setSceneBriefMeta(brief as unknown as Record<string, unknown>);
        }
      } catch {
        setScenes([]);
        setSceneSource("");
        setSceneBriefMeta({});
      }

      setThreadsLoading(true);
      setThreadsError(null);
      try {
        const threads = await api.getThreadsForChapter(projectId, chapterNumber);
        if (!mounted) return;
        setRelatedThreads(Array.isArray(threads) ? threads : []);
      } catch (e) {
        if (!mounted) return;
        setRelatedThreads([]);
        setThreadsError((e as Error).message);
      } finally {
        if (mounted) setThreadsLoading(false);
      }
    };
    loadData();
    return () => { mounted = false; };
  }, [projectId, chapterNumber, refreshRevision]);

  const buildConflictText = (cc: { desire?: string; obstacle?: string; action?: string; turn?: string } | null | undefined): string => {
    const parts = [
      cc?.desire || "",
      cc?.obstacle || "",
      cc?.action || "",
      cc?.turn || "",
    ].filter(Boolean);
    return parts.join("，但");
  };

  const updateSpineField = (field: string, value: unknown) => {
    setSpineItem((prev) => (prev ? { ...prev, [field]: value } : prev));
    setDirty(true);
  };

  const updateCoreConflict = (subField: string, value: string) => {
    setSpineItem((prev) => {
      if (!prev) return prev;
      const cc = prev.core_conflict || { desire: "", obstacle: "", action: "", turn: "" };
      const newCc = { ...cc, [subField]: value };
      return { ...prev, core_conflict: newCc, conflict_text: buildConflictText(newCc) };
    });
    setDirty(true);
  };

  const updateValueShift = (subField: string, value: string) => {
    setSpineItem((prev) => {
      if (!prev) return prev;
      const vs = (prev.value_shift && typeof prev.value_shift === "object")
        ? prev.value_shift as { axis: string; from: string; to: string }
        : { axis: "", from: "", to: "" };
      return { ...prev, value_shift: { ...vs, [subField]: value } };
    });
    setDirty(true);
  };

  const updateScene = (sceneIndex: number, field: keyof SceneBriefItem, value: string) => {
    setScenes((prev) => {
      const newScenes = [...prev];
      newScenes[sceneIndex] = { ...newScenes[sceneIndex], [field]: value };
      return newScenes;
    });
    setDirty(true);
  };

  const addScene = () => {
    setScenes((prev) => [
      ...prev,
      {
        scene_id: `ch_${String(chapterNumber).padStart(3, "0")}_s${prev.length + 1}`,
        type: "development",
        goal: "",
        conflict: "",
        outcome: "",
        info_release: "",
        hook: "",
        required_context_refs: [],
      },
    ]);
    setDirty(true);
  };

  const removeScene = (sceneIndex: number) => {
    setScenes((prev) => prev.filter((_, i) => i !== sceneIndex));
    setDirty(true);
  };

  const handleSave = async () => {
    if (!spineItem) return;
    setSaving(true);
    try {
      await api.updateChapterSpineItem(projectId, chapterNumber, {
        title: spineItem.title,
        core_conflict: spineItem.core_conflict,
        conflict_text: spineItem.conflict_text,
        value_shift: spineItem.value_shift,
        pov_character: spineItem.pov_character,
        hook: spineItem.hook,
      });

      if (scenes.length > 0) {
        await api.saveSceneBrief(projectId, chapterNumber, {
          scenes,
          source: sceneSource || "user_edited",
          scene_count_user_overridden: sceneBriefMeta.scene_count_user_overridden || false,
        });
      }

      setDirty(false);
      onSave();
    } catch (e) {
      console.error("保存章节大纲失败:", e);
    } finally {
      setSaving(false);
    }
  };

  if (!spineItem) {
    return (
      <div className="chapter-outline-detail flex h-full flex-col items-center justify-center text-pine-800">
        <p className="text-sm">该章节暂无大纲</p>
        <button
          onClick={onBack}
          className="mt-3 text-xs text-magic-400 hover:underline"
        >
          返回大纲总文件
        </button>
      </div>
    );
  }

  const coreConflict = spineItem.core_conflict || { desire: "", obstacle: "", action: "", turn: "" };
  const recommendedSceneCount = Number(sceneBriefMeta.recommended_scene_count || 0);
  const sceneCountOverridden = Boolean(sceneBriefMeta.scene_count_user_overridden);
  const valueShift = (spineItem.value_shift && typeof spineItem.value_shift === "object")
    ? spineItem.value_shift as { axis: string; from: string; to: string }
    : { axis: "", from: "", to: "" };

  return (
    <div className="chapter-outline-detail flex h-full flex-col">
      <div className="chapter-outline-detail__header flex items-center justify-between border-b border-white/40 px-5 py-3">
        <div className="flex items-center gap-3">
          <button
            onClick={onBack}
            className="chapter-outline-detail__back rounded-lg p-1.5 text-pine-700 hover:bg-white/50 hover:text-pine-950"
          >
            <ArrowLeft className="h-4 w-4" />
          </button>
          <div>
            <h2 className="chapter-outline-detail__title text-sm font-semibold text-pine-950">
              第{chapterNumber}章 大纲
            </h2>
            {frozen && (
              <span className="inline-flex items-center gap-1 text-xs text-crimson-400">
                <Lock className="h-3 w-3" />
                已冻结
              </span>
            )}
          </div>
        </div>
        {!frozen && (
          <button
            onClick={handleSave}
            disabled={saving || !dirty}
            className={`chapter-outline-detail__save flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-pine-600/40 ${
              dirty
                ? "bg-pine-700 text-white hover:bg-pine-800 active:bg-pine-900"
                : "bg-white/55 text-pine-500"
            }`}
          >
            {saving ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Save className="h-3.5 w-3.5" />
            )}
            保存
          </button>
        )}
      </div>

      <div className="chapter-outline-detail__content flex-1 overflow-auto px-5 py-4 space-y-4">
        <div className="chapter-outline-detail__tabs flex gap-1 overflow-x-auto border-b border-pine-200/50 pb-2">
          {([
            ["overview", "概览"],
            ["spine", "章节脊柱"],
            ["scenes", `场景简报 (${scenes.length}${recommendedSceneCount ? ` / 建议 ${recommendedSceneCount}` : ""})`],
            ["threads", `关联线索 (${relatedThreads.length})`],
          ] as Array<[DetailTab, string]>).map(([tab, label]) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`chapter-outline-detail__tab shrink-0 whitespace-nowrap rounded-md px-2.5 py-1.5 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-pine-600/25 ${
                activeTab === tab
                  ? "chapter-outline-detail__tab--active bg-pine-900/10 font-medium text-pine-900"
                  : "text-pine-800 hover:bg-white/50 hover:text-pine-950"
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {activeTab === "overview" && (
          <div className="mx-auto max-w-3xl pb-10">
            <section className="relative overflow-hidden border-b border-pine-900/10 px-1 pb-7 pt-2">
              <span
                className="pointer-events-none absolute right-1 top-[-18px] font-serif text-[96px] font-semibold leading-none text-pine-900/[0.045]"
                aria-hidden="true"
              >
                {String(chapterNumber).padStart(2, "0")}
              </span>
              <div className="relative z-10 flex min-w-0 items-center justify-between gap-3">
                <span className="text-xs font-medium text-pine-600">
                  第 {String(chapterNumber).padStart(2, "0")} 章
                </span>
                <div className="flex min-w-0 items-center gap-2 text-[11px] text-pine-600">
                  {spineItem.pov_character && (
                    <span className="max-w-40 truncate">视角：{spineItem.pov_character}</span>
                  )}
                  <span className="rounded-md border border-pine-900/10 bg-white/70 px-2 py-0.5 font-medium text-pine-700">
                    {frozen ? "已冻结" : "草稿"}
                  </span>
                </div>
              </div>
              <h1 className="relative z-10 mt-4 min-w-0 [overflow-wrap:anywhere] font-serif text-3xl font-semibold leading-tight text-pine-900">
                {spineItem.title || "未命名章节"}
              </h1>
            </section>

            <section className="-mx-5 border-b border-pine-900/10 bg-pine-900/[0.035] px-6 py-7">
              <div className="mx-auto grid max-w-3xl grid-cols-[3px_minmax(0,1fr)] gap-4">
                <span className="h-full min-h-20 bg-pine-600" aria-hidden="true" />
                <div className="min-w-0">
                  <p className="text-xs font-medium text-pine-600">核心冲突</p>
                  <p className="mt-2 text-base leading-8 text-pine-900">
                    {spineItem.conflict_text || "本章尚未设定核心冲突。"}
                  </p>
                </div>
              </div>
            </section>

            <section className="grid grid-cols-1 border-b border-pine-900/10 sm:grid-cols-[minmax(0,2fr)_minmax(140px,1fr)]">
              <div className="min-w-0 py-6 sm:pr-8">
                <p className="text-xs font-medium text-tea-700">章节钩子</p>
                <p className="mt-2 text-sm leading-7 text-pine-900">
                  {spineItem.hook || "本章尚未设定钩子。"}
                </p>
              </div>
              <div className="flex items-end justify-between gap-4 border-t border-pine-900/10 py-6 sm:border-l sm:border-t-0 sm:pl-8">
                <div>
                  <p className="text-xs font-medium text-pine-600">场景数量</p>
                  <p className="mt-1 text-[11px] text-pine-500">
                    {recommendedSceneCount ? `建议 ${recommendedSceneCount}` : "已规划"}
                  </p>
                </div>
                <span className="font-serif text-4xl font-semibold tabular-nums leading-none text-pine-800">
                  {String(scenes.length).padStart(2, "0")}
                </span>
              </div>
            </section>

            {(valueShift.axis || valueShift.from || valueShift.to) && (
              <section className="grid grid-cols-1 gap-3 py-6 sm:grid-cols-[140px_minmax(0,1fr)] sm:items-center">
                <div>
                  <p className="text-xs font-medium text-pine-600">价值转变</p>
                  {valueShift.axis && (
                    <p className="mt-1 text-[11px] text-pine-500">{valueShift.axis}</p>
                  )}
                </div>
                <div className="flex min-w-0 items-center gap-3 text-sm text-pine-900">
                  <span className="min-w-0 flex-1 border-b border-pine-900/10 pb-1">
                    {valueShift.from || "未设定"}
                  </span>
                  <span className="shrink-0 text-tea-600" aria-hidden="true">→</span>
                  <span className="min-w-0 flex-1 border-b border-pine-900/10 pb-1 text-right">
                    {valueShift.to || "未设定"}
                  </span>
                </div>
              </section>
            )}
          </div>
        )}

        {activeTab === "spine" && (
          <div className="mx-auto max-w-3xl pb-12">
            <section className="relative grid grid-cols-[56px_minmax(0,1fr)] gap-4 border-b border-pine-900/10 pb-7 pt-2 sm:grid-cols-[72px_minmax(0,1fr)]">
              <span className="font-serif text-4xl font-semibold tabular-nums leading-none text-pine-700 sm:text-5xl">
                {String(chapterNumber).padStart(2, "0")}
              </span>
              <div className="min-w-0">
                <div className="mb-2 flex items-center justify-between gap-3">
                  <label htmlFor="chapter-spine-title" className="text-[11px] font-medium text-pine-600">
                    章节标题
                  </label>
                  <span className="rounded-md border border-pine-900/10 bg-white/65 px-2 py-0.5 text-[10px] font-medium text-pine-600">
                    {frozen ? "已冻结" : "编辑中"}
                  </span>
                </div>
                {frozen ? (
                  <p className="min-w-0 [overflow-wrap:anywhere] font-serif text-2xl font-semibold leading-tight text-pine-950">
                    {spineItem.title || "未命名章节"}
                  </p>
                ) : (
                  <input
                    id="chapter-spine-title"
                    type="text"
                    value={spineItem.title || ""}
                    onChange={(e) => updateSpineField("title", e.target.value)}
                    placeholder="输入章节标题"
                    className="w-full border-0 border-b border-pine-900/15 bg-transparent px-0 pb-2 font-serif text-2xl font-semibold leading-tight text-pine-950 outline-none transition-colors placeholder:text-pine-400 focus:border-pine-700 focus:ring-0"
                  />
                )}
              </div>
            </section>

            <section className="-mx-5 border-b border-pine-900/10 bg-pine-900/[0.035] px-5 py-7 sm:px-6">
              <div className="mb-5 flex items-end justify-between gap-4">
                <div className="flex items-baseline gap-3">
                  <span className="font-serif text-xl font-semibold tabular-nums text-pine-500">01</span>
                  <h3 className="text-sm font-semibold text-pine-950">核心冲突</h3>
                </div>
                <span className="text-[10px] tracking-wide text-pine-500">渴望 · 障碍 · 行动 · 转折</span>
              </div>

              <div className="grid grid-cols-1 border-y border-pine-900/10 sm:grid-cols-2">
                {([
                  ["desire", "01", "渴望", "本章角色想要什么"],
                  ["obstacle", "02", "障碍", "什么正在阻止他"],
                  ["action", "03", "行动", "角色采取什么行动"],
                  ["turn", "04", "转折", "行动导致什么不可逆变化"],
                ] as const).map(([field, number, label, placeholder], index) => (
                  <div
                    key={field}
                    className={`min-w-0 py-4 ${index >= 2 ? "border-t border-pine-900/10" : ""} ${
                      index % 2 === 0 ? "sm:pr-5" : "sm:border-l sm:border-pine-900/10 sm:pl-5"
                    }`}
                  >
                    <label className="mb-2 flex items-center gap-2 text-[11px] font-medium text-pine-700">
                      <span className="font-mono text-[9px] tabular-nums text-pine-400">{number}</span>
                      {label}
                    </label>
                    {frozen ? (
                      <p className="min-h-12 text-sm leading-6 text-pine-800">{coreConflict[field] || "未设定"}</p>
                    ) : (
                      <textarea
                        value={coreConflict[field] || ""}
                        onChange={(e) => updateCoreConflict(field, e.target.value)}
                        placeholder={placeholder}
                        rows={2}
                        className="w-full resize-none rounded-md border border-pine-900/10 bg-white/80 px-3 py-2.5 text-sm leading-6 text-pine-950 outline-none transition-colors placeholder:text-pine-400 hover:border-pine-900/20 focus:border-pine-700 focus:bg-white focus:ring-2 focus:ring-pine-600/10"
                      />
                    )}
                  </div>
                ))}
              </div>

              <div className="mt-5 grid grid-cols-[3px_minmax(0,1fr)] gap-4">
                <span className="h-full min-h-20 bg-pine-600" aria-hidden="true" />
                <div className="min-w-0">
                  <label className="mb-2 block text-[11px] font-medium text-pine-700">冲突陈述</label>
                  {frozen ? (
                    <p className="text-sm leading-7 text-pine-900">{spineItem.conflict_text || "未设定"}</p>
                  ) : (
                    <textarea
                      value={spineItem.conflict_text || ""}
                      onChange={(e) => updateSpineField("conflict_text", e.target.value)}
                      placeholder="由四要素形成的完整冲突陈述"
                      rows={3}
                      className="w-full resize-none rounded-md border border-pine-900/10 bg-white/90 px-3 py-2.5 text-sm leading-7 text-pine-950 outline-none transition-colors placeholder:text-pine-400 hover:border-pine-900/20 focus:border-pine-700 focus:ring-2 focus:ring-pine-600/10"
                    />
                  )}
                </div>
              </div>
            </section>

            <section className="border-b border-pine-900/10 py-7">
              <div className="mb-5 flex items-baseline gap-3">
                <span className="font-serif text-xl font-semibold tabular-nums text-pine-500">02</span>
                <h3 className="text-sm font-semibold text-pine-950">价值与视角</h3>
              </div>

              <div className="grid grid-cols-1 gap-6 sm:grid-cols-[minmax(150px,0.8fr)_minmax(0,2fr)]">
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-1">
                  <div>
                    <label className="mb-1.5 block text-[11px] font-medium text-pine-600">视角角色</label>
                    {frozen ? (
                      <p className="text-sm text-pine-900">{spineItem.pov_character || "未设定"}</p>
                    ) : (
                      <input
                        type="text"
                        value={spineItem.pov_character || ""}
                        onChange={(e) => updateSpineField("pov_character", e.target.value)}
                        placeholder="视角角色名"
                        className="w-full rounded-md border border-pine-900/10 bg-white/75 px-3 py-2 text-sm text-pine-950 outline-none transition-colors placeholder:text-pine-400 hover:border-pine-900/20 focus:border-pine-700 focus:bg-white focus:ring-2 focus:ring-pine-600/10"
                      />
                    )}
                  </div>
                  <div>
                    <label className="mb-1.5 block text-[11px] font-medium text-pine-600">价值轴</label>
                    {frozen ? (
                      <p className="text-sm text-pine-900">{valueShift.axis || "未设定"}</p>
                    ) : (
                      <input
                        type="text"
                        value={valueShift.axis || ""}
                        onChange={(e) => updateValueShift("axis", e.target.value)}
                        placeholder="例如：安全 / 危险"
                        className="w-full rounded-md border border-pine-900/10 bg-white/75 px-3 py-2 text-sm text-pine-950 outline-none transition-colors placeholder:text-pine-400 hover:border-pine-900/20 focus:border-pine-700 focus:bg-white focus:ring-2 focus:ring-pine-600/10"
                      />
                    )}
                  </div>
                </div>

                <div className="min-w-0">
                  <label className="mb-1.5 block text-[11px] font-medium text-pine-600">变化轨迹</label>
                  <div className="grid grid-cols-[minmax(0,1fr)_24px_minmax(0,1fr)] items-center gap-2">
                    {frozen ? (
                      <>
                        <p className="min-w-0 border-b border-pine-900/10 py-2 text-sm text-pine-900">{valueShift.from || "未设定"}</p>
                        <span className="text-center text-pine-500" aria-hidden="true">→</span>
                        <p className="min-w-0 border-b border-pine-900/10 py-2 text-right text-sm text-pine-900">{valueShift.to || "未设定"}</p>
                      </>
                    ) : (
                      <>
                        <input
                          type="text"
                          value={valueShift.from || ""}
                          onChange={(e) => updateValueShift("from", e.target.value)}
                          placeholder="起点"
                          className="min-w-0 rounded-md border border-pine-900/10 bg-white/75 px-3 py-2 text-sm text-pine-950 outline-none transition-colors placeholder:text-pine-400 hover:border-pine-900/20 focus:border-pine-700 focus:bg-white focus:ring-2 focus:ring-pine-600/10"
                        />
                        <span className="text-center text-pine-500" aria-hidden="true">→</span>
                        <input
                          type="text"
                          value={valueShift.to || ""}
                          onChange={(e) => updateValueShift("to", e.target.value)}
                          placeholder="终点"
                          className="min-w-0 rounded-md border border-pine-900/10 bg-white/75 px-3 py-2 text-sm text-pine-950 outline-none transition-colors placeholder:text-pine-400 hover:border-pine-900/20 focus:border-pine-700 focus:bg-white focus:ring-2 focus:ring-pine-600/10"
                        />
                      </>
                    )}
                  </div>
                </div>
              </div>
            </section>

            <section className="py-7">
              <div className="mb-4 flex items-baseline gap-3">
                <span className="font-serif text-xl font-semibold tabular-nums text-pine-500">03</span>
                <h3 className="text-sm font-semibold text-pine-950">章节钩子</h3>
              </div>
              {frozen ? (
                <p className="border-l-2 border-tea-500 pl-4 text-sm leading-7 text-pine-900">
                  {spineItem.hook || "未设定"}
                </p>
              ) : (
                <textarea
                  value={spineItem.hook || ""}
                  onChange={(e) => updateSpineField("hook", e.target.value)}
                  placeholder="读者离开本章时必须带走的疑问或压力"
                  rows={3}
                  className="w-full resize-none border-0 border-l-2 border-tea-500 bg-pine-900/[0.025] px-4 py-3 text-sm leading-7 text-pine-950 outline-none transition-colors placeholder:text-pine-400 focus:border-pine-700 focus:bg-pine-900/[0.045] focus:ring-0"
                />
              )}
            </section>

            {Array.isArray(spineItem.thread_ops) && spineItem.thread_ops.length > 0 && (
              <section className="border-t border-pine-900/10 pt-5">
                <div className="mb-3 flex items-center justify-between gap-3">
                  <h3 className="text-xs font-semibold text-pine-800">剧情线操作</h3>
                  <span className="text-[10px] tabular-nums text-pine-500">{spineItem.thread_ops.length} 项</span>
                </div>
                <div className="flex flex-wrap gap-x-4 gap-y-2">
                  {spineItem.thread_ops.map((op, idx) => (
                    <span key={idx} className="inline-flex items-center gap-1.5 border-b border-pine-900/10 pb-1 text-[11px] text-pine-700">
                      <span className="font-medium text-pine-900">{op.thread_id}</span>
                      <span>{op.op}</span>
                      <span className="text-pine-500">{op.mode}</span>
                    </span>
                  ))}
                </div>
              </section>
            )}
          </div>
        )}

        {activeTab === "scenes" && (
          <div>
            {recommendedSceneCount > scenes.length && !sceneCountOverridden && (
              <div className="mb-4 rounded-lg border border-monet-300/50 bg-monet-50 p-3 text-xs text-monet-700">
                <p className="font-medium text-monet-800">
                  本章当前有 {scenes.length} 个场景，建议补充到 {recommendedSceneCount} 个。
                </p>
                {Array.isArray(sceneBriefMeta.scene_budget_reasons) && sceneBriefMeta.scene_budget_reasons.length > 0 && (
                  <p className="mt-1">{sceneBriefMeta.scene_budget_reasons.map(String).join("；")}</p>
                )}
                {!frozen && (
                  <button
                    type="button"
                    onClick={() => {
                      setSceneBriefMeta((prev) => ({ ...prev, scene_count_user_overridden: true }));
                      setDirty(true);
                    }}
                    className="mt-2 rounded-md border border-monet-300 bg-white px-2.5 py-1 text-[11px] font-medium text-monet-700 hover:border-monet-400 hover:text-monet-900"
                  >
                    保持当前数量
                  </button>
                )}
              </div>
            )}

            <div className="mb-4 flex items-center justify-between gap-3">
              <div className="flex min-w-0 items-center gap-2">
                <span className="text-xs font-medium text-pine-800">场景简报</span>
                <span className="text-[11px] tabular-nums text-pine-500">{scenes.length} 个场景</span>
                {sceneSource && (
                  <span className="rounded-md border border-pine-900/10 bg-white/70 px-1.5 py-0.5 text-[10px] text-pine-600">
                    {SCENE_SOURCE_LABELS[sceneSource] || sceneSource}
                  </span>
                )}
              </div>
              {!frozen && (
                <button
                  type="button"
                  onClick={addScene}
                  className="inline-flex items-center gap-1.5 rounded-md border border-pine-700/25 bg-white/70 px-3 py-1.5 text-xs font-medium text-pine-800 transition-colors hover:border-pine-700/40 hover:bg-white hover:text-pine-950"
                >
                  <Plus className="h-3.5 w-3.5" />
                  添加场景
                </button>
              )}
            </div>

            {scenes.length === 0 ? (
              <div className="flex h-40 items-center justify-center text-sm text-pine-600">
                暂无场景简报
              </div>
            ) : (
              <div className="space-y-4">
                {scenes.map((scene, sceneIndex) => (
                  <SceneBriefCard
                    key={scene.scene_id || sceneIndex}
                    scene={scene}
                    index={sceneIndex}
                    editing={!frozen}
                    source={sceneSource}
                    removable={!frozen && scenes.length > 1}
                    onChange={(field, value) => updateScene(sceneIndex, field, value)}
                    onRemove={() => removeScene(sceneIndex)}
                  />
                ))}
              </div>
            )}
          </div>
        )}

        {activeTab === "threads" && (
          <div className="space-y-3">
            {threadsLoading && (
              <div className="flex items-center gap-2 py-6 text-xs text-pine-700">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                正在加载关联线索...
              </div>
            )}
            {threadsError && (
              <div className="flex items-start gap-2 rounded-md border border-crimson-500/30 bg-crimson-500/10 p-3 text-xs text-crimson-300">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span>关联线索加载失败：{threadsError}</span>
              </div>
            )}
            {!threadsLoading && !threadsError && relatedThreads.length === 0 && (
              <p className="py-6 text-center text-xs text-pine-800">本章暂无关联线索</p>
            )}
            {!threadsLoading && !threadsError && relatedThreads.map((thread, index) => (
              <div
                key={String(thread.thread_id || index)}
                className="rounded-md border border-white/60 bg-white/50 backdrop-blur-sm p-3 shadow-sm"
              >
                <div className="flex items-center justify-between gap-3">
                  <p className="text-sm font-medium text-pine-600">
                    {String(thread.name || thread.thread_id || "未命名线索")}
                  </p>
                  <span className="text-xs text-magic-400">
                    {Array.isArray(thread.operations) && thread.operations.length > 0
                      ? thread.operations.map(String).join(" / ")
                      : String(thread.status || "关联")}
                  </span>
                </div>
                {thread.description ? (
                  <p className="mt-2 text-xs leading-5 text-pine-700">{String(thread.description)}</p>
                ) : null}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
