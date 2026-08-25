import { useEffect, useState } from "react";
import { useParams, Outlet, useNavigate, useLocation } from "react-router-dom";
import { motion } from "framer-motion";
import {
  ChevronRight,
  ChevronDown,
  FileText,
  Trash2,
  Lock,
  ScrollText,
  BookOpen,
  Plus,
  Clock,
  FileEdit,
  Loader2,
} from "lucide-react";
import { useProjectStore } from "@/stores/projectStore";
import * as api from "@/api/client";
import type { OutlineStatus } from "@/types";
import Modal from "@/components/Common/Modal";
import Toast from "@/components/Common/Toast";
import ExportPanel from "@/components/Common/ExportPanel";
import OutlineDesigner from "@/components/Editor/OutlineDesigner";
import OutlineMasterEditor from "@/components/Editor/OutlineMasterEditor";
import ChapterOutlineDetail from "@/components/Editor/ChapterOutlineDetail";
import ChatErrorBoundary from "@/components/Chat/ChatErrorBoundary";
import { GlassButton } from "@/components/UI/GlassButton";
import clsx from "clsx";
import ProjectDashboard from "./ProjectDashboard";
import { formatChapterLabel } from "@/utils/chineseDisplay";

/* Hallmark · pre-emit critique: P5 H5 E4 S5 R5 V4 · outline chapter controls */

interface OutlineChapter {
  chapter_number: number;
  title: string;
  main_conflict?: string;
  value_shift?: string;
}

type OutlineNavItem =
  | { kind: "chapter"; chapter: OutlineChapter }
  | { kind: "gap"; start: number; end: number };

function formatValueShift(value: unknown): string {
  if (typeof value === "string") return value;
  if (!value || typeof value !== "object") return "";
  const record = value as Record<string, unknown>;
  const from = String(record.from || "").trim();
  const to = String(record.to || "").trim();
  const axis = String(record.axis || record.dimension || "").trim();
  const change = [from, to].filter(Boolean).join(" → ");
  return axis && change ? `${axis}：${change}` : change;
}

function getOutlineChapters(outlineData: Record<string, unknown>): OutlineChapter[] {
  const spine = outlineData.chapter_spine;
  if (Array.isArray(spine) && spine.length > 0) {
    return (spine as Array<Record<string, unknown>>).map((item) => ({
      chapter_number: Number(item.chapter_number || 0),
      title: String(item.title || ""),
      main_conflict: String(item.conflict_text || ""),
      value_shift: formatValueShift(item.value_shift),
    }));
  }
  const chapters = outlineData.chapters;
  return Array.isArray(chapters) ? chapters as OutlineChapter[] : [];
}

function buildOutlineNavItems(chapters: OutlineChapter[]): OutlineNavItem[] {
  const sorted = [...chapters]
    .filter((chapter) => Number.isFinite(chapter.chapter_number) && chapter.chapter_number > 0)
    .sort((a, b) => a.chapter_number - b.chapter_number);
  const items: OutlineNavItem[] = [];

  sorted.forEach((chapter, index) => {
    const previous = sorted[index - 1];
    if (!previous && chapter.chapter_number > 1) {
      items.push({ kind: "gap", start: 1, end: chapter.chapter_number - 1 });
    } else if (previous && chapter.chapter_number - previous.chapter_number > 1) {
      items.push({
        kind: "gap",
        start: previous.chapter_number + 1,
        end: chapter.chapter_number - 1,
      });
    }
    items.push({ kind: "chapter", chapter });
  });

  return items;
}

export default function ProjectWorkspace() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const isDashboard = id ? (location.pathname === `/project/${id}` || location.pathname === `/project/${id}/`) && !location.search.includes('outline') : false;
  const hideChapterSidebar = ["/worldbuilding", "/style", "/state", "/intelligence", "/health", "/foreshadowing"].some(
    (seg) => location.pathname.endsWith(seg)
  ) || isDashboard;
  const {
    currentProject,
    chapters,
    storyState,
    selectProject,
    fetchChapters,
    fetchState,
    refreshProject,
    setCurrentChapter,
    setOutlineDesignerOpen,
    outlineView,
    setOutlineView,
    leftPanelVisible,
  } = useProjectStore();
  const [deleting, setDeleting] = useState<number | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<number | null>(null);
  const [deleteOutlineTarget, setDeleteOutlineTarget] = useState(false);
  const [addingChapter, setAddingChapter] = useState(false);
  const [addingOutlineChapter, setAddingOutlineChapter] = useState(false);
  const [deletingOutlineChapter, setDeletingOutlineChapter] = useState<number | null>(null);
  const [deleteOutlineChapterTarget, setDeleteOutlineChapterTarget] = useState<number | null>(null);
  const [deletingOutline, setDeletingOutline] = useState(false);
  const [toast, setToast] = useState<{
    type: "success" | "error";
    message: string;
  } | null>(null);
  const [outlineStatus, setOutlineStatus] = useState<OutlineStatus | null>(
    null
  );
  const [outlineChaptersExpanded, setOutlineChaptersExpanded] = useState(true);
  const [outlineRevision, setOutlineRevision] = useState(0);
  const [chapterContentRevision, setChapterContentRevision] = useState(0);

  useEffect(() => {
    if (id) {
      selectProject(id);
      fetchChapters(id);
      fetchState(id);
      api.getOutlineStatus(id).then(setOutlineStatus).catch(() => {});
    }
  }, [id, selectProject, fetchChapters, fetchState]);

  useEffect(() => {
    const value = new URLSearchParams(location.search).get("outline");
    if (value === "master") {
      setOutlineView("master");
      setOutlineDesignerOpen(true);
      return;
    }
    const match = value?.match(/^chapter-(\d+)$/);
    if (match) {
      setOutlineView(Number(match[1]));
      setOutlineDesignerOpen(true);
      return;
    }
    setOutlineView(null);
    setOutlineDesignerOpen(false);
  }, [location.search, setOutlineDesignerOpen, setOutlineView]);

  const urlChapter = new URLSearchParams(location.search).get("chapter");
  const activeChapter = urlChapter ? Number(urlChapter) : (storyState?.active_chapter ?? currentProject?.current_chapter ?? 1);
  const latestChapterNumber = chapters.length > 0
    ? Math.max(...chapters.map((chapter) => chapter.chapter_number))
    : null;
  const narrativeTime = storyState?.narrative_time ?? "—";
  const totalWords = chapters.length > 0
    ? chapters.reduce((total, chapter) => total + Number(chapter.word_count || 0), 0)
    : (currentProject?.total_words ?? 0);

  const outlineData =
    (currentProject?.outline_data as Record<string, unknown>) || {};
  const outlineChapters = getOutlineChapters(outlineData);
  const outlineNavItems = buildOutlineNavItems(outlineChapters);
  const isFrozen = (outlineData._frozen as boolean) || false;
  const outlineLatestChapterNumber = outlineChapters.length > 0
    ? Math.max(...outlineChapters.map((chapter) => chapter.chapter_number))
    : null;
  const outlineMutationBlockReason = isFrozen
    ? "正式大纲已冻结，请先解冻或提交修订案"
    : outlineStatus?.has_draft
      ? "已有待确认的大纲草稿，请先确认或丢弃草稿"
      : outlineStatus?.chapter_continuity?.valid === false
        ? "正式大纲章节不连续，请先处理连续草稿"
        : null;

  const isOutlineMode = outlineView !== null;

  const confirmDelete = async () => {
    if (deleteTarget === null || !id) return;
    if (deleteTarget !== latestChapterNumber) {
      setDeleteTarget(null);
      setToast({ type: "error", message: "只能从最新章节开始逐章删除" });
      return;
    }
    setDeleting(deleteTarget);
    setDeleteTarget(null);
    try {
      await api.deleteChapter(id, deleteTarget);
      setCurrentChapter(null);
      await fetchChapters(id);
      await fetchState(id);
      await refreshProject(id);
      const remaining = chapters.filter((c) => c.chapter_number !== deleteTarget);
      if (remaining.length > 0) {
        const next = remaining.find((c) => c.chapter_number > deleteTarget) || remaining[remaining.length - 1];
        navigate(`/project/${id}/editor?chapter=${next.chapter_number}`);
      } else {
        navigate(`/project/${id}/editor?chapter=1`);
      }
      setToast({ type: "success", message: `第${deleteTarget}章已删除` });
    } catch (e) {
      setToast({ type: "error", message: `删除失败: ${(e as Error).message}` });
    } finally {
      setDeleting(null);
    }
  };

  const confirmDeleteOutline = async () => {
    if (!id) return;
    setDeletingOutline(true);
    setDeleteOutlineTarget(false);
    try {
      const result = await api.deleteOutline(id);
      await refreshProject(id);
      api.getOutlineStatus(id).then(setOutlineStatus).catch(() => {});
      setOutlineView(null);
      setOutlineDesignerOpen(false);
      navigate(`/project/${id}/editor?chapter=${activeChapter || 1}`);
      setToast({
        type: "success",
        message: result.message || "大纲已删除",
      });
    } catch (e) {
      setToast({ type: "error", message: `删除大纲失败: ${(e as Error).message}` });
    } finally {
      setDeletingOutline(false);
    }
  };

  const handleAddChapter = async () => {
    if (!id || addingChapter) return;
    setAddingChapter(true);
    try {
      const nextChapter = chapters.length > 0
        ? Math.max(...chapters.map((c) => c.chapter_number)) + 1
        : 1;
      await api.updateChapter(id, nextChapter, {
        title: `第${nextChapter}章`,
        content: "",
        status: "draft",
      });
      await fetchChapters(id);
      navigate(`/project/${id}/editor?chapter=${nextChapter}`);
      // Navigating to the same chapter URL is a no-op in React Router. Bump an
      // explicit content revision so the editor reloads the newly created row
      // even when the user was already viewing that not-yet-created chapter.
      setChapterContentRevision((revision) => revision + 1);
      setToast({ type: "success", message: `第${nextChapter}章已创建` });
    } catch (e) {
      setToast({ type: "error", message: `创建失败: ${(e as Error).message}` });
    } finally {
      setAddingChapter(false);
    }
  };

  const handleAddOutlineChapter = async () => {
    if (!id || addingOutlineChapter || outlineMutationBlockReason) return;
    setAddingOutlineChapter(true);
    try {
      const result = await api.appendChapterSpineItem(id);
      await refreshProject(id);
      setOutlineRevision((revision) => revision + 1);
      setOutlineChaptersExpanded(true);
      setOutlineStatus(await api.getOutlineStatus(id));
      setOutlineLocation(result.chapter_number);
      setToast({ type: "success", message: `第${result.chapter_number}章大纲已创建，可继续填写冲突与钩子` });
    } catch (e) {
      setToast({ type: "error", message: `新增大纲章节失败: ${(e as Error).message}` });
    } finally {
      setAddingOutlineChapter(false);
    }
  };

  const confirmDeleteOutlineChapter = async () => {
    const chapterNumber = deleteOutlineChapterTarget;
    if (!id || chapterNumber === null) return;
    if (chapterNumber !== outlineLatestChapterNumber) {
      setDeleteOutlineChapterTarget(null);
      setToast({ type: "error", message: "为保持章节连续，只能从末章开始删除大纲章节" });
      return;
    }
    setDeletingOutlineChapter(chapterNumber);
    setDeleteOutlineChapterTarget(null);
    try {
      await api.deleteChapterSpineItem(id, chapterNumber);
      await refreshProject(id);
      setOutlineRevision((revision) => revision + 1);
      setOutlineStatus(await api.getOutlineStatus(id));
      setOutlineLocation("master");
      setToast({ type: "success", message: `第${chapterNumber}章大纲已删除` });
    } catch (e) {
      setToast({ type: "error", message: `删除大纲章节失败: ${(e as Error).message}` });
    } finally {
      setDeletingOutlineChapter(null);
    }
  };

  const handleOutlineSave = async () => {
    if (id) {
      await refreshProject(id);
      setOutlineRevision((revision) => revision + 1);
      api.getOutlineStatus(id).then(setOutlineStatus).catch(() => {});
    }
  };

  const setOutlineLocation = (view: "master" | number) => {
    const params = new URLSearchParams(location.search);
    params.set("outline", view === "master" ? "master" : `chapter-${view}`);
    setOutlineView(view);
    setOutlineDesignerOpen(true);
    navigate(`${location.pathname}?${params.toString()}`);
  };

  const handleSelectChapterOutline = (chapterNumber: number) => {
    setOutlineLocation(chapterNumber);
  };

  const handleOpenMasterOutline = () => {
    setOutlineLocation("master");
  };

  const handleBackFromChapter = () => {
    setOutlineLocation("master");
  };

  const renderEditorSidebar = () => (
    <div className="flex w-60 flex-col border-r border-white/50 bg-white/40 backdrop-blur-xl shrink-0 z-10 shadow-[4px_0_24px_-12px_rgba(0,0,0,0.05)]">
      <div className="border-b border-white/40 px-4 py-4 shrink-0">
        <h2 className="text-xs font-bold uppercase tracking-widest text-pine-700">章节目录</h2>
      </div>
      <div className="flex-1 overflow-y-auto p-2 scrollbar-none">
        {chapters.map((ch) => (
          <div
            key={ch.chapter_number}
            className={clsx(
              "group flex items-center gap-1 rounded-lg px-1.5 transition-colors",
              ch.chapter_number === activeChapter
                ? "bg-white/60 border border-white/80 shadow-sm"
                : "border border-transparent hover:bg-white/40"
            )}
          >
            <button
              onClick={() =>
                navigate(
                  `/project/${id}/editor?chapter=${ch.chapter_number}`
                )
              }
              className={clsx(
                "flex min-w-0 flex-1 items-center gap-2 rounded-lg px-2 py-2.5 text-left text-sm transition-colors",
                ch.chapter_number === activeChapter
                  ? "text-magic-700 font-medium"
                  : "text-pine-800 hover:text-pine-950"
              )}
            >
              <FileText className={clsx("h-3.5 w-3.5 shrink-0", ch.chapter_number === activeChapter ? "text-magic-500" : "text-pine-700")} />
              <span className="truncate">
                {formatChapterLabel(ch.chapter_number, ch.title)}
              </span>
            </button>
            {ch.chapter_number === latestChapterNumber ? (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  setDeleteTarget(ch.chapter_number);
                }}
                disabled={deleting === ch.chapter_number}
                title="删除最新章节"
                className="shrink-0 rounded p-1 text-pine-700 opacity-0 transition-all hover:bg-red-50 hover:text-red-600 group-hover:opacity-100 disabled:opacity-50"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            ) : (
              <span
                title={`请先删除第${latestChapterNumber}章，章节只能从后向前逐章删除`}
                className="shrink-0 rounded p-1 text-pine-500 opacity-0 transition-opacity group-hover:opacity-70"
              >
                <Lock className="h-3.5 w-3.5" />
              </span>
            )}
          </div>
        ))}
        {chapters.length === 0 && (
          <p className="px-2 py-6 text-center text-xs text-pine-700">
            {outlineStatus?.has_outline
              ? "大纲已就绪，请生成章节"
              : "请先创建大纲"}
          </p>
        )}
      </div>
      <div className="shrink-0 border-t border-white/40 p-3 bg-white/30 backdrop-blur">
        <button
          onClick={handleAddChapter}
          disabled={addingChapter}
          className="flex w-full items-center justify-center gap-2 rounded-lg border border-dashed border-pine-200/50 bg-white/50 px-3 py-2.5 text-sm font-medium text-pine-800 transition-colors hover:border-magic-300 hover:bg-white/80 hover:text-magic-600 disabled:opacity-50 shadow-sm"
        >
          <Plus className="h-4 w-4" />
          <span>{addingChapter ? "添加中..." : "添加新章节"}</span>
        </button>
      </div>
      <div className="relative border-t border-white/40 bg-white/30">
        {id && currentProject && (
          <ExportPanel projectId={id} projectName={currentProject.name || ""} compact />
        )}
      </div>
    </div>
  );

  const renderOutlineSidebar = () => (
    <div className="flex w-64 flex-col border-r border-[var(--workspace-border)] bg-[var(--workspace-sidebar-surface)] shrink-0 z-10">
      <div className="flex h-14 shrink-0 items-center border-b border-[var(--workspace-border)] px-4">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-xs font-bold uppercase tracking-widest text-pine-700">大纲结构</h2>
          {outlineStatus?.has_outline && (
            <button
              onClick={() => setDeleteOutlineTarget(true)}
              disabled={deletingOutline}
              title="删除大纲"
              className="rounded-md p-1 text-pine-700 transition-colors hover:bg-red-50 hover:text-red-600 disabled:opacity-50"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto scrollbar-none">
        <div className="p-3">
          <button
            onClick={handleOpenMasterOutline}
            className={clsx(
              "flex w-full items-center gap-3 rounded-lg px-3 py-3 transition-colors border",
              isOutlineMode && outlineView === "master"
                ? "bg-[var(--workspace-sidebar-active)] border-transparent"
                : "bg-transparent border-transparent hover:bg-pine-900/[0.04]"
            )}
          >
            <div
              className={clsx(
                "flex h-8 w-8 items-center justify-center rounded-lg",
                isOutlineMode && outlineView === "master"
                  ? "bg-magic-100 text-magic-600"
                  : "bg-pine-900/[0.04] text-pine-700"
              )}
            >
              <ScrollText className="h-4 w-4" />
            </div>
            <div className="flex-1 text-left">
              <div className={clsx("text-sm font-bold", isOutlineMode && outlineView === "master" ? "text-magic-800" : "text-pine-900")}>大纲总文件</div>
              <div className="flex items-center gap-1.5 mt-0.5">
                <span className="text-xs text-pine-700 font-medium">
                  {outlineChapters.length} 个已规划节点
                </span>
                {isFrozen && (
                  <span className="inline-flex items-center gap-0.5 text-xs font-semibold text-red-500 bg-red-50 px-1 rounded">
                    <Lock className="h-3 w-3" />
                    冻结
                  </span>
                )}
              </div>
            </div>
          </button>

          {!outlineStatus?.has_outline && (
            <p className="mt-3 px-3 text-xs text-pine-700 text-center">
              点击与架构师对话生成大纲
            </p>
          )}
        </div>

        <div className="border-t border-white/40 mx-3 my-1" />

        <div className="p-2">
          <button
            onClick={() => setOutlineChaptersExpanded(!outlineChaptersExpanded)}
            className="flex w-full items-center gap-1.5 rounded-lg px-2 py-2 text-xs font-bold uppercase tracking-wider text-pine-700 hover:text-pine-900 hover:bg-white/50 transition-colors"
          >
            {outlineChaptersExpanded ? (
              <ChevronDown className="h-4 w-4" />
            ) : (
              <ChevronRight className="h-4 w-4" />
            )}
            已规划章节 ({outlineChapters.length})
          </button>

          {outlineChaptersExpanded && (
            <div className="mt-2 space-y-1">
              {outlineNavItems.map((item) => {
                if (item.kind === "gap") {
                  return (
                    <div
                      key={`gap-${item.start}-${item.end}`}
                      className="flex min-w-0 items-center gap-2 rounded-lg border border-dashed border-pine-200/70 px-2.5 py-2 text-xs text-pine-600/80"
                      title={`第${item.start}–${item.end}章尚未建立大纲`}
                    >
                      <Clock className="h-3.5 w-3.5 shrink-0 text-pine-500" />
                      <span className="min-w-0 flex-1 truncate">
                        第{item.start}–{item.end}章
                      </span>
                      <span className="shrink-0 text-[10px] text-pine-500">待规划</span>
                    </div>
                  );
                }

                const ch = item.chapter;
                return (
                  <div
                    key={ch.chapter_number}
                    className={clsx(
                      "group flex min-w-0 items-center gap-1 rounded-lg px-2 transition-colors",
                      outlineView === ch.chapter_number
                        ? "bg-[var(--workspace-sidebar-active)] border border-transparent"
                        : "border border-transparent hover:bg-pine-900/[0.04]"
                    )}
                  >
                    <button
                      onClick={() => handleSelectChapterOutline(ch.chapter_number)}
                      title={formatChapterLabel(ch.chapter_number, ch.title, "未命名")}
                      className={clsx(
                        "flex min-w-0 flex-1 items-center gap-2 rounded-lg px-1.5 py-2.5 text-left text-sm transition-colors",
                        outlineView === ch.chapter_number
                          ? "text-magic-700 font-medium"
                          : "text-pine-800 hover:text-pine-950"
                      )}
                    >
                      <BookOpen className={clsx("h-3.5 w-3.5 shrink-0", outlineView === ch.chapter_number ? "text-magic-500" : "text-pine-700")} />
                      <span className="min-w-0 flex-1 truncate">
                        {formatChapterLabel(ch.chapter_number, ch.title, "未命名")}
                      </span>
                    </button>
                    {ch.chapter_number === outlineLatestChapterNumber ? (
                      <button
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation();
                          setDeleteOutlineChapterTarget(ch.chapter_number);
                        }}
                        disabled={Boolean(outlineMutationBlockReason) || deletingOutlineChapter === ch.chapter_number}
                        title={outlineMutationBlockReason || "删除末章大纲"}
                        aria-label={`删除第${ch.chapter_number}章大纲`}
                        className="shrink-0 rounded-md p-1 text-pine-600 opacity-0 transition-opacity hover:bg-[var(--color-error-soft)] hover:text-[var(--color-error)] focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)] active:bg-[var(--color-error-soft)] group-hover:opacity-100 disabled:cursor-not-allowed disabled:opacity-30"
                      >
                        {deletingOutlineChapter === ch.chapter_number
                          ? <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />
                          : <Trash2 className="h-3.5 w-3.5" />}
                      </button>
                    ) : (
                      <span
                        title={outlineLatestChapterNumber === null ? undefined : `请先删除第${outlineLatestChapterNumber}章，大纲只能从后向前逐章删除`}
                        className="shrink-0 rounded-md p-1 text-pine-500 opacity-0 transition-opacity group-hover:opacity-70"
                      >
                        <Lock className="h-3.5 w-3.5" />
                      </span>
                    )}
                  </div>
                );
              })}
              {outlineChapters.length === 0 && (
                <p className="px-3 py-6 text-center text-xs text-pine-700">
                  暂无章节大纲
                </p>
              )}
            </div>
          )}
        </div>
      </div>

      <div className="shrink-0 border-t border-[var(--workspace-border)] bg-[var(--workspace-sidebar-surface)] p-3">
        <button
          type="button"
          onClick={() => void handleAddOutlineChapter()}
          disabled={addingOutlineChapter || Boolean(outlineMutationBlockReason)}
          title={outlineMutationBlockReason || "在大纲末尾追加下一章"}
          className="flex min-h-10 w-full items-center justify-center gap-2 whitespace-nowrap rounded-md border border-dashed border-[var(--border-emphasis)] bg-[var(--surface-raised)] px-3 text-sm font-medium text-[var(--color-ink)] transition-colors hover:border-[var(--color-accent)] hover:bg-[var(--color-accent-soft)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)] active:bg-[var(--surface-tool)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {addingOutlineChapter
            ? <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />
            : outlineStatus?.has_draft
              ? <FileEdit className="h-4 w-4" />
              : <Plus className="h-4 w-4" />}
          <span>{addingOutlineChapter ? "添加中" : outlineStatus?.has_draft ? "先处理大纲草稿" : "添加大纲章节"}</span>
        </button>
      </div>

      <div className="relative border-t border-white/40 bg-white/30 shrink-0">
        {id && currentProject && (
          <ExportPanel projectId={id} projectName={currentProject.name || ""} compact />
        )}
      </div>
    </div>
  );

  const renderStatusBar = () => (
    <div className="flex items-center gap-6 border-t border-white/40 bg-white/30 backdrop-blur-md px-5 py-2 text-xs font-medium text-pine-800 shrink-0">
      <span className="flex items-center gap-1.5"><FileText className="h-3.5 w-3.5"/> 当前章节：第{activeChapter}章</span>
      <span className="flex items-center gap-1.5"><ScrollText className="h-3.5 w-3.5"/> 总字数：{totalWords.toLocaleString()}</span>
      <span className="flex items-center gap-1.5"><Clock className="h-3.5 w-3.5"/> 叙事时间：{narrativeTime}</span>
    </div>
  );

  return (
    <div className="flex h-full flex-1 overflow-hidden bg-transparent">
      {leftPanelVisible && !hideChapterSidebar && (isOutlineMode ? renderOutlineSidebar() : renderEditorSidebar())}

      <div className="flex flex-1 flex-col overflow-hidden bg-transparent">
        <div className="flex-1 overflow-x-auto overflow-y-hidden flex flex-col relative">
          {isOutlineMode && id && currentProject ? (
            outlineView === "master" ? (
              <div className="flex h-full min-w-[960px]">
                <motion.div 
                  initial={{ opacity: 0, x: -30 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ duration: 0.4, ease: "easeOut" }}
                  className="min-w-[480px] flex-1 flex flex-col border-r border-[var(--workspace-border)] bg-[var(--workspace-document-surface)] overflow-hidden"
                >
                  <div className="min-h-0 flex-1 overflow-hidden">
                    <ChatErrorBoundary agentName="大纲总文件">
                      <OutlineMasterEditor
                        projectId={id}
                        outlineData={outlineData}
                        projectBrief={{
                          name: currentProject.name,
                          description: currentProject.description || "",
                          genre: currentProject.genre || "",
                          wordCountTarget: currentProject.word_count_target || 100000,
                        }}
                        frozen={isFrozen}
                        onSave={handleOutlineSave}
                        onSelectChapter={handleSelectChapterOutline}
                        refreshRevision={outlineRevision}
                      />
                    </ChatErrorBoundary>
                  </div>
                  {renderStatusBar()}
                </motion.div>
                <motion.div 
                  initial={{ opacity: 0, x: 30 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ duration: 0.4, delay: 0.1, ease: "easeOut" }}
                  className="flex-1 flex flex-col min-w-0 overflow-hidden"
                >
                  <OutlineDesigner
                    projectId={id}
                    projectName={currentProject.name}
                    outlineData={currentProject?.outline_data || undefined}
                    onClose={() => {
                      setOutlineDesignerOpen(false);
                    }}
                    onOutlineSaved={handleOutlineSave}
                  />
                </motion.div>
              </div>
            ) : (
              <div className="flex h-full min-w-[960px]">
                <motion.div 
                  initial={{ opacity: 0, x: -30 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ duration: 0.4, ease: "easeOut" }}
                  className="min-w-[480px] flex-1 flex flex-col border-r border-[var(--workspace-border)] bg-[var(--workspace-document-surface)] overflow-hidden"
                >
                  <div className="flex-1 overflow-auto">
                    <ChapterOutlineDetail
                      projectId={id}
                      chapterNumber={outlineView as number}
                      outlineData={outlineData}
                      frozen={isFrozen}
                      onBack={handleBackFromChapter}
                      onSave={handleOutlineSave}
                      refreshRevision={outlineRevision}
                    />
                  </div>
                  {renderStatusBar()}
                </motion.div>
                <motion.div 
                  initial={{ opacity: 0, x: 30 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ duration: 0.4, delay: 0.1, ease: "easeOut" }}
                  className="flex-1 flex flex-col min-w-0 overflow-hidden"
                >
                  <OutlineDesigner
                    key={`chapter-${outlineView as number}`}
                    projectId={id}
                    projectName={currentProject.name}
                    outlineData={currentProject?.outline_data || undefined}
                    contextMode="chapter"
                    selectedChapterNumber={outlineView as number}
                    onClose={handleBackFromChapter}
                    onOutlineSaved={handleOutlineSave}
                  />
                </motion.div>
              </div>
            )
          ) : isDashboard ? (
            <ProjectDashboard />
          ) : (
            <Outlet context={{ renderStatusBar, chapterContentRevision }} />
          )}
        </div>
      </div>

      {deleteTarget !== null && (
        <Modal
          title="确认删除"
          onClose={() => setDeleteTarget(null)}
          footer={
            <>
              <GlassButton variant="ghost" onClick={() => setDeleteTarget(null)}>取消</GlassButton>
              <GlassButton variant="danger" onClick={confirmDelete}>删除</GlassButton>
            </>
          }
        >
          <p className="text-sm text-pine-800 leading-relaxed">
            确定要删除第
            <span className="font-bold text-red-600 mx-1">
              {deleteTarget}
            </span>
            章吗？
          </p>
          <p className="mt-2 text-xs text-pine-700 font-medium">
            将同时清除该章产生的世界观、伏笔、状态、质量记忆和修订记录。此操作不可恢复。
          </p>
        </Modal>
      )}

      {deleteOutlineTarget && (
        <Modal
          title="删除大纲"
          onClose={() => setDeleteOutlineTarget(false)}
          footer={
            <>
              <GlassButton variant="ghost" onClick={() => setDeleteOutlineTarget(false)}>取消</GlassButton>
              <GlassButton variant="danger" onClick={confirmDeleteOutline} disabled={deletingOutline}>
                {deletingOutline ? "删除中..." : "删除"}
              </GlassButton>
            </>
          }
        >
          <p className="text-sm text-pine-800 leading-relaxed">
            确定要删除整个大纲吗？这会清空正式大纲、草案、索引和向量缓存，但不会删除章节正文。
          </p>
          <p className="mt-2 text-xs text-pine-700 font-medium">此操作不可恢复。</p>
        </Modal>
      )}

      {deleteOutlineChapterTarget !== null && (
        <Modal
          title="删除大纲末章"
          onClose={() => setDeleteOutlineChapterTarget(null)}
          footer={
            <>
              <GlassButton variant="ghost" onClick={() => setDeleteOutlineChapterTarget(null)}>取消</GlassButton>
              <GlassButton variant="danger" onClick={confirmDeleteOutlineChapter}>删除大纲章节</GlassButton>
            </>
          }
        >
          <p className="text-sm leading-relaxed text-pine-800">
            确定删除第 <strong className="mx-1 text-[var(--color-error)]">{deleteOutlineChapterTarget}</strong> 章的大纲吗？
          </p>
          <p className="mt-2 text-xs font-medium text-pine-700">
            这里只删除章节规划，不删除正文。若该章已经存在于正文列表，系统会要求先删除正文，避免留下孤立章节。
          </p>
        </Modal>
      )}

      {toast && (
        <Toast
          type={toast.type}
          message={toast.message}
          onClose={() => setToast(null)}
        />
      )}
    </div>
  );
}
