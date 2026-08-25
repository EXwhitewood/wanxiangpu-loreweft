import { useEffect, useState, useCallback, useRef } from "react";
import { useParams, useSearchParams, useOutletContext } from "react-router-dom";
import { motion, AnimatePresence } from "framer-motion";
import { useProjectStore } from "@/stores/projectStore";
import { useEntertainmentStore } from "@/stores/entertainmentStore";
import { useWorldbuildingStore } from "@/stores/worldbuildingStore";
import TiptapEditor from "@/components/Editor/TiptapEditor";
import WritingCompanionChat from "@/components/Editor/WritingCompanionChat";
import ChapterBlueprintPanel, {
  type ChapterBlueprintPanelHandle,
  type ChapterBlueprintStatus,
} from "@/components/Editor/ChapterBlueprintPanel";
import WorkflowMonitor from "@/components/Editor/WorkflowMonitor";
import ContextEnvelopePanel from "@/components/Editor/ContextEnvelopePanel";
import ChapterReminderPanel from "@/components/Editor/ChapterReminderPanel";
import EntertainmentWorkflowPrompt from "@/components/Entertainment/EntertainmentWorkflowPrompt";
import Toast from "@/components/Common/Toast";
import * as api from "@/api/client";
import type {
  ChapterAdvisoryCheckResponse,
  ChapterAdvisoryTrigger,
  ChapterDiagnosticRecord,
  ChapterSettlementStatus,
  GenerationHistoryEntry,
  WritingAssistanceSettings,
} from "@/types";
import { Save, History, FileText, CheckCircle2, BookOpenCheck, Activity, Feather, Loader2, Play } from "lucide-react";
import { GlassButton } from "@/components/UI/GlassButton";
import { FadeInWrapper } from "@/components/UI/FadeInWrapper";
import { chapterTitleWithoutNumber } from "@/utils/chineseDisplay";
import { countWords } from "@/utils/wordCount";

export default function EditorPage() {
  const { id } = useParams<{ id: string }>();
  const [searchParams] = useSearchParams();
  const chapterParam = searchParams.get("chapter");
  const humanWorkbenchPreview = import.meta.env.DEV
    && searchParams.get("human-workbench-preview") === "1";
  const {
    currentChapter,
    setCurrentChapter,
    chapters,
    rightPanelVisible,
    storyState,
    currentProject,
    fetchChapters,
    fetchState,
    refreshProject,
  } = useProjectStore();
  const { settings: entertainmentSettings, notifyWorkflowComplete } = useEntertainmentStore();
  const invalidateWorldbuilding = useWorldbuildingStore(
    (state) => state.invalidateProjectedData,
  );
  const [content, setContent] = useState("");
  const [saving, setSaving] = useState(false);
  const [genHistory, setGenHistory] = useState<GenerationHistoryEntry[]>([]);
  const [showHistory, setShowHistory] = useState(false);
  const [rightTab, setRightTab] = useState<"blueprint" | "companion" | "workflow">(
    humanWorkbenchPreview ? "workflow" : "blueprint",
  );
  const [highlightNew, setHighlightNew] = useState(false);
  const [activeExecutionId, setActiveExecutionId] = useState<string | null>(null);
  const [showEntertainmentPrompt, setShowEntertainmentPrompt] = useState(false);
  const [assistanceSettings, setAssistanceSettings] = useState<WritingAssistanceSettings | null>(null);
  const [assistanceLoading, setAssistanceLoading] = useState(true);
  const [advisoryResult, setAdvisoryResult] = useState<ChapterAdvisoryCheckResponse | null>(null);
  const [checkedContent, setCheckedContent] = useState<string | null>(null);
  const [advisoryChecking, setAdvisoryChecking] = useState(false);
  const [advisoryError, setAdvisoryError] = useState<string | null>(null);
  const [chapterDiagnostics, setChapterDiagnostics] = useState<ChapterDiagnosticRecord[]>([]);
  const [settlementStatus, setSettlementStatus] = useState<ChapterSettlementStatus | null>(null);
  const blueprintRef = useRef<ChapterBlueprintPanelHandle | null>(null);
  const chapterLoadRequestId = useRef(0);
  const [blueprintStatus, setBlueprintStatus] = useState<ChapterBlueprintStatus>({
    loading: true,
    saving: false,
    dirty: false,
    persisted: false,
    frozen: false,
    revision: null,
    blockingCount: 0,
    error: null,
  });
  const [startingGeneration, setStartingGeneration] = useState(false);
  const advisoryRequestId = useRef(0);
  const settlementMonitorId = useRef(0);
  // 工作流完成弱提醒 toast
  const [workflowToast, setWorkflowToast] = useState<{ type: "success" | "error"; message: string } | null>(null);
  const { renderStatusBar, chapterContentRevision = 0 } = useOutletContext<{
    renderStatusBar?: () => React.ReactNode;
    chapterContentRevision?: number;
  }>() || {};

  useEffect(() => {
    if (humanWorkbenchPreview) setRightTab("workflow");
  }, [humanWorkbenchPreview]);

  // 附录4问题13修复：与 ProjectWorkspace 统一 activeChapter 来源
  const chapterNumber = chapterParam
    ? parseInt(chapterParam)
    : (storyState?.active_chapter ?? currentProject?.current_chapter ?? 1);
  const selectedChapterListed = chapters.some(
    (chapter) => chapter.chapter_number === chapterNumber,
  );
  const chapterContextTitle = chapterTitleWithoutNumber(
    chapterNumber,
    currentChapter?.title,
  ) || "本章工作台";
  const hasExistingContent = countWords(content) > 0;

  const handleGenerated = useCallback(async () => {
    if (!id) return;
    try {
      const chapter = await api.getChapter(id, chapterNumber);
      setCurrentChapter(chapter);
      setContent(chapter.content || "");
      setHighlightNew(true);
    } catch {
      setCurrentChapter(null);
      setContent("");
    }
    api.getGenerationHistory(id).then(setGenHistory).catch(() => {});
  }, [id, chapterNumber, setCurrentChapter]);

  const refreshWorkspaceAfterCommit = useCallback(async () => {
    if (!id) return;
    invalidateWorldbuilding(id);
    await Promise.allSettled([
      // The chapter directory is backed by this store slice. Refresh it at
      // commit time so the newly committed chapter/status appears without a
      // full page reload.
      fetchChapters(id),
      fetchState(id),
      refreshProject(id),
    ]);
  }, [fetchChapters, fetchState, id, invalidateWorldbuilding, refreshProject]);

  const dismissEntertainmentPrompt = useCallback(() => {
    setShowEntertainmentPrompt(false);
  }, []);

  const loadChapter = useCallback(async () => {
    if (!id) return;
    const requestId = ++chapterLoadRequestId.current;
    try {
      const chapter = await api.getChapter(id, chapterNumber);
      if (requestId !== chapterLoadRequestId.current) return;
      setCurrentChapter(chapter);
      setContent(chapter.content || "");
    } catch {
      if (requestId !== chapterLoadRequestId.current) return;
      setCurrentChapter(null);
      setContent("");
    }
  }, [id, chapterNumber, setCurrentChapter]);

  useEffect(() => {
    void loadChapter();
  }, [chapterContentRevision, loadChapter, selectedChapterListed]);

  useEffect(() => {
    if (id) {
      api.getGenerationHistory(id).then(setGenHistory).catch(() => {});
    }
  }, [id]);

  useEffect(() => {
    let cancelled = false;
    if (!id) {
      setAssistanceSettings(null);
      setAssistanceLoading(false);
      return;
    }
    setAssistanceLoading(true);
    setAdvisoryError(null);
    api.getWritingAssistanceSettings(id)
      .then((settings) => {
        if (!cancelled) setAssistanceSettings(settings);
      })
      .catch((cause) => {
        if (!cancelled) {
          setAssistanceSettings(null);
          setAdvisoryError((cause as Error).message || "提醒设置读取失败");
        }
      })
      .finally(() => {
        if (!cancelled) setAssistanceLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  useEffect(() => {
    settlementMonitorId.current += 1;
    setSettlementStatus(null);
    advisoryRequestId.current += 1;
    setAdvisoryResult(null);
    setCheckedContent(null);
    setAdvisoryChecking(false);
    setAdvisoryError(null);
    if (id) {
      api.getChapterDiagnostics(id, chapterNumber)
        .then((result) => setChapterDiagnostics(result.items || []))
        .catch(() => setChapterDiagnostics([]));
    } else {
      setChapterDiagnostics([]);
    }
  }, [id, chapterNumber]);

  const monitorSettlement = useCallback(async (jobId: string) => {
    if (!id) return;
    const monitorId = ++settlementMonitorId.current;
    for (let attempt = 0; attempt < 120; attempt += 1) {
      if (attempt > 0) {
        await new Promise((resolve) => window.setTimeout(resolve, 2500));
      }
      if (monitorId !== settlementMonitorId.current) return;
      try {
        const status = await api.getChapterSettlement(id, chapterNumber);
        if (monitorId !== settlementMonitorId.current || !status || status.job_id !== jobId) return;
        setSettlementStatus(status);
        if (status.status === "completed") {
          const [diagnosticsResult] = await Promise.allSettled([
            api.getChapterDiagnostics(id, chapterNumber),
            refreshWorkspaceAfterCommit(),
          ]);
          if (monitorId !== settlementMonitorId.current) return;
          if (diagnosticsResult.status === "fulfilled") {
            setChapterDiagnostics(diagnosticsResult.value.items || []);
            setAdvisoryError(null);
          }
          setWorkflowToast({
            type: "success",
            message: `第${chapterNumber}章正文与世界状态已同步`,
          });
          return;
        }
        if (["retryable_failed", "degraded", "superseded"].includes(status.status)) {
          setAdvisoryError(
            status.status === "superseded"
              ? "正文已保存；本次状态同步已被更新版本取代。"
              : `正文已保存，但附属状态同步未完成：${status.error || "可点击重新同步"}`
          );
          setWorkflowToast({
            type: "error",
            message: "正文已安全保存，但世界状态同步需要重试",
          });
          return;
        }
      } catch (cause) {
        if (attempt === 119 && monitorId === settlementMonitorId.current) {
          setAdvisoryError(`正文已保存；结算状态暂时无法读取：${(cause as Error).message}`);
        }
      }
    }
  }, [chapterNumber, id, refreshWorkspaceAfterCommit]);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    api.getChapterSettlement(id, chapterNumber)
      .then((status) => {
        if (cancelled) return;
        setSettlementStatus(status);
        if (status && ["pending", "running"].includes(status.status)) {
          void monitorSettlement(status.job_id);
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [chapterNumber, id, monitorSettlement]);

  const handleContentChange = (newContent: string) => {
    setContent(newContent);
  };

  const runAdvisoryCheck = useCallback(async (
    trigger: ChapterAdvisoryTrigger,
    contentSnapshot: string,
  ) => {
    if (!id || !assistanceSettings?.consistency_reminders.enabled) return;
    if (trigger === "save" && !assistanceSettings.consistency_reminders.check_on_save) return;

    const requestId = ++advisoryRequestId.current;
    setAdvisoryChecking(true);
    setAdvisoryError(null);
    try {
      const result = await api.checkChapterAdvisories(id, chapterNumber, {
        content: contentSnapshot,
        trigger,
        dimensions: assistanceSettings.consistency_reminders.dimensions,
      });
      if (requestId === advisoryRequestId.current) {
        setAdvisoryResult(result);
        setCheckedContent(contentSnapshot);
        const persisted = await api.getChapterDiagnostics(id, chapterNumber);
        if (requestId === advisoryRequestId.current) {
          setChapterDiagnostics(persisted.items || []);
        }
      }
    } catch (cause) {
      if (requestId === advisoryRequestId.current) {
        setAdvisoryError(
          `${(cause as Error).message || "提醒检查失败"}。正文编辑和保存不受影响。`
        );
      }
    } finally {
      if (requestId === advisoryRequestId.current) {
        setAdvisoryChecking(false);
      }
    }
  }, [id, chapterNumber, assistanceSettings]);

  const handleAssistanceSettingsChange = useCallback(async (
    settings: WritingAssistanceSettings,
  ) => {
    if (!id) throw new Error("项目尚未加载");
    const updated = await api.updateWritingAssistanceSettings(id, settings);
    setAssistanceSettings(updated);
    if (!updated.consistency_reminders.enabled) {
      advisoryRequestId.current += 1;
      setAdvisoryChecking(false);
    }
  }, [id]);

  const handleSave = async () => {
    if (!id || !currentChapter) return;
    const savedContent = content;
    setSaving(true);
    setWorkflowToast(null);
    try {
      const saved = await api.updateChapter(id, chapterNumber, {
        content: savedContent,
        title: currentChapter?.title || `第${chapterNumber}章`,
        status: currentChapter?.status || "draft",
      });
      await refreshWorkspaceAfterCommit();
      setCheckedContent(savedContent);
      setSettlementStatus(saved.settlement || null);
      setWorkflowToast({
        type: "success",
        message: saved.settlement?.status === "completed"
          ? `第${chapterNumber}章已保存并完成状态同步`
          : `第${chapterNumber}章正文已保存，世界状态正在后台同步`,
      });
      if (saved.settlement && saved.settlement.status !== "completed") {
        void monitorSettlement(saved.settlement.job_id);
      }
    } catch (cause) {
      setWorkflowToast({
        type: "error",
        message: `保存失败：${(cause as Error).message || "未知错误"}`,
      });
    } finally {
      setSaving(false);
    }
  };

  const retrySettlement = useCallback(async () => {
    if (!id) return;
    setAdvisoryError(null);
    try {
      const status = await api.retryChapterSettlement(id, chapterNumber);
      setSettlementStatus(status);
      setWorkflowToast({ type: "success", message: "已重新提交世界状态同步" });
      void monitorSettlement(status.job_id);
    } catch (cause) {
      setAdvisoryError(`重新同步失败：${(cause as Error).message || "未知错误"}`);
    }
  }, [chapterNumber, id, monitorSettlement]);

  const handleChapterDiagnosticDecision = useCallback(async (
    diagnosticId: string,
    action: "mark_fixed" | "ignore" | "ignore_and_amend_outline",
  ) => {
    if (!id) return;
    const updated = await api.decideChapterDiagnostic(id, chapterNumber, diagnosticId, {
      action,
    });
    setChapterDiagnostics((current) => {
      if (["resolved", "ignored", "outline_amendment_applied"].includes(updated.status)) {
        return current.filter((item) => item.diagnostic_id !== diagnosticId);
      }
      return current.map((item) => item.diagnostic_id === diagnosticId ? updated : item);
    });
  }, [chapterNumber, id]);

  const handleGenerateChapter = useCallback(async () => {
    if (!id || startingGeneration || activeExecutionId) return;
    if (hasExistingContent) {
      setWorkflowToast({
        type: "error",
        message: "本章已有正文，普通 AI 生成不会覆盖现有内容",
      });
      return;
    }
    setStartingGeneration(true);
    setWorkflowToast(null);
    try {
      const savedBlueprint = await blueprintRef.current?.saveIfNeeded();
      if (!savedBlueprint) throw new Error("本章蓝图尚未加载完成");
      const blocking = (savedBlueprint.diagnostics || []).filter((item) => item.blocks_generation);
      if (blocking.length > 0) {
        throw new Error(blocking.map((item) => item.message).join("；"));
      }
      const result = await api.editorGenerate(
        id,
        chapterNumber,
        null,
        null,
        savedBlueprint.revision,
      );
      setActiveExecutionId(result.execution_id);
      setRightTab("workflow");
      setShowEntertainmentPrompt(true);
    } catch (cause) {
      setWorkflowToast({
        type: "error",
        message: `无法生成本章：${(cause as Error).message}`,
      });
    } finally {
      setStartingGeneration(false);
    }
  }, [activeExecutionId, chapterNumber, hasExistingContent, id, startingGeneration]);

  const advisoryIsStale = checkedContent !== null && checkedContent !== content;
  const reminderCount = assistanceSettings?.consistency_reminders.enabled && !advisoryIsStale
    ? chapterDiagnostics.length
    : 0;

  return (
    <div className="flex h-full w-full overflow-hidden bg-white/50">
      <div className="flex-1 flex flex-col overflow-hidden relative">
        <EntertainmentWorkflowPrompt
          visible={showEntertainmentPrompt}
          onDismiss={dismissEntertainmentPrompt}
        />
        <div className="flex-1 overflow-auto p-4 sm:p-8 lg:p-12 relative">
          <FadeInWrapper className="mx-auto max-w-[800px] h-full flex flex-col relative z-10">
          <div className="mb-8 flex items-center justify-between sticky top-0 bg-white/50 backdrop-blur-xl py-4 z-20 rounded-b-2xl px-6 border-b border-pine-200/30">
            <h2 className="text-xl font-bold text-pine-700 font-serif tracking-wide flex items-center gap-3">
              <FileText className="h-5 w-5 text-magic-500" />
              {currentChapter?.title || `第${chapterNumber}章`}
            </h2>
            <div className="flex items-center gap-3">
              {genHistory.length > 0 && (
                <GlassButton variant="ghost" onClick={() => setShowHistory(!showHistory)}>
                  <History className="h-4 w-4" />
                  {showHistory ? "隐藏历史" : `记录 (${genHistory.length})`}
                </GlassButton>
              )}
              <GlassButton variant="primary" onClick={handleSave} disabled={saving || !currentChapter}>
                {saving ? (
                  <motion.div animate={{ rotate: 360 }} transition={{ repeat: Infinity, duration: 1, ease: "linear" }}>
                    <CheckCircle2 className="h-4 w-4" />
                  </motion.div>
                ) : (
                  <Save className="h-4 w-4" />
                )}
                {saving ? "保存中..." : "保存文本"}
              </GlassButton>
            </div>
          </div>

          <AnimatePresence>
            {showHistory && genHistory.length > 0 && (
              <motion.div 
                initial={{ opacity: 0, height: 0, marginBottom: 0 }}
                animate={{ opacity: 1, height: "auto", marginBottom: 24 }}
                exit={{ opacity: 0, height: 0, marginBottom: 0 }}
                className="overflow-hidden glass-panel rounded-2xl"
              >
                <div className="p-4 max-h-48 overflow-y-auto scrollbar-none space-y-2">
                  <h4 className="mb-3 text-xs font-bold uppercase tracking-wider text-pine-700 flex items-center gap-2">
                    <History className="h-3 w-3" />
                    生成时间线
                  </h4>
                  {[...genHistory].reverse().map((entry, i) => (
                    <div key={i} className="flex items-center justify-between rounded-lg bg-white/50 px-3 py-2 text-xs border border-pine-200/50">
                      <div className="flex items-center gap-4">
                        <span className="text-magic-400/70 font-mono">
                          {new Date(entry.timestamp).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}
                        </span>
                        <span className="text-pine-700 bg-white/50 px-2 py-0.5 rounded">第{entry.chapter}章</span>
                        <span className="text-pine-700">{entry.word_count} 字</span>
                      </div>
                      <div className="flex items-center gap-3">
                        {entry.report_summary.auto_repairs > 0 && (
                          <span className="text-magic-400 bg-magic-500/10 px-2 py-0.5 rounded-full">
                            自动修复 {entry.report_summary.auto_repairs} 处
                          </span>
                        )}
                        <span className={`px-2 py-0.5 rounded-full ${entry.report_summary.pass ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20" : "bg-crimson-500/10 text-crimson-400 border border-crimson-500/20"}`}>
                          {entry.report_summary.pass ? "检测通过" : "存在瑕疵"}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          <div className="flex-1 glass-panel rounded-2xl overflow-hidden shadow-2xl relative flex flex-col">
            {currentChapter ? (
              <TiptapEditor
                content={content}
                onChange={handleContentChange}
                highlightNew={highlightNew}
                onHighlightDismissed={() => setHighlightNew(false)}
              />
            ) : (
              <div className="flex h-full flex-col items-center justify-center p-12 text-center">
                <FileText className="mb-4 h-16 w-16 text-pine-700 opacity-50" />
                <h3 className="text-lg font-medium text-pine-700">尚未加载章节</h3>
                <p className="mt-2 text-sm text-pine-700">请从左侧大纲选择章节，或者新建章节开始创作。</p>
              </div>
            )}
          </div>
          </FadeInWrapper>
        </div>
        {renderStatusBar && renderStatusBar()}
      </div>

      <AnimatePresence>
        {rightPanelVisible && (
          <motion.div 
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 20 }}
            transition={{ type: "spring", stiffness: 300, damping: 30 }}
            className="w-[430px] shrink-0 border-l border-pine-200/50 bg-white/50 backdrop-blur-xl flex flex-col z-20 shadow-[-10px_0_30px_rgba(0,0,0,0.5)]"
          >
            <div className="border-b border-pine-200/50 bg-white/55">
              <div className="flex items-center justify-between gap-3 px-3 py-2.5">
                <div className="min-w-0">
                  <p className="truncate text-xs font-semibold text-pine-950">
                    第{chapterNumber}章 · {chapterContextTitle}
                  </p>
                  <p className="mt-0.5 flex items-center gap-1 text-[10px] text-pine-500">
                    {blueprintStatus.dirty || !blueprintStatus.persisted ? (
                      <><span className="h-1.5 w-1.5 rounded-full bg-amber-500" />蓝图有未保存修改</>
                    ) : (
                      <><span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />蓝图 v{blueprintStatus.revision ?? "—"}</>
                    )}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => void handleGenerateChapter()}
                  disabled={
                    startingGeneration
                    || Boolean(activeExecutionId)
                    || blueprintStatus.loading
                    || blueprintStatus.saving
                    || blueprintStatus.blockingCount > 0
                    || hasExistingContent
                  }
                  title={hasExistingContent ? "本章已有正文；普通生成不会覆盖现有内容" : undefined}
                  className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-pine-800 px-3 py-2 text-[11px] font-semibold text-white shadow-sm transition-colors hover:bg-pine-900 disabled:cursor-not-allowed disabled:opacity-45"
                >
                  {startingGeneration || activeExecutionId
                    ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    : <Play className="h-3.5 w-3.5" />}
                  {startingGeneration
                    ? "准备中"
                    : activeExecutionId
                      ? "生成中"
                      : hasExistingContent
                        ? "已有正文"
                        : blueprintStatus.dirty || !blueprintStatus.persisted
                        ? "保存并生成"
                        : "生成本章"}
                </button>
              </div>
              <div className="flex gap-1 px-2 pb-2">
                {[
                  { id: "blueprint" as const, label: "本章蓝图", icon: BookOpenCheck },
                  { id: "companion" as const, label: "墨伴", icon: Feather },
                  { id: "workflow" as const, label: "流程", icon: Activity },
                ].map(tab => (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => setRightTab(tab.id)}
                  className={`flex-1 flex items-center justify-center gap-1.5 whitespace-nowrap rounded-lg px-2 py-2 text-[11px] font-medium transition-all ${
                    rightTab === tab.id
                      ? "bg-magic-500/20 text-magic-400 shadow-[inset_0_1px_0_rgba(255,255,255,0.1)] border border-magic-500/20"
                      : "text-pine-700 hover:bg-white/50 hover:text-pine-700"
                  }`}
                >
                  <tab.icon className="h-3.5 w-3.5" />
                  {tab.label}
                  {tab.id === "blueprint" && reminderCount > 0 && (
                    <span className="min-w-4 rounded-full bg-amber-500/15 px-1 text-[10px] font-semibold text-amber-700">
                      {reminderCount}
                    </span>
                  )}
                </button>
              ))}
              </div>
            </div>
            <div className="flex-1 overflow-hidden relative">
              {rightTab === "blueprint" ? (
                <ChapterBlueprintPanel
                  ref={blueprintRef}
                  projectId={id || ""}
                  chapterNumber={chapterNumber}
                  onStatusChange={setBlueprintStatus}
                  contextContent={
                    <ContextEnvelopePanel
                      projectId={id || ""}
                      chapterNumber={chapterNumber}
                    />
                  }
                  reminderCount={reminderCount}
                  reminderContent={
                    <ChapterReminderPanel
                      settings={assistanceSettings}
                      result={advisoryResult}
                      checkedContent={checkedContent}
                      currentContent={content}
                      loading={assistanceLoading}
                      checking={advisoryChecking}
                      error={advisoryError}
                      diagnostics={chapterDiagnostics}
                      settlement={settlementStatus}
                      onSettingsChange={handleAssistanceSettingsChange}
                      onCheck={() => void runAdvisoryCheck("manual", content)}
                      onDiagnosticDecision={handleChapterDiagnosticDecision}
                      onRetrySettlement={() => void retrySettlement()}
                    />
                  }
                />
              ) : rightTab === "companion" ? (
                <WritingCompanionChat
                  projectId={id || ""}
                  chapterNumber={chapterNumber}
                />
              ) : rightTab === "workflow" ? (
                <WorkflowMonitor
                  projectId={id || ""}
                  activeExecutionId={activeExecutionId}
                  onWorkflowComplete={() => {
                    setActiveExecutionId(null);
                    dismissEntertainmentPrompt();
                    void handleGenerated();
                    void refreshWorkspaceAfterCommit();
                    void blueprintRef.current?.reload();
                    setRightTab("blueprint");
                    // 弱提醒：若用户启用了"工作流完成提醒"，显示 toast 通知
                    if (entertainmentSettings?.notify_on_workflow_complete) {
                      setWorkflowToast({
                        type: "success",
                        message: "✅ 本章生成已完成，蓝图与正文已刷新",
                      });
                    }
                    // 若娱乐弹窗当时是打开的，更新弹窗内提示为"工作流已完成"
                    notifyWorkflowComplete();
                  }}
                />
              ) : null}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* 工作流完成弱提醒 Toast */}
      {workflowToast && (
        <Toast
          type={workflowToast.type}
          message={workflowToast.message}
          onClose={() => setWorkflowToast(null)}
        />
      )}
    </div>
  );
}
