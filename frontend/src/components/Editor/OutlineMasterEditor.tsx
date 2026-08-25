import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { Save, Loader2, Lock, Eye, BookOpen, List, Layers, GitBranch, Plus, Trash2, FileEdit, Check, X, TrendingUp, ChevronDown, ChevronRight } from "lucide-react";
import { marked } from "marked";
import { sanitizeMarkdownHtml } from "@/utils/sanitizeMarkdownHtml";
import * as api from "@/api/client";
import OutlineVisualization from "@/components/Editor/OutlineVisualization";
import SceneBriefPanel from "@/components/Editor/SceneBriefPanel";
import ThreadPlanPanel from "@/components/Editor/ThreadPlanPanel";
import WorkspacePaneHeader from "@/components/Editor/WorkspacePaneHeader";
import clsx from "clsx";
import type { StoryConstitution, ChapterSpineItem } from "@/types";
import { formatChapterLabel } from "@/utils/chineseDisplay";

/* Hallmark · pre-emit critique: P4 H5 E4 S5 R5 V4 */
interface ChapterOutlineData {
  chapter_number: number;
  title: string;
  main_conflict: string;
  value_shift: string;
  pov_character?: string;
  thread_ops?: Array<{ op: string; thread_id: string }>;
  scenes: Array<{
    goal: string;
    conflict: string;
    outcome: string;
    info_release: string;
    hook: string;
  }>;
}

function formatValueShift(value: unknown): string {
  if (!value) return "";
  if (typeof value === "string") return value;
  if (typeof value === "object") {
    const obj = value as Record<string, unknown>;
    const axis = typeof obj.axis === "string" ? obj.axis : "";
    const from = typeof obj.from === "string" ? obj.from : "";
    const to = typeof obj.to === "string" ? obj.to : "";
    if (axis || from || to) {
      const body = [from, to].filter(Boolean).join(" -> ");
      return axis ? axis + ": " + body : body;
    }
    return JSON.stringify(value);
  }
  return String(value);
}

interface OutlineMasterEditorProps {
  projectId: string;
  outlineData: Record<string, unknown>;
  projectBrief?: {
    name: string;
    description: string;
    genre: string;
    wordCountTarget: number;
  };
  frozen: boolean;
  onSave: () => void;
  onSelectChapter: (chapterNumber: number) => void;
  refreshRevision?: number;
}

type DraftErrorPhase = "load" | "confirm" | "discard";

interface DraftActionError {
  phase: DraftErrorPhase;
  message: string;
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message.trim() ? error.message.trim() : fallback;
}

function outlineToMarkdown(chapters: ChapterOutlineData[]): string {
  const lines: string[] = ["# 大纲总文件", ""];

  for (const ch of chapters) {
    lines.push(`## ${formatChapterLabel(ch.chapter_number, ch.title || "")}`);
    lines.push("");
    lines.push(`- **核心冲突**: ${ch.main_conflict || ""}`);
    lines.push(`- **价值转变**: ${ch.value_shift || ""}`);
    if (ch.pov_character) {
      lines.push(`- **视角角色**: ${ch.pov_character}`);
    }
    if (ch.thread_ops && ch.thread_ops.length > 0) {
      const foreshadowStr = ch.thread_ops
        .map((f) => `${f.op === "plant" ? "埋设" : "揭示"} - ${f.thread_id}`)
        .join(" | ");
      lines.push(`- **伏笔**: ${foreshadowStr}`);
    }
    lines.push("");

    const scenes = Array.isArray(ch.scenes) ? ch.scenes : [];
    for (let si = 0; si < scenes.length; si++) {
      const scene = scenes[si];
      lines.push(`### 场景${si + 1}`);
      lines.push(`- **目标**: ${scene.goal || ""}`);
      lines.push(`- **冲突**: ${scene.conflict || ""}`);
      lines.push(`- **结果**: ${scene.outcome || ""}`);
      lines.push(`- **信息释放**: ${scene.info_release || ""}`);
      lines.push(`- **悬念钩子**: ${scene.hook || ""}`);
      lines.push("");
    }
  }

  return lines.join("\n");
}

function getOutlineChapters(outline: Record<string, unknown> | null | undefined): ChapterOutlineData[] {
  if (!outline) return [];
  const spine = Array.isArray(outline.chapter_spine)
    ? (outline.chapter_spine as Array<Record<string, unknown>>)
    : [];
  const legacyChapters = Array.isArray(outline.chapters)
    ? (outline.chapters as Array<Record<string, unknown>>)
    : [];

  // chapter_spine is the canonical source. Recovery drafts may intentionally
  // retain a sparse legacy `chapters` list for compatibility; choosing it first
  // hides recovered chapters and can crash when old rows have no `scenes`.
  if (spine.length === 0 && legacyChapters.length > 0) {
    return legacyChapters.map((item) => {
      const scenes = Array.isArray(item.scenes)
        ? (item.scenes as Array<Record<string, unknown>>)
        : [];
      return {
        chapter_number: Number(item.chapter_number || 0),
        title: String(item.title || ""),
        main_conflict: typeof item.main_conflict === "string"
          ? item.main_conflict
          : typeof item.conflict_text === "string"
            ? item.conflict_text
            : "",
        value_shift: formatValueShift(item.value_shift),
        pov_character: String(item.pov_character || ""),
        thread_ops: Array.isArray(item.thread_ops)
          ? (item.thread_ops as Array<Record<string, unknown>>).map((op) => ({
              op: String(op.op || ""),
              thread_id: String(op.thread_id || ""),
            }))
          : [],
        scenes: scenes.map((scene) => ({
          goal: String(scene.goal || ""),
          conflict: String(scene.conflict || ""),
          outcome: String(scene.outcome || ""),
          info_release: String(scene.info_release || ""),
          hook: String(scene.hook || ""),
        })),
      };
    });
  }

  const sceneBriefs =
    outline.scene_briefs && typeof outline.scene_briefs === "object"
      ? (outline.scene_briefs as Record<string, Record<string, unknown>>)
      : {};

  return spine.map((item) => {
    const chapterNumber = Number(item.chapter_number || 0);
    const chapterId = String(item.chapter_id || `ch_${String(chapterNumber).padStart(3, "0")}`);
    const brief =
      sceneBriefs[chapterId] ||
      sceneBriefs[String(chapterNumber)] ||
      Object.values(sceneBriefs).find((b) => b?.chapter_id === chapterId);
    const scenes = Array.isArray(brief?.scenes)
      ? (brief.scenes as Array<Record<string, unknown>>)
      : Array.isArray(item.scenes)
        ? (item.scenes as Array<Record<string, unknown>>)
        : [];

    return {
      chapter_number: chapterNumber,
      title: String(item.title || ""),
      main_conflict: String(item.conflict_text || ""),
      value_shift: formatValueShift(item.value_shift),
      pov_character: String(item.pov_character || ""),
      thread_ops: Array.isArray(item.thread_ops)
        ? (item.thread_ops as Array<Record<string, unknown>>).map((op) => ({
            op: String(op.op || ""),
            thread_id: String(op.thread_id || ""),
          }))
        : [],
      scenes: scenes.map((scene) => ({
        goal: String(scene.goal || ""),
        conflict: String(scene.conflict || ""),
        outcome: String(scene.outcome || ""),
        info_release: String(scene.info_release || ""),
        hook: String(scene.hook || ""),
      })),
    };
  });
}

export default function OutlineMasterEditor({
  projectId,
  outlineData,
  projectBrief,
  frozen,
  onSave,
  onSelectChapter,
  refreshRevision = 0,
}: OutlineMasterEditorProps) {
  const [chapters, setChapters] = useState<ChapterOutlineData[]>([]);
  const [markdown, setMarkdown] = useState("");
  const [showVisualization, setShowVisualization] = useState(false);
  const [activeTab, setActiveTab] = useState<"classic" | "constitution" | "spine" | "briefs" | "threads">("classic");
  const [planLoading, setPlanLoading] = useState(false);
  const [expandedChapter, setExpandedChapter] = useState<number | null>(null);
  const [constitutionData, setConstitutionData] = useState<Partial<StoryConstitution>>({});
  const [spineData, setSpineData] = useState<Partial<ChapterSpineItem>[]>([]);
  const [savingPlan, setSavingPlan] = useState(false);
  const [addingSpineItem, setAddingSpineItem] = useState(false);
  const [planActionError, setPlanActionError] = useState<string | null>(null);
  const [draftMode, setDraftMode] = useState(false);
  const [draftLoading, setDraftLoading] = useState(false);
  const [draftData, setDraftData] = useState<Record<string, unknown> | null>(null);
  const [draftDiff, setDraftDiff] = useState<{
    total_changes: number;
    change_types: string[];
    changes: Array<Record<string, unknown>>;
  } | null>(null);
  const [draftActionLoading, setDraftActionLoading] = useState<"confirm" | "discard" | null>(null);
  const [draftActionError, setDraftActionError] = useState<DraftActionError | null>(null);
  const [draftNotice, setDraftNotice] = useState<string | null>(null);
  const draftOperationId = useRef(0);

  useEffect(() => {
    // The same workspace component can survive a route-param change. Invalidate
    // late responses so one project's draft can never appear under another.
    draftOperationId.current += 1;
    setDraftMode(false);
    setDraftLoading(false);
    setDraftData(null);
    setDraftDiff(null);
    setDraftActionLoading(null);
    setDraftActionError(null);
    setDraftNotice(null);
  }, [projectId]);

  useEffect(() => {
    const chList = getOutlineChapters(outlineData);
    const normalized = chList.map((ch) => ({
      chapter_number: ch.chapter_number || 0,
      title: ch.title || "",
      main_conflict: ch.main_conflict || "",
      value_shift: formatValueShift(ch.value_shift),
      pov_character: ch.pov_character || "",
      thread_ops: ch.thread_ops || [],
      scenes: (ch.scenes || []).map((s) => ({
        goal: s.goal || "",
        conflict: s.conflict || "",
        outcome: s.outcome || "",
        info_release: s.info_release || "",
        hook: s.hook || "",
      })),
    }));
    setChapters(normalized);
    setMarkdown(outlineToMarkdown(normalized));
  }, [outlineData]);

  useEffect(() => {
    if (activeTab !== "classic") {
      setPlanLoading(true);
      api.getStoryPlan(projectId).then((plan) => {
        setConstitutionData(plan.story_constitution || {});
        setSpineData(plan.chapter_spine || []);
      }).catch((error) => {
        setPlanActionError((error as Error).message || "读取大纲结构失败");
      }).finally(() => setPlanLoading(false));
    }
  }, [activeTab, projectId, refreshRevision]);

  const loadDraft = useCallback(async () => {
    const operationId = ++draftOperationId.current;
    setDraftLoading(true);
    setDraftActionError(null);
    setDraftNotice(null);
    try {
      const result = await api.readDraftOutline(projectId);
      if (operationId !== draftOperationId.current) return;
      const validDraft = result.has_draft
        && result.draft
        && typeof result.draft === "object"
        && !Array.isArray(result.draft)
        && Object.keys(result.draft).length > 0
        ? result.draft
        : null;

      if (!validDraft) {
        setDraftData(null);
        setDraftDiff(null);
        setDraftMode(false);
        if (result.has_draft) {
          setDraftActionError({
            phase: "load",
            message: "草稿数据不完整，当前仍显示正式大纲。请重新加载；问题持续时请返回大纲构建师重新保存草稿。",
          });
        } else {
          setDraftNotice("当前没有待确认的草稿，仍显示正式大纲。");
        }
        return;
      }

      setDraftData(validDraft);
      setDraftDiff(result.diff_summary || null);
      setDraftMode(true);
      setActiveTab("classic");
      setShowVisualization(false);
    } catch (e) {
      if (operationId !== draftOperationId.current) return;
      setDraftMode(false);
      setDraftActionError({
        phase: "load",
        message: `草稿加载失败：${errorMessage(e, "连接暂时不可用")}。当前仍显示正式大纲。`,
      });
    } finally {
      if (operationId === draftOperationId.current) setDraftLoading(false);
    }
  }, [projectId]);

  const handleConfirmDraft = async () => {
    const operationId = ++draftOperationId.current;
    setDraftActionLoading("confirm");
    setDraftActionError(null);
    setDraftNotice(null);
    let confirmed = false;
    try {
      await api.confirmDraftOutline(projectId);
      confirmed = true;
    } catch (e) {
      if (operationId !== draftOperationId.current) return;
      setDraftActionError({
        phase: "confirm",
        message: `草稿确认失败：${errorMessage(e, "连接暂时不可用")}。正式大纲未发生变化，当前草稿仍保留。`,
      });
    } finally {
      if (operationId === draftOperationId.current) setDraftActionLoading(null);
    }
    if (!confirmed || operationId !== draftOperationId.current) return;
    setDraftData(null);
    setDraftDiff(null);
    setDraftMode(false);
    try {
      onSave();
    } catch {
      setDraftNotice("草稿已确认为正式大纲，但界面刷新失败。重新进入大纲页即可读取最新内容。");
    }
  };

  const handleDiscardDraft = async () => {
    const operationId = ++draftOperationId.current;
    setDraftActionLoading("discard");
    setDraftActionError(null);
    setDraftNotice(null);
    let discarded = false;
    try {
      await api.discardDraftOutline(projectId);
      discarded = true;
    } catch (e) {
      if (operationId !== draftOperationId.current) return;
      setDraftActionError({
        phase: "discard",
        message: `草稿丢弃失败：${errorMessage(e, "连接暂时不可用")}。正式大纲未发生变化，当前草稿仍保留。`,
      });
    } finally {
      if (operationId === draftOperationId.current) setDraftActionLoading(null);
    }
    if (!discarded || operationId !== draftOperationId.current) return;
    setDraftData(null);
    setDraftDiff(null);
    setDraftMode(false);
    try {
      onSave();
    } catch {
      setDraftNotice("草稿已丢弃，但界面刷新失败。重新进入大纲页即可读取正式大纲。");
    }
  };

  const foreshadowing = chapters.flatMap((ch) =>
    (ch.thread_ops || []).map((f) => ({
      id: `${ch.chapter_number}-${f.thread_id}`,
      name: f.thread_id,
      plant_chapter: f.op === "plant" ? ch.chapter_number : null,
      reveal_chapter: f.op === "reveal" ? ch.chapter_number : null,
      status: f.op === "reveal" ? "resolved" : "active",
    }))
  );

  const previewHtml = useMemo(() => {
    marked.setOptions({
      gfm: true,
      breaks: true,
    });
    return sanitizeMarkdownHtml(marked(markdown) as string);
  }, [markdown]);

  const draftChapters = useMemo(() => getOutlineChapters(draftData), [draftData]);
  const draftPreviewHtml = useMemo(() => {
    marked.setOptions({
      gfm: true,
      breaks: true,
    });
    return sanitizeMarkdownHtml(marked(outlineToMarkdown(draftChapters)) as string);
  }, [draftChapters]);

  const handlePreviewClick = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      const target = e.target as HTMLElement;
      const heading = target.closest("h2");
      if (heading) {
        const text = heading.textContent || "";
        const match = text.match(/第(\d+)章/);
        if (match) {
          onSelectChapter(parseInt(match[1], 10));
        }
      }
    },
    [onSelectChapter]
  );

  const renderOutlinePreview = (
    html: string,
    previewChapters: ChapterOutlineData[],
    versionLabel: string
  ) => {
    const sceneCount = previewChapters.reduce(
      (total, chapter) => total + (Array.isArray(chapter.scenes) ? chapter.scenes.length : 0),
      0
    );

    return (
      <div className="outline-preview-shell px-8 py-6" onClick={handlePreviewClick}>
        <header className="outline-preview-masthead">
          <span className="outline-preview-watermark" aria-hidden="true">
            {String(previewChapters.length).padStart(2, "0")}
          </span>
          <div className="outline-preview-meta">
            <span>{versionLabel}</span>
            <span>
              {previewChapters.length} 章{sceneCount > 0 ? ` · ${sceneCount} 个场景` : ""}
            </span>
          </div>
          <h1>大纲总文件</h1>
        </header>
        <div className="outline-preview" dangerouslySetInnerHTML={{ __html: html }} />
      </div>
    );
  };

  const renderProjectBrief = () => (
    <div className="flex min-h-full items-center justify-center px-6 py-10">
      <article className="w-full max-w-2xl">
        <header className="border-b border-[var(--border-emphasis)] pb-6">
          <p className="text-xs font-medium text-[var(--color-accent)]">作品起点</p>
          <h1 className="mt-3 min-w-0 [overflow-wrap:anywhere] text-3xl font-semibold leading-tight text-[var(--color-ink-strong)]">
            {projectBrief?.name || "未命名作品"}
          </h1>
          <p className="mt-2 text-sm leading-6 text-[var(--color-ink-muted)]">
            这里展示创建时的初始构想，仅供大纲架构师参考；正式剧情以确认后的故事宪法和大纲为准。
          </p>
        </header>

        <dl className="divide-y divide-[var(--border-subtle)] border-b border-[var(--border-subtle)]">
          <div className="grid gap-2 py-5 sm:grid-cols-[7rem_minmax(0,1fr)]">
            <dt className="text-xs font-medium text-[var(--color-ink-muted)]">故事种子</dt>
            <dd className="whitespace-pre-wrap text-sm leading-7 text-[var(--color-ink)]">
              {projectBrief?.description || "尚未填写。可以在右侧选择引导构建，让架构师从空白开始提问。"}
            </dd>
          </div>
          <div className="grid gap-2 py-5 sm:grid-cols-[7rem_minmax(0,1fr)]">
            <dt className="text-xs font-medium text-[var(--color-ink-muted)]">细分题材</dt>
            <dd className="text-sm text-[var(--color-ink)]">{projectBrief?.genre || "通用小说"}</dd>
          </div>
          <div className="grid gap-2 py-5 sm:grid-cols-[7rem_minmax(0,1fr)]">
            <dt className="text-xs font-medium text-[var(--color-ink-muted)]">目标总字数</dt>
            <dd className="text-sm tabular-nums text-[var(--color-ink)]">
              {(projectBrief?.wordCountTarget || 100000).toLocaleString("zh-CN")} 字
            </dd>
          </div>
        </dl>

        <p className="mt-5 text-xs leading-5 text-[var(--color-ink-muted)]">
          右侧发送消息或启动“引导构建”后才会调用 AI；生成结果会先进入上方的“草稿”区。
        </p>
      </article>
    </div>
  );

  const handleSaveConstitution = async () => {
    setSavingPlan(true);
    try {
      await api.updateStoryPlanLayer(projectId, "story_constitution", constitutionData as Record<string, unknown>);
      onSave();
    } catch (e) {
      console.error("保存故事宪法失败", e);
    } finally {
      setSavingPlan(false);
    }
  };

  const handleSaveSpineItem = async (chapterNumber: number, updates: Record<string, unknown>) => {
    setSavingPlan(true);
    setPlanActionError(null);
    try {
      await api.updateChapterSpineItem(projectId, chapterNumber, updates);
      setSpineData((prev) =>
        prev.map((item) =>
          item.chapter_number === chapterNumber
            ? { ...item, ...updates }
            : item
        )
      );
      onSave();
    } catch (e) {
      setPlanActionError((e as Error).message || "保存章节大纲失败");
    } finally {
      setSavingPlan(false);
    }
  };

  const handleAddSpineItem = async () => {
    if (addingSpineItem || frozen) return;
    setAddingSpineItem(true);
    setPlanActionError(null);
    try {
      const result = await api.appendChapterSpineItem(projectId);
      setSpineData((previous) => [
        ...previous,
        result.chapter as Partial<ChapterSpineItem>,
      ]);
      setExpandedChapter(result.chapter_number);
      onSave();
      onSelectChapter(result.chapter_number);
    } catch (error) {
      setPlanActionError((error as Error).message || "新增大纲章节失败");
    } finally {
      setAddingSpineItem(false);
    }
  };

  const handleDeleteSpineItem = async (chapterNumber: number) => {
    if (!window.confirm(`确定删除第 ${chapterNumber} 章的大纲吗？`)) return;
    setSavingPlan(true);
    setPlanActionError(null);
    try {
      await api.deleteChapterSpineItem(projectId, chapterNumber);
      setSpineData((prev) => prev.filter((item) => item.chapter_number !== chapterNumber));
      if (expandedChapter === chapterNumber) {
        setExpandedChapter(null);
      }
      onSave();
    } catch (e) {
      setPlanActionError((e as Error).message || "删除章节大纲失败");
    } finally {
      setSavingPlan(false);
    }
  };

  const buildConflictText = (cc: { desire?: string; obstacle?: string; action?: string; turn?: string } | null): string => {
    const parts = [
      cc?.desire || "",
      cc?.obstacle || "",
      cc?.action || "",
      cc?.turn || "",
    ].filter(Boolean);
    return parts.join("，但");
  };

  const TABS: Array<{ key: typeof activeTab; label: string; icon: React.ReactNode }> = [
    { key: "classic", label: "预览", icon: <Eye className="h-3.5 w-3.5" /> },
    { key: "constitution", label: "故事宪法", icon: <BookOpen className="h-3.5 w-3.5" /> },
    { key: "spine", label: "章节脊柱", icon: <List className="h-3.5 w-3.5" /> },
    { key: "briefs", label: "场景简报", icon: <Layers className="h-3.5 w-3.5" /> },
    { key: "threads", label: "剧情线", icon: <GitBranch className="h-3.5 w-3.5" /> },
  ];

  const renderConstitutionTab = () => {
    const constitution = constitutionData;
    const protagonistCore = constitution.protagonist_core || { desire: "", need: "", flaw: "", lie: "" };
    const readerPromises = Array.isArray(constitution.reader_promise)
      ? constitution.reader_promise
      : [];

    const updateProtagonistCore = (
      field: "desire" | "need" | "flaw" | "lie",
      value: string
    ) => {
      setConstitutionData((previous) => ({
        ...previous,
        protagonist_core: {
          ...(previous.protagonist_core || { desire: "", need: "", flaw: "", lie: "" }),
          [field]: value,
        },
      }));
    };

    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="min-h-0 flex-1 overflow-auto">
          <div className="mx-auto max-w-3xl px-6 pb-6 pt-5">
          <header className="relative overflow-hidden border-b border-[var(--border-emphasis)] pb-7 pt-2">
            <span
              className="pointer-events-none absolute right-1 top-[-24px] font-serif text-[112px] font-semibold leading-none text-pine-900/[0.04]"
              aria-hidden="true"
            >
              宪
            </span>
            <div className="relative z-10 flex items-center justify-between gap-4 text-xs text-pine-600">
              <span>创作总纲</span>
              <span>{readerPromises.length} 条读者期待</span>
            </div>
            <h2 className="relative z-10 mt-4 font-serif text-3xl font-semibold leading-tight text-pine-900">
              故事宪法
            </h2>
          </header>

          <section className="border-b border-[var(--border-subtle)] py-7">
            <div className="mb-3 flex items-center gap-3">
              <span className="font-serif text-2xl font-semibold text-pine-600">01</span>
              <div>
                <h3 className="text-sm font-semibold text-pine-900">核心命题</h3>
                <p className="mt-0.5 text-[11px] text-pine-500">故事一句话</p>
              </div>
            </div>
            <textarea
              value={typeof constitution.logline === "string" ? constitution.logline : ""}
              onChange={(event) =>
                setConstitutionData((previous) => ({ ...previous, logline: event.target.value }))
              }
              rows={4}
              className="ui-field resize-y px-4 py-3 font-serif text-base leading-8"
              placeholder="用一句话概括整个故事的核心"
            />
          </section>

          <section className="border-b border-[var(--border-subtle)] py-7">
            <div className="mb-4 flex items-center justify-between gap-4">
              <div className="flex items-center gap-3">
                <span className="font-serif text-2xl font-semibold text-pine-600">02</span>
                <div>
                  <h3 className="text-sm font-semibold text-pine-900">读者承诺</h3>
                  <p className="mt-0.5 text-[11px] text-pine-500">{readerPromises.length} 项</p>
                </div>
              </div>
              <button
                type="button"
                onClick={() =>
                  setConstitutionData((previous) => ({
                    ...previous,
                    reader_promise: [...readerPromises, ""],
                  }))
                }
                className="inline-flex items-center gap-1.5 rounded-md border border-pine-700/20 bg-white/70 px-2.5 py-1.5 text-xs font-medium text-pine-700 transition-colors hover:border-pine-700/35 hover:bg-white hover:text-pine-900"
              >
                <Plus className="h-3.5 w-3.5" />
                添加
              </button>
            </div>

            <div className="divide-y divide-pine-900/10 border-y border-pine-900/10">
              {readerPromises.map((item: string, index: number) => (
                <div key={index} className="grid grid-cols-[34px_minmax(0,1fr)_32px] items-center gap-2 py-2.5">
                  <span className="font-serif text-sm font-semibold tabular-nums text-pine-500">
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <input
                    type="text"
                    value={item}
                    onChange={(event) => {
                      const next = [...readerPromises];
                      next[index] = event.target.value;
                      setConstitutionData((previous) => ({ ...previous, reader_promise: next }));
                    }}
                    className="ui-field px-3 py-2 text-sm"
                    placeholder={`读者期待 ${index + 1}`}
                  />
                  <button
                    type="button"
                    onClick={() =>
                      setConstitutionData((previous) => ({
                        ...previous,
                        reader_promise: readerPromises.filter((_: string, itemIndex: number) => itemIndex !== index),
                      }))
                    }
                    title="删除读者期待"
                    className="inline-flex h-8 w-8 items-center justify-center rounded-md text-pine-500 transition-colors hover:bg-red-50 hover:text-red-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/20"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
              {readerPromises.length === 0 && (
                <div className="py-6 text-center text-xs text-pine-500">尚未设定读者承诺</div>
              )}
            </div>
          </section>

          <section className="border-b border-[var(--border-subtle)] py-7">
            <div className="mb-4 flex items-center gap-3">
              <span className="font-serif text-2xl font-semibold text-pine-600">03</span>
              <div>
                <h3 className="text-sm font-semibold text-pine-900">主角驱动力</h3>
                <p className="mt-0.5 text-[11px] text-pine-500">欲望、需要、缺陷与错误信念</p>
              </div>
            </div>
            <div className="grid grid-cols-1 gap-x-4 gap-y-3 sm:grid-cols-2">
              {(
                [
                  ["desire", "外在欲望", "主角想要什么"],
                  ["need", "内在需要", "主角真正需要什么"],
                  ["flaw", "核心缺陷", "主角的致命弱点"],
                  ["lie", "错误信念", "主角深信但错误的信念"],
                ] as const
              ).map(([field, label, placeholder]) => (
                <label key={field} className="min-w-0">
                  <span className="mb-1.5 block text-[11px] font-medium text-pine-700">{label}</span>
                  <input
                    type="text"
                    value={protagonistCore[field] || ""}
                    onChange={(event) => updateProtagonistCore(field, event.target.value)}
                    className="ui-field px-3 py-2 text-sm"
                    placeholder={placeholder}
                  />
                </label>
              ))}
            </div>
          </section>

          <section className="py-7">
            <div className="mb-4 flex items-center gap-3">
              <span className="font-serif text-2xl font-semibold text-pine-600">04</span>
              <div>
                <h3 className="text-sm font-semibold text-pine-900">叙事罗盘</h3>
                <p className="mt-0.5 text-[11px] text-pine-500">控制思想与结局方向</p>
              </div>
            </div>
            <div className="grid grid-cols-1 border-y border-pine-900/10 sm:grid-cols-2">
              <label className="min-w-0 py-4 sm:pr-4">
                <span className="mb-1.5 block text-[11px] font-medium text-pine-700">控制思想</span>
                <textarea
                  value={typeof constitution.controlling_idea === "string" ? constitution.controlling_idea : ""}
                  onChange={(event) =>
                    setConstitutionData((previous) => ({ ...previous, controlling_idea: event.target.value }))
                  }
                  rows={5}
                  className="ui-field resize-y px-3 py-2 text-sm leading-6"
                  placeholder="故事要传达的核心思想"
                />
              </label>
              <label className="min-w-0 border-t border-pine-900/10 py-4 sm:border-l sm:border-t-0 sm:pl-4">
                <span className="mb-1.5 block text-[11px] font-medium text-pine-700">结局方向</span>
                <textarea
                  value={typeof constitution.ending_direction === "string" ? constitution.ending_direction : ""}
                  onChange={(event) =>
                    setConstitutionData((previous) => ({ ...previous, ending_direction: event.target.value }))
                  }
                  rows={5}
                  className="ui-field resize-y px-3 py-2 text-sm leading-6"
                  placeholder="故事结局的走向和情感基调"
                />
              </label>
            </div>
          </section>
          </div>
        </div>

        <div className="z-20 flex shrink-0 items-center justify-end border-t border-[var(--workspace-border)] bg-[var(--workspace-chrome)] px-6 py-3">
          <button
            type="button"
            onClick={handleSaveConstitution}
            disabled={savingPlan}
            className="inline-flex items-center gap-1.5 rounded-md bg-pine-700 px-4 py-2 text-xs font-medium text-white transition-colors hover:bg-pine-800 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {savingPlan ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
            保存故事宪法
          </button>
        </div>
      </div>
    );
  };

  const renderChapterSpineDetailPanel = (item: Partial<ChapterSpineItem>) => {
    const spineItem = item;
    const chNum = spineItem.chapter_number ?? 0;
    const coreConflict = spineItem.core_conflict || { desire: "", obstacle: "", action: "", turn: "" };
    const valueShift = (spineItem.value_shift && typeof spineItem.value_shift === "object" ? spineItem.value_shift : { axis: "", from: "", to: "" }) as { axis: string; from: string; to: string };
    const latestChapterNumber = Math.max(
      0,
      ...spineData.map((chapter) => chapter.chapter_number || 0),
    );
    const canDelete = chNum > 0 && chNum === latestChapterNumber && !frozen;

    return (
      <div className="space-y-4 border-t border-[var(--workspace-border)] bg-[var(--surface-tool)] px-5 py-4">
        <div>
          <label className="block text-xs font-medium text-pine-900 mb-2">核心冲突四元素</label>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-[11px] text-pine-800 font-medium mb-1">欲望 (Desire)</label>
              <textarea
                value={coreConflict.desire || ""}
                onChange={(e) => {
                  const newCc = { ...coreConflict, desire: e.target.value };
                  const updates: Record<string, unknown> = { core_conflict: newCc, conflict_text: buildConflictText(newCc) };
                  handleSaveSpineItem(chNum, updates);
                }}
                rows={2}
                className="w-full rounded-lg border border-white/60 bg-white/50 backdrop-blur-sm px-3 py-2 text-xs text-pine-950 outline-none transition-colors focus:border-magic-400 focus:bg-white/80 placeholder:text-pine-700 shadow-sm resize-none"
              />
            </div>
            <div>
              <label className="block text-[11px] text-pine-800 font-medium mb-1">障碍 (Obstacle)</label>
              <textarea
                value={coreConflict.obstacle || ""}
                onChange={(e) => {
                  const newCc = { ...coreConflict, obstacle: e.target.value };
                  const updates: Record<string, unknown> = { core_conflict: newCc, conflict_text: buildConflictText(newCc) };
                  handleSaveSpineItem(chNum, updates);
                }}
                rows={2}
                className="w-full rounded-lg border border-white/60 bg-white/50 backdrop-blur-sm px-3 py-2 text-xs text-pine-950 outline-none transition-colors focus:border-magic-400 focus:bg-white/80 placeholder:text-pine-700 shadow-sm resize-none"
              />
            </div>
            <div>
              <label className="block text-[11px] text-pine-800 font-medium mb-1">行动 (Action)</label>
              <textarea
                value={coreConflict.action || ""}
                onChange={(e) => {
                  const newCc = { ...coreConflict, action: e.target.value };
                  const updates: Record<string, unknown> = { core_conflict: newCc, conflict_text: buildConflictText(newCc) };
                  handleSaveSpineItem(chNum, updates);
                }}
                rows={2}
                className="w-full rounded-lg border border-white/60 bg-white/50 backdrop-blur-sm px-3 py-2 text-xs text-pine-950 outline-none transition-colors focus:border-magic-400 focus:bg-white/80 placeholder:text-pine-700 shadow-sm resize-none"
              />
            </div>
            <div>
              <label className="block text-[11px] text-pine-800 font-medium mb-1">转折 (Turn)</label>
              <textarea
                value={coreConflict.turn || ""}
                onChange={(e) => {
                  const newCc = { ...coreConflict, turn: e.target.value };
                  const updates: Record<string, unknown> = { core_conflict: newCc, conflict_text: buildConflictText(newCc) };
                  handleSaveSpineItem(chNum, updates);
                }}
                rows={2}
                className="w-full rounded-lg border border-white/60 bg-white/50 backdrop-blur-sm px-3 py-2 text-xs text-pine-950 outline-none transition-colors focus:border-magic-400 focus:bg-white/80 placeholder:text-pine-700 shadow-sm resize-none"
              />
            </div>
          </div>
        </div>

        <div>
          <label className="block text-xs font-medium text-pine-900 mb-1.5">冲突文本</label>
          <textarea
            value={spineItem.conflict_text || ""}
            onChange={(e) => handleSaveSpineItem(chNum, { conflict_text: e.target.value })}
            rows={2}
            className="w-full rounded-lg border border-white/60 bg-white/50 backdrop-blur-sm px-3 py-2 text-xs text-pine-950 outline-none transition-colors focus:border-magic-400 focus:bg-white/80 placeholder:text-pine-700 shadow-sm resize-none"
          />
        </div>

        <div>
          <label className="block text-xs font-medium text-pine-900 mb-2">价值转变</label>
          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="block text-[11px] text-pine-800 font-medium mb-1">轴(Axis)</label>
              <input
                type="text"
                value={valueShift.axis || ""}
                onChange={(e) =>
                  handleSaveSpineItem(chNum, {
                    value_shift: { ...valueShift, axis: e.target.value },
                  })
                }
                className="w-full rounded-lg border border-white/60 bg-white/50 backdrop-blur-sm px-3 py-2 text-xs text-pine-950 outline-none transition-colors focus:border-magic-400 focus:bg-white/80 placeholder:text-pine-700 shadow-sm"
              />
            </div>
            <div>
              <label className="block text-[11px] text-pine-800 font-medium mb-1">从(From)</label>
              <input
                type="text"
                value={valueShift.from || ""}
                onChange={(e) =>
                  handleSaveSpineItem(chNum, {
                    value_shift: { ...valueShift, from: e.target.value },
                  })
                }
                className="w-full rounded-lg border border-white/60 bg-white/50 backdrop-blur-sm px-3 py-2 text-xs text-pine-950 outline-none transition-colors focus:border-magic-400 focus:bg-white/80 placeholder:text-pine-700 shadow-sm"
              />
            </div>
            <div>
              <label className="block text-[11px] text-pine-800 font-medium mb-1">到(To)</label>
              <input
                type="text"
                value={valueShift.to || ""}
                onChange={(e) =>
                  handleSaveSpineItem(chNum, {
                    value_shift: { ...valueShift, to: e.target.value },
                  })
                }
                className="w-full rounded-lg border border-white/60 bg-white/50 backdrop-blur-sm px-3 py-2 text-xs text-pine-950 outline-none transition-colors focus:border-magic-400 focus:bg-white/80 placeholder:text-pine-700 shadow-sm"
              />
            </div>
          </div>
        </div>

        {Array.isArray(spineItem.thread_ops) && spineItem.thread_ops.length > 0 && (
          <div>
            <label className="block text-xs font-medium text-pine-900 mb-1.5">剧情线操作</label>
            <div className="flex flex-wrap gap-1.5">
              {spineItem.thread_ops.map((op, idx) => (
                <span key={idx} className="inline-flex items-center gap-1 rounded-md bg-white/60 px-2 py-0.5 text-[11px] text-pine-600">
                  <span className="text-magic-400">{op.thread_id}</span>
                  <span>{op.op}</span>
                  <span className="text-pine-700">({op.mode})</span>
                </span>
              ))}
            </div>
          </div>
        )}

        {Array.isArray(spineItem.depends_on) && spineItem.depends_on.length > 0 && (
          <div>
            <label className="block text-xs font-medium text-pine-900 mb-1.5">依赖</label>
            <div className="flex flex-wrap gap-1.5">
              {spineItem.depends_on.map((dep, idx) => (
                <span key={idx} className="inline-flex items-center rounded-md bg-white/60 px-2 py-0.5 text-[11px] text-pine-600">
                  {dep}
                </span>
              ))}
            </div>
          </div>
        )}

        {Array.isArray(spineItem.must_not) && spineItem.must_not.length > 0 && (
          <div>
            <label className="block text-xs font-medium text-pine-900 mb-1.5">禁忌</label>
            <div className="flex flex-wrap gap-1.5">
              {spineItem.must_not.map((mn, idx) => (
                <span key={idx} className="inline-flex items-center rounded-md bg-red-50 text-red-600 border border-red-100">
                  {mn}
                </span>
              ))}
            </div>
          </div>
        )}

        <div className="flex items-center justify-between gap-3 border-t border-white/40 pt-3">
          <button
            type="button"
            disabled={savingPlan || !canDelete}
            onClick={() => handleDeleteSpineItem(chNum)}
            title={frozen ? "大纲已冻结" : canDelete ? "删除末章大纲" : `请先删除第 ${latestChapterNumber} 章大纲`}
            className="flex items-center gap-1.5 whitespace-nowrap rounded-md border border-[var(--color-error)] px-3 py-1.5 text-xs text-[var(--color-error)] transition-colors hover:bg-[var(--color-error-soft)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)] active:bg-[var(--color-error-soft)] disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Trash2 className="h-3.5 w-3.5" />
            删除此章大纲
          </button>
          <div className="flex items-center gap-2">
          <span className="text-xs text-pine-700">状态：</span>
          <span
            className={clsx(
              "inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-medium",
              spineItem.status === "frozen"
                ? "bg-red-50 text-red-600 border border-red-100"
                : "bg-white/80 text-pine-900 border border-white"
            )}
          >
            {spineItem.status === "frozen" ? "已冻结" : "草稿"}
          </span>
          </div>
        </div>
      </div>
    );
  };

  const renderChapterSpineTab = () => {
    const spine = spineData;
    const frozenCount = spine.filter((item) => item.status === "frozen").length;
    const hookCount = spine.filter((item) => Boolean(item.hook)).length;

    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="flex shrink-0 items-center justify-between gap-4 border-b border-[var(--workspace-border)] bg-[var(--workspace-chrome)] px-5 py-3">
          <div className="flex min-w-0 items-center gap-2">
            <List className="h-4 w-4 shrink-0 text-pine-700" />
            <h3 className="text-sm font-semibold text-pine-900">章节脊柱</h3>
          </div>
          <div className="flex shrink-0 items-center gap-3">
            <div className="hidden items-center gap-3 text-[11px] text-pine-600 xl:flex">
              <span>共 <strong className="font-semibold text-pine-900">{spine.length}</strong> 章</span>
              <span>已冻结 <strong className="font-semibold text-pine-900">{frozenCount}</strong></span>
              <span>有钩子 <strong className="font-semibold text-pine-900">{hookCount}</strong></span>
            </div>
            <button
              type="button"
              onClick={() => void handleAddSpineItem()}
              disabled={addingSpineItem || frozen}
              className="inline-flex min-h-8 items-center gap-1.5 whitespace-nowrap rounded-md bg-[var(--color-accent)] px-3 text-xs font-semibold text-[var(--color-on-accent)] transition-colors hover:bg-[var(--color-accent-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)] active:bg-[var(--color-accent-hover)] disabled:cursor-not-allowed disabled:opacity-50"
            >
              {addingSpineItem ? <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" /> : <Plus className="h-3.5 w-3.5" />}
              {addingSpineItem ? "添加中" : "添加章节"}
            </button>
          </div>
        </div>

        {planActionError && (
          <div className="mx-4 mt-3 rounded-md border border-[var(--color-error)] bg-[var(--color-error-soft)] px-3 py-2 text-xs leading-5 text-[var(--color-error)]" role="alert">
            {planActionError}
          </div>
        )}

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
          {spine.length === 0 && !planLoading ? (
            <div className="flex h-40 items-center justify-center text-sm text-pine-600">
              暂无章节脊柱数据
            </div>
          ) : (
            <div className="divide-y divide-pine-900/10 border-y border-pine-900/10">
              {spine.map((item) => {
                const chapterNumber = item.chapter_number ?? 0;
                const isExpanded = expandedChapter === chapterNumber;
                const conflict = item.conflict_text || (
                  item.core_conflict ? buildConflictText(item.core_conflict) : "尚未设定核心冲突"
                );
                const valueShiftText = formatValueShift(item.value_shift) || "未设定";

                return (
                  <section key={chapterNumber} className={clsx(isExpanded && "bg-pine-900/[0.025]")}>
                    <button
                      type="button"
                      onClick={() => setExpandedChapter(isExpanded ? null : chapterNumber)}
                      className="grid w-full grid-cols-[46px_minmax(0,1fr)_24px] gap-3 px-2 py-4 text-left transition-colors hover:bg-pine-900/[0.025]"
                    >
                      <span className="pt-0.5 font-serif text-2xl font-semibold tabular-nums text-pine-600">
                        {String(chapterNumber).padStart(2, "0")}
                      </span>
                      <span className="min-w-0">
                        <span className="flex min-w-0 items-center gap-2">
                          <span className="min-w-0 flex-1 truncate text-sm font-semibold text-pine-900">
                            {item.title || `第${chapterNumber}章`}
                          </span>
                          <span
                            className={clsx(
                              "shrink-0 rounded-md border px-1.5 py-0.5 text-[10px] font-medium",
                              item.status === "frozen"
                                ? "border-red-200 bg-red-50 text-red-600"
                                : "border-pine-900/10 bg-white/70 text-pine-600"
                            )}
                          >
                            {item.status === "frozen" ? "已冻结" : "草稿"}
                          </span>
                        </span>

                        <span className="mt-2 grid grid-cols-[3px_minmax(0,1fr)] gap-2.5">
                          <span className="bg-pine-500" aria-hidden="true" />
                          <span className="min-w-0">
                            <span className="block text-[10px] font-medium text-pine-500">核心冲突</span>
                            <span className="mt-0.5 line-clamp-2 text-xs leading-5 text-pine-800">
                              {conflict}
                            </span>
                          </span>
                        </span>

                        <span className="mt-3 flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-pine-600">
                          <span className="min-w-0 max-w-full truncate">
                            <strong className="font-medium text-pine-500">价值转变：</strong>{valueShiftText}
                          </span>
                          <span className="shrink-0">
                            <strong className="font-medium text-pine-500">POV：</strong>{item.pov_character || "未设定"}
                          </span>
                          <span className="min-w-0 max-w-full truncate">
                            <strong className="font-medium text-pine-500">钩子：</strong>{item.hook || "未设定"}
                          </span>
                        </span>
                      </span>
                      <span className="flex h-7 w-7 items-center justify-center self-center rounded-md text-pine-500">
                        {isExpanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                      </span>
                    </button>
                    {isExpanded && renderChapterSpineDetailPanel(item)}
                  </section>
                );
              })}
            </div>
          )}
        </div>
      </div>
    );
  };

  return (
    <div className="outline-workspace flex h-full flex-col">
      <WorkspacePaneHeader className="gap-2 px-2">
        <div className="workspace-tab-strip min-w-0 flex-1 self-stretch overflow-x-auto">
          {!draftMode ? (
            <div className="flex h-full min-w-max items-center">
              {TABS.map((tab) => {
                const isActive = activeTab === tab.key;
                return (
                  <button
                    key={tab.key}
                    onClick={() => setActiveTab(tab.key)}
                    className={clsx(
                      "group relative flex h-full shrink-0 items-center gap-1 px-2.5 text-xs font-medium transition-colors",
                      isActive ? "text-pine-950" : "text-pine-600 hover:text-pine-900"
                    )}
                  >
                    <span
                      className={clsx(
                        "transition-colors",
                        isActive ? "text-magic-500" : "text-pine-400 group-hover:text-pine-600"
                      )}
                    >
                      {tab.icon}
                    </span>
                    {tab.label}
                    <span
                      className={clsx(
                        "absolute inset-x-2.5 bottom-0 h-0.5 bg-magic-500 transition-transform duration-200",
                        isActive ? "scale-x-100" : "scale-x-0"
                      )}
                    />
                  </button>
                );
              })}
            </div>
          ) : (
            <div className="flex h-full items-center gap-2 px-3 text-xs font-medium text-pine-800">
              <FileEdit className="h-3.5 w-3.5 text-magic-500" />
              草稿预览
            </div>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-1.5 border-l border-pine-900/10 pl-2">
          <div
            className="flex items-center rounded-md border border-pine-900/10 bg-pine-900/[0.04] p-0.5"
            role="group"
            aria-label="大纲版本"
          >
            <button
              onClick={() => {
                setDraftMode(false);
                setDraftNotice(null);
              }}
              disabled={draftActionLoading !== null}
              className={clsx(
                "rounded-[5px] px-2.5 py-1 text-[11px] font-medium transition-colors disabled:opacity-50",
                !draftMode
                  ? "bg-white text-pine-950"
                  : "text-pine-600 hover:text-pine-900"
              )}
            >
              正式
            </button>
            <button
              onClick={loadDraft}
              disabled={draftLoading || draftActionLoading !== null}
              className={clsx(
                "flex items-center gap-1 rounded-[5px] px-2.5 py-1 text-[11px] font-medium transition-colors disabled:opacity-50",
                draftMode
                  ? "bg-white text-pine-950"
                  : "text-pine-600 hover:text-pine-900"
              )}
            >
              {draftLoading && <Loader2 className="h-3 w-3 animate-spin" />}
              草稿
            </button>
          </div>

          {frozen && (
            <span
              className="inline-flex h-7 items-center gap-1 rounded-md border border-[#dfc172]/60 bg-[#f5edd7] px-2 text-[11px] font-medium text-[#75522b]"
              title="当前大纲已冻结"
            >
              <Lock className="h-3 w-3" />
              <span className="hidden min-[1700px]:inline">已冻结</span>
            </span>
          )}

          {activeTab === "classic" && !draftMode && (
            <button
              onClick={() => setShowVisualization(!showVisualization)}
              className={clsx(
                "inline-flex h-8 items-center gap-1.5 rounded-md px-2 text-xs font-medium transition-colors",
                showVisualization
                  ? "bg-magic-100/80 text-magic-700"
                  : "text-pine-600 hover:bg-pine-900/[0.05] hover:text-pine-950"
              )}
              title={showVisualization ? "隐藏价值曲线" : "显示价值曲线"}
            >
              <TrendingUp className="h-3.5 w-3.5" />
              <span className="hidden min-[1650px]:inline">
                {showVisualization ? "隐藏图表" : "价值曲线"}
              </span>
            </button>
          )}
        </div>
      </WorkspacePaneHeader>

      {!draftMode && (draftActionError || draftNotice) && (
        <div
          className={clsx(
            "mx-5 mt-3 flex items-start justify-between gap-3 rounded-md border px-3 py-2 text-xs leading-5",
            draftActionError
              ? "border-[var(--color-error)] bg-[var(--color-error-soft)] text-[var(--color-error)]"
              : "border-pine-200/60 bg-white/50 text-pine-700"
          )}
          role={draftActionError ? "alert" : "status"}
        >
          <span>{draftActionError?.message || draftNotice}</span>
          {draftActionError?.phase === "load" && (
            <button
              type="button"
              onClick={loadDraft}
              disabled={draftLoading}
              className="shrink-0 font-medium underline underline-offset-2 disabled:opacity-50"
            >
              {draftLoading ? "正在重试" : "重试加载"}
            </button>
          )}
        </div>
      )}

      {!draftMode && activeTab === "classic" && showVisualization && chapters.length > 0 && (
        <div className="border-b border-pine-200/40 px-5 py-4">
          <OutlineVisualization
            chapters={chapters.map((ch) => ({
              chapter_number: ch.chapter_number,
              title: ch.title,
              conflict: ch.main_conflict,
              value_shift: ch.value_shift,
              scenes: ch.scenes,
            }))}
            foreshadowing={foreshadowing}
          />
        </div>
      )}

      {draftMode ? (
        <div className="flex-1 min-h-0 overflow-auto">
          <div className="border-b border-white/40 bg-magic-50/50 px-5 py-3">
            <div className="flex items-center justify-between gap-3">
              <div>
                <div className="flex items-center gap-2 text-sm font-medium text-magic-300">
                  <FileEdit className="h-4 w-4" />
                  大纲修改草稿
                </div>
                <p className="mt-1 text-xs text-pine-700">
                  草稿不会覆盖正式大纲。保存后会成为唯一的大纲总文件，之后可在自由对话模式继续精细化修改。                </p>
                {draftDiff && (
                  <p className="mt-1 text-xs text-pine-700">
                    与当前正式大纲相比约 {draftDiff.total_changes} 处变化                  </p>
                )}
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <button
                  onClick={handleConfirmDraft}
                  disabled={!draftData || draftActionLoading !== null}
                  className="flex items-center gap-1.5 rounded-lg bg-magic-500 px-3 py-1.5 text-xs font-medium text-pine-950 transition-colors hover:bg-magic-400 disabled:opacity-50"
                >
                  {draftActionLoading === "confirm" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
                  保存为正式大纲                </button>
                <button
                  onClick={handleDiscardDraft}
                  disabled={!draftData || draftActionLoading !== null}
                  className="flex items-center gap-1.5 rounded-lg border border-pine-200 bg-white/30 px-3 py-1.5 text-xs text-pine-600 transition-colors hover:bg-white/50 hover:text-pine-500 disabled:opacity-50"
                >
                  {draftActionLoading === "discard" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <X className="h-3.5 w-3.5" />}
                  丢弃草稿
                </button>
              </div>
            </div>
            {draftActionError && (
              <div
                className="mt-3 rounded-md border border-[var(--color-error)] bg-[var(--color-error-soft)] px-3 py-2 text-xs leading-5 text-[var(--color-error)]"
                role="alert"
              >
                {draftActionError.message}
              </div>
            )}
          </div>

          {draftLoading ? (
            <div className="flex h-full items-center justify-center text-sm text-pine-700">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              正在加载草稿...
            </div>
          ) : draftData ? (
            renderOutlinePreview(draftPreviewHtml, draftChapters, "草稿大纲")
          ) : (
            <div className="flex h-full items-center justify-center text-sm text-pine-700">
              当前没有待保存的草稿
            </div>
          )}
        </div>
      ) : planLoading && activeTab !== "classic" ? (
        <div className="flex flex-1 items-center justify-center">
          <Loader2 className="h-5 w-5 animate-spin text-pine-700" />
        </div>
      ) : activeTab === "classic" ? (
        <div className="flex-1 min-h-0 overflow-auto">
          {chapters.length > 0
            ? renderOutlinePreview(previewHtml, chapters, "正式大纲")
            : renderProjectBrief()}
        </div>
      ) : activeTab === "constitution" ? (
        renderConstitutionTab()
      ) : activeTab === "spine" ? (
        renderChapterSpineTab()
      ) : activeTab === "briefs" ? (
        <SceneBriefPanel key={`briefs-${refreshRevision}`} projectId={projectId} spineData={spineData} />
      ) : (
        <ThreadPlanPanel key={`threads-${refreshRevision}`} projectId={projectId} spineData={spineData} />
      )}

      <style>{`
        .outline-preview-shell {
          container-type: inline-size;
          max-width: 780px;
          margin: 0 auto;
        }

        .outline-preview-masthead {
          position: relative;
          min-height: 150px;
          overflow: hidden;
          border-bottom: 1px solid var(--border-emphasis);
          padding: 18px 4px 34px;
        }

        .outline-preview-masthead h1 {
          position: relative;
          z-index: 1;
          max-width: 75%;
          margin: 28px 0 0;
          color: var(--color-ink-strong);
          font-family: var(--font-serif, Georgia, serif);
          font-size: 32px;
          font-style: normal;
          font-weight: 650;
          line-height: 1.25;
          overflow-wrap: anywhere;
        }

        .outline-preview-meta {
          position: relative;
          z-index: 1;
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 16px;
          color: var(--color-ink-muted);
          font-size: 12px;
          font-weight: 500;
        }

        .outline-preview-watermark {
          position: absolute;
          top: -20px;
          right: 2px;
          color: color-mix(in srgb, var(--color-ink-strong) 5%, transparent);
          font-family: var(--font-serif, Georgia, serif);
          font-size: 112px;
          font-weight: 650;
          line-height: 1;
          pointer-events: none;
        }

        .outline-preview {
          counter-reset: outline-chapter;
          color: var(--color-ink);
          font-size: 14px;
          line-height: 1.8;
        }

        .outline-preview > h1 {
          display: none;
        }

        .outline-preview h2 {
          counter-increment: outline-chapter;
          position: relative;
          min-height: 74px;
          margin: 0;
          padding: 27px 48px 14px 62px;
          background: transparent;
          border-bottom: 1px solid var(--border-subtle);
          color: var(--color-ink-strong);
          cursor: pointer;
          font-family: var(--font-serif, Georgia, serif);
          font-size: 20px;
          font-style: normal;
          font-weight: 650;
          line-height: 1.35;
          overflow-wrap: anywhere;
          transition:
            color var(--motion-fast) var(--ease-ui),
            border-color var(--motion-fast) var(--ease-ui);
        }

        .outline-preview h2::before {
          content: counter(outline-chapter, decimal-leading-zero);
          position: absolute;
          left: 4px;
          top: 25px;
          color: var(--color-accent);
          font-family: var(--font-serif, Georgia, serif);
          font-size: 25px;
          font-weight: 650;
          line-height: 1;
        }

        .outline-preview h2::after {
          content: '\\203A';
          position: absolute;
          right: 4px;
          top: 27px;
          color: var(--color-ink-muted);
          font-family: sans-serif;
          font-size: 22px;
          font-weight: 400;
          line-height: 1;
        }

        .outline-preview h2:hover {
          color: var(--color-accent-hover);
          border-bottom-color: var(--border-emphasis);
        }

        .outline-preview h2 + ul {
          display: grid;
          grid-template-columns: minmax(0, 2fr) minmax(150px, 1fr);
          margin: 0;
          padding: 0 0 32px 62px;
          border-bottom: 1px solid var(--border-subtle);
        }

        .outline-preview h2 + ul > li {
          position: relative;
          min-width: 0;
          padding: 14px 18px;
          color: var(--color-ink);
          overflow-wrap: anywhere;
        }

        .outline-preview h2 + ul > li:first-child {
          grid-column: 1 / -1;
          margin: 0 0 2px;
          border-left: 3px solid var(--color-accent);
          background: color-mix(in srgb, var(--surface-tool) 55%, transparent);
          color: var(--color-ink-strong);
          font-size: 15px;
          line-height: 1.9;
        }

        .outline-preview h2 + ul > li:nth-child(3) {
          border-left: 1px solid var(--border-subtle);
        }

        .outline-preview h2 + ul > li::before {
          content: none;
        }

        .outline-preview h3 {
          margin: 18px 0 8px 62px;
          padding: 0;
          color: var(--color-accent);
          font-size: 13px;
          font-weight: 600;
        }

        .outline-preview ul {
          list-style: none;
          padding-left: 0;
          margin: 6px 0 16px 62px;
        }

        .outline-preview li {
          position: relative;
          padding: 3px 0 3px 14px;
        }

        .outline-preview li::before {
          content: '';
          position: absolute;
          left: 0;
          top: 11px;
          width: 4px;
          height: 4px;
          border-radius: 50%;
          background: var(--color-accent-soft);
        }

        .outline-preview strong {
          margin-right: 5px;
          color: var(--color-ink-muted);
          font-size: 11px;
          font-weight: 600;
        }

        .outline-preview p {
          margin: 8px 0;
        }

        @container (max-width: 560px) {
          .outline-preview-masthead {
            min-height: 132px;
          }

          .outline-preview-masthead h1 {
            max-width: 82%;
            font-size: 27px;
          }

          .outline-preview h2 {
            padding-left: 52px;
          }

          .outline-preview h2 + ul {
            grid-template-columns: minmax(0, 1fr);
            padding-left: 52px;
          }

          .outline-preview h2 + ul > li:nth-child(3) {
            border-left: 0;
            border-top: 1px solid var(--border-subtle);
          }

          .outline-preview h3,
          .outline-preview ul {
            margin-left: 52px;
          }
        }
      `}</style>
    </div>
  );
}
