/* Hallmark · pre-emit critique: P5 H5 E5 S5 R5 V5 · macrostructure: Workbench control dossier · design-system: DESIGN.md */
import { useEffect, useState, useRef, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useParams } from "react-router-dom";
import {
  Feather,
  Upload,
  Loader2,
  BookOpen,
  X,
  ChevronRight,
  Trash2,
  GitMerge,
  Shield,
  Power,
  RotateCcw,
  AlertTriangle,
} from "lucide-react";
import clsx from "clsx";
import * as api from "@/api/client";
import type {
  StyleProfile,
  StyleEmbedding,
  StyleConflictReport,
  StyleFusionRequest,
} from "@/types";

const STYLE_STATUS_CONFIG: Record<string, { label: string; color: string }> = {
  learning: { label: "学习中", color: "text-magic-400 bg-magic-500/15 border-magic-500/30" },
  ready: { label: "就绪", color: "text-emerald-400 bg-emerald-500/15 border-emerald-500/30" },
  failed: { label: "失败", color: "text-crimson-400 bg-crimson-500/15 border-crimson-500/30" },
};

const STYLE_DIMENSION_LABELS: Record<string, string> = {
  vocabulary: "词汇偏好",
  sentence_structure: "句式特征",
  tone: "语气基调",
  pacing: "节奏特征",
  description_style: "描写风格",
  dialogue_style: "对话风格",
  narrative_voice: "叙事视角",
};

const EMBEDDING_DIMENSION_LABELS: Record<keyof StyleEmbedding, string> = {
  emotionality: "情感外显度",
  sentence_complexity: "句式复杂度",
  narrative_distance: "叙事距离",
  info_density: "信息密度",
  dialogue_ratio: "对话占比",
  description_density: "描写密度",
  rhythm_steepness: "节奏陡峭度",
  narrator_intrusion: "叙事者介入度",
};

const EMBEDDING_GROUPS: Array<{
  label: string;
  description: string;
  dimensions: Array<keyof StyleEmbedding>;
}> = [
  {
    label: "叙事气质",
    description: "情绪、距离与信息呈现",
    dimensions: ["emotionality", "narrative_distance", "info_density", "narrator_intrusion"],
  },
  {
    label: "文本节奏",
    description: "句式、对白与描写配比",
    dimensions: ["sentence_complexity", "dialogue_ratio", "description_density", "rhythm_steepness"],
  },
];

const PASSAGE_CATEGORY_LABELS: Record<string, string> = {
  action: "动作",
  dialogue: "对话",
  description: "描写",
  emotion: "情感",
  opening: "开篇",
  ending: "结尾",
};

const DETAIL_TABS = [
  { id: "features", label: "风格特征" },
  { id: "embedding", label: "八维画像" },
  { id: "persona", label: "创作人格" },
  { id: "samples", label: "范例片段" },
  { id: "statistics", label: "统计与演变" },
] as const;

type DetailTab = (typeof DETAIL_TABS)[number]["id"];

const CONFLICT_LEVEL_COLORS: Record<string, string> = {
  L1: "text-emerald-400 bg-emerald-500/15 border-emerald-500/30",
  L2: "text-magic-400 bg-magic-500/15 border-magic-500/30",
  L3: "text-orange-400 bg-orange-500/15 border-orange-500/30",
  L4: "text-crimson-400 bg-crimson-500/15 border-crimson-500/30",
};

type ViewMode = "list" | "detail" | "fusion" | "conflicts" | "upload";

function profileInfluencesWriting(profile: StyleProfile): boolean {
  return profile.status === "ready"
    && (profile.active || profile.frozen)
    && profile.editor_influence_enabled !== false;
}

type StyleLearningStepKey = "prepare" | "upload" | "scan" | "distill" | "refine" | "evolve" | "save" | "done" | "error";

interface StyleLearningWorkflow {
  name: string;
  fileName: string;
  profileId: string;
  status: "running" | "done" | "error";
  currentStepIndex: number;
  message: string;
  error?: string;
}

const STYLE_LEARNING_STEPS: Array<{ key: StyleLearningStepKey; label: string; desc: string }> = [
  { key: "prepare", label: "准备", desc: "确认名称与上传文件" },
  { key: "upload", label: "上传", desc: "接收并解析原始文本" },
  { key: "scan", label: "扫描", desc: "提取句式、节奏与场景分布" },
  { key: "distill", label: "蒸馏", desc: "挑选代表性片段" },
  { key: "refine", label: "精炼", desc: "生成风格特征与人格画像" },
  { key: "evolve", label: "演变", desc: "检测多风格簇与变化点" },
  { key: "save", label: "入库", desc: "写入风格画像与激活信息" },
];

export default function StylePage() {
  const { id: projectId } = useParams<{ id: string }>();
  const [styles, setStyles] = useState<StyleProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [viewMode, setViewMode] = useState<ViewMode>("list");
  const [selectedProfileId, setSelectedProfileId] = useState<string | null>(null);
  const [showUpload, setShowUpload] = useState(false);
  const [workflow, setWorkflow] = useState<StyleLearningWorkflow | null>(null);
  const pollingRef = useRef<ReturnType<typeof window.setInterval> | null>(null);

  const fetchStyles = useCallback(async () => {
    if (!projectId) return;
    try {
      const data = await api.listStyleProfiles(projectId);
      setStyles(data);
      return data;
    } catch {
      return undefined;
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    fetchStyles();
  }, [fetchStyles]);

  useEffect(() => {
    if (!projectId || loading) return;
    const learningProfile = styles.find((s) => s.status === "learning");
    if (learningProfile && !workflow) {
      const progress = learningProfile.learning_progress;
      setWorkflow({
        name: learningProfile.name,
        fileName: learningProfile.source_books?.[0]?.filename || "",
        profileId: learningProfile.id,
        status: "running",
        currentStepIndex: progress?.current_step_index ?? 0,
        message: progress?.message || "正在恢复学习进度",
      });
      setViewMode("upload");
    }
  }, [styles, loading, projectId, workflow]);

  useEffect(() => {
    if (!workflow || workflow.status !== "running" || !projectId || !workflow.profileId) return;

    pollingRef.current = window.setInterval(async () => {
      try {
        const result = await api.getStyleLearningStatus(projectId, workflow.profileId);
        if (result.status === "ready") {
          setWorkflow((prev) =>
            prev
              ? {
                  ...prev,
                  status: "done",
                  currentStepIndex: STYLE_LEARNING_STEPS.length - 1,
                  message: "风格学习已完成并写入画像",
                }
              : prev
          );
          fetchStyles();
          if (pollingRef.current) {
            window.clearInterval(pollingRef.current);
            pollingRef.current = null;
          }
        } else if (result.status === "failed") {
          setWorkflow((prev) =>
            prev
              ? {
                  ...prev,
                  status: "error",
                  message: "风格学习失败",
                  error: "学习过程中发生错误",
                }
              : prev
          );
          fetchStyles();
          if (pollingRef.current) {
            window.clearInterval(pollingRef.current);
            pollingRef.current = null;
          }
        } else if (result.learning_progress) {
          setWorkflow((prev) => {
            if (!prev) return prev;
            const p = result.learning_progress!;
            const changed =
              prev.currentStepIndex !== p.current_step_index ||
              prev.message !== p.message;
            if (!changed) return prev;
            return {
              ...prev,
              currentStepIndex: p.current_step_index,
              message: p.message,
            };
          });
        }
      } catch {
        // polling error, keep trying
      }
    }, 3000);

    return () => {
      if (pollingRef.current) {
        window.clearInterval(pollingRef.current);
        pollingRef.current = null;
      }
    };
  }, [workflow?.status, workflow?.profileId, projectId, fetchStyles]);

  const handleActivate = async (profileId: string) => {
    console.log("handleActivate called, projectId:", projectId, "profileId:", profileId);
    if (!projectId) {
      console.error("projectId is undefined!");
      return;
    }
    try {
      const result = await api.activateStyleProfile(projectId, profileId);
      console.log("activate result:", result);
      await fetchStyles();
    } catch (e) {
      console.error("激活失败:", e);
    }
  };

  const handleDelete = async (profileId: string) => {
    if (!projectId) return;
    try {
      await api.deleteStyleProfile(projectId, profileId);
      if (selectedProfileId === profileId) {
        setSelectedProfileId(null);
        setViewMode("list");
      }
      await fetchStyles();
    } catch (e) {
      console.error("删除失败:", e);
    }
  };

  const handleEditorInfluence = async (profileId: string, enabled: boolean) => {
    if (!projectId) return;
    try {
      await api.setStyleProfileEditorInfluence(projectId, profileId, enabled);
      await fetchStyles();
    } catch (e) {
      console.error("切换主编影响失败:", e);
    }
  };

  const handleRollback = async (profileId: string) => {
    if (!projectId) return;
    try {
      await api.rollbackStyleProfile(projectId, profileId);
      await fetchStyles();
    } catch (e) {
      console.error("回滚失败:", e);
    }
  };

  const handleStyleUpload = useCallback(async (name: string, file: File) => {
    if (!projectId) return;
    setShowUpload(false);
    setWorkflow({
      name,
      fileName: file.name,
      profileId: "",
      status: "running",
      currentStepIndex: 0,
      message: "正在准备上传与解析",
    });
    setViewMode("upload");
    try {
      const profile = await api.learnStyleFromBook(projectId, file, name);
      setWorkflow((prev) =>
        prev
          ? {
              ...prev,
              profileId: profile.id,
              message: "风格学习已启动，正在后台处理",
            }
          : prev
      );
    } catch (error) {
      setWorkflow((prev) =>
        prev
          ? {
              ...prev,
              status: "error",
              message: "风格学习启动失败",
              error: error instanceof Error ? error.message : "未知错误",
            }
          : prev
      );
    }
  }, [projectId]);

  const selectedProfile = styles.find((s) => s.id === selectedProfileId);

  if (!projectId) return null;

  if (loading) {
    return <StyleLoadingScreen />;
  }

  return (
    <div className="flex h-full flex-col overflow-x-clip">
      <div className="border-b border-[var(--border-subtle)] bg-[var(--surface-tool)] px-3 py-4 sm:px-6">
        <div className="mx-auto flex w-full max-w-screen-2xl flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex min-w-0 items-center gap-3">
            <Feather className="h-6 w-6 shrink-0 text-[var(--color-accent)]" strokeWidth={1.8} />
            <div>
              <h1 className="text-lg font-semibold text-[var(--color-ink-strong)]">文笔工坊</h1>
              <p className="mt-0.5 text-xs text-[var(--color-ink)]">
                管理正文生成使用的风格画像 · {styles.length} 个画像，{styles.filter(profileInfluencesWriting).length} 个正在使用
              </p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {styles.length >= 2 && (
              <button
                onClick={() => setViewMode("fusion")}
                aria-pressed={viewMode === "fusion"}
                className={clsx(
                  "flex items-center gap-1.5 whitespace-nowrap rounded-md border px-3 py-2 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]",
                  viewMode === "fusion"
                    ? "border-[var(--border-emphasis)] bg-[var(--color-accent-soft)] text-[var(--color-accent)]"
                    : "border-[var(--border-subtle)] bg-[var(--surface-raised)] text-[var(--color-ink)] hover:border-[var(--border-emphasis)]"
                )}
              >
                <GitMerge className="h-3.5 w-3.5" />
                风格融合
              </button>
            )}
            {styles.length >= 2 && (
              <button
                onClick={() => setViewMode("conflicts")}
                aria-pressed={viewMode === "conflicts"}
                className={clsx(
                  "flex items-center gap-1.5 whitespace-nowrap rounded-md border px-3 py-2 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]",
                  viewMode === "conflicts"
                    ? "border-[var(--border-emphasis)] bg-[var(--color-accent-soft)] text-[var(--color-accent)]"
                    : "border-[var(--border-subtle)] bg-[var(--surface-raised)] text-[var(--color-ink)] hover:border-[var(--border-emphasis)]"
                )}
              >
                <AlertTriangle className="h-3.5 w-3.5" />
                冲突检测
              </button>
            )}
            <button
              onClick={() => setShowUpload(true)}
              className="flex items-center gap-1.5 whitespace-nowrap rounded-md bg-[var(--color-accent)] px-3 py-2 text-sm font-medium text-[var(--color-on-accent)] transition-colors hover:bg-[var(--color-accent-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)] focus-visible:ring-offset-2"
            >
              <Upload className="h-4 w-4" />
              学习新书
            </button>
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-auto px-3 py-4 sm:px-6">
        {viewMode === "list" && (
          <StyleList
            styles={styles}
            onActivate={handleActivate}
            onDelete={handleDelete}
            onEditorInfluence={handleEditorInfluence}
            onRollback={handleRollback}
            onSelect={(id) => {
              setSelectedProfileId(id);
              setViewMode("detail");
            }}
          />
        )}
        {viewMode === "detail" && selectedProfile && (
          <StyleDetail
            profile={selectedProfile}
            onBack={() => {
              setSelectedProfileId(null);
              setViewMode("list");
            }}
            onActivate={() => handleActivate(selectedProfile.id)}
            onDelete={() => handleDelete(selectedProfile.id)}
            onEditorInfluence={(enabled) => handleEditorInfluence(selectedProfile.id, enabled)}
            onRollback={() => handleRollback(selectedProfile.id)}
          />
        )}
        {viewMode === "fusion" && (
          <StyleFusionPanel
            projectId={projectId}
            styles={styles}
            onBack={() => setViewMode("list")}
            onFused={fetchStyles}
          />
        )}
        {viewMode === "conflicts" && (
          <StyleConflictPanel
            projectId={projectId}
            styles={styles}
            onBack={() => setViewMode("list")}
          />
        )}
        {viewMode === "upload" && (
          <StyleLearningWorkflowPanel
            workflow={workflow}
            onBack={() => {
              if (workflow?.status === "done" || workflow?.status === "error") {
                setWorkflow(null);
              }
              setViewMode("list");
            }}
          />
        )}
      </div>

      {showUpload && (
        <StyleUploadModal
          onClose={() => setShowUpload(false)}
          onSubmit={handleStyleUpload}
        />
      )}
    </div>
  );
}

function StyleList({
  styles,
  onActivate,
  onDelete,
  onEditorInfluence,
  onRollback,
  onSelect,
}: {
  styles: StyleProfile[];
  onActivate: (id: string) => void;
  onDelete: (id: string) => void;
  onEditorInfluence: (id: string, enabled: boolean) => void;
  onRollback: (id: string) => void;
  onSelect: (id: string) => void;
}) {
  const [deletingId, setDeletingId] = useState<string | null>(null);

  if (styles.length === 0) {
    return (
      <div className="flex flex-col items-center py-16 text-pine-700">
        <Feather className="mb-3 h-16 w-16" />
        <p className="text-sm">尚未学习任何文笔风格</p>
        <p className="mt-1 text-xs">上传你喜欢的书籍，AI 将学习其文笔并在写作中模仿</p>
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-screen-2xl space-y-4">
      {styles.map((profile) => {
        const sCfg = STYLE_STATUS_CONFIG[profile.status] || STYLE_STATUS_CONFIG.learning;
        const influencesWriting = profileInfluencesWriting(profile);
        return (
          <article
            key={profile.id}
            className="overflow-hidden rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-raised)]"
          >
            <div className="grid gap-4 border-b border-[var(--border-subtle)] p-4 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
              <div className="flex min-w-0 items-start gap-3">
                <Feather className="mt-0.5 h-5 w-5 shrink-0 text-[var(--color-accent)]" strokeWidth={1.8} />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="text-base font-semibold text-[var(--color-ink-strong)]">{profile.name}</h2>
                  <span className={clsx("rounded-full border px-1.5 py-0.5 text-[10px]", sCfg.color)}>
                    {sCfg.label}
                  </span>
                    <span
                      className={clsx(
                        "rounded-full border px-2 py-0.5 text-[10px] font-medium",
                        influencesWriting
                          ? "border-[var(--border-emphasis)] bg-[var(--color-accent-soft)] text-[var(--color-ink)]"
                          : "border-[var(--border-subtle)] bg-[var(--surface-paper)] text-[var(--color-ink-muted)]",
                      )}
                    >
                      {influencesWriting ? "正在用于正文" : "未用于正文"}
                    </span>
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[var(--color-ink-muted)]">
                    {profile.source_books.slice(0, 2).map((book) => (
                      <span key={book.id} className="flex min-w-0 items-center gap-1.5">
                        <BookOpen className="h-3.5 w-3.5 shrink-0" />
                        <span className="max-w-64 truncate">{book.filename}</span>
                      </span>
                    ))}
                    {profile.source_books.length > 2 && <span>另有 {profile.source_books.length - 2} 本来源</span>}
                    {profile.confidence > 0 && <span className="tabular-nums">置信度 {Math.round(profile.confidence * 100)}%</span>}
                    <span className="tabular-nums">版本 v{profile.version}</span>
                  </div>
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-2 lg:justify-end">
                {profile.status === "ready" && (
                  <button
                    onClick={() => {
                      if (influencesWriting) {
                        onEditorInfluence(profile.id, false);
                      } else if (profile.frozen) {
                        onEditorInfluence(profile.id, true);
                      } else {
                        onActivate(profile.id);
                      }
                    }}
                    className={clsx(
                      "flex min-h-10 items-center gap-1.5 whitespace-nowrap rounded-md px-3 py-1.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)] active:translate-y-px",
                      influencesWriting
                        ? "border border-[var(--border-subtle)] bg-[var(--surface-paper)] text-[var(--color-ink)] hover:border-[var(--border-emphasis)]"
                        : "bg-[var(--color-accent)] text-[var(--color-on-accent)] hover:bg-[var(--color-accent-hover)]",
                    )}
                  >
                    <Power className="h-3.5 w-3.5" />
                    {influencesWriting ? "停止用于正文" : "用于正文"}
                  </button>
                )}
                {profile.version > 1 && (
                  <button
                    onClick={() => onRollback(profile.id)}
                    className="flex items-center gap-1 whitespace-nowrap rounded-md border border-[var(--border-subtle)] bg-[var(--surface-paper)] px-2.5 py-1.5 text-xs text-[var(--color-ink)] transition-colors hover:border-[var(--border-emphasis)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
                  >
                    <RotateCcw className="h-3.5 w-3.5" /> 回滚
                  </button>
                )}
                <button
                  onClick={() => setDeletingId(profile.id)}
                  className="flex items-center gap-1 whitespace-nowrap rounded-md border border-[var(--border-subtle)] bg-[var(--surface-paper)] px-2.5 py-1.5 text-xs text-[var(--color-ink-muted)] transition-colors hover:border-[var(--color-error)] hover:text-[var(--color-error)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-error)]"
                >
                  <Trash2 className="h-3.5 w-3.5" /> 删除
                </button>
                <button
                  onClick={() => onSelect(profile.id)}
                  className="flex items-center gap-1 whitespace-nowrap rounded-md border border-[var(--border-emphasis)] bg-[var(--surface-tool)] px-2.5 py-1.5 text-xs font-medium text-[var(--color-accent)] transition-colors hover:bg-[var(--color-accent-soft)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
                >
                  查看详情 <ChevronRight className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>

            {profile.style_embedding && (
              <div className="p-4">
                <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
                  <div>
                    <h3 className="text-sm font-semibold text-[var(--color-ink-strong)]">八维风格画像</h3>
                    <p className="mt-0.5 text-xs text-[var(--color-ink-muted)]">按叙事气质与文本节奏分组，便于横向比较</p>
                  </div>
                  <span className="font-mono text-[10px] text-[var(--color-ink-muted)]">归一化刻度 0—100</span>
                </div>
                <EmbeddingBarChart embedding={profile.style_embedding} compact />
              </div>
            )}
          </article>
        );
      })}

      {deletingId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="w-80 rounded-xl border border-pine-200/60 bg-white/50 p-5">
            <h3 className="text-sm font-semibold text-pine-700">确认删除</h3>
            <p className="mt-2 text-xs text-pine-700">删除后该风格画像将被移除，AI 生成时将不再参考此风格。</p>
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => setDeletingId(null)}
                className="rounded-lg px-3 py-1.5 text-sm text-pine-700 hover:text-pine-700"
              >
                取消
              </button>
              <button
                onClick={() => {
                  onDelete(deletingId);
                  setDeletingId(null);
                }}
                className="rounded-lg bg-crimson-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-crimson-400"
              >
                删除
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function StyleDetail({
  profile,
  onBack,
  onActivate,
  onDelete,
  onEditorInfluence,
  onRollback,
}: {
  profile: StyleProfile;
  onBack: () => void;
  onActivate: () => void | Promise<void>;
  onDelete: () => void | Promise<void>;
  onEditorInfluence: (enabled: boolean) => void | Promise<void>;
  onRollback: () => void | Promise<void>;
}) {
  const [tab, setTab] = useState<DetailTab>("features");
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const influencesWriting = profileInfluencesWriting(profile);
  const statusConfig = STYLE_STATUS_CONFIG[profile.status] || STYLE_STATUS_CONFIG.learning;
  const actionsDisabled = pendingAction !== null;

  const runAction = async (key: string, action: () => void | Promise<void>) => {
    if (actionsDisabled) return;
    setPendingAction(key);
    try {
      await action();
    } finally {
      setPendingAction(null);
    }
  };

  const handleWritingToggle = () => {
    if (influencesWriting) {
      return runAction("writing", () => onEditorInfluence(false));
    }
    if (profile.frozen) {
      return runAction("writing", () => onEditorInfluence(true));
    }
    return runAction("writing", onActivate);
  };

  const neutralButtonClass = "inline-flex min-h-11 items-center justify-center gap-1.5 whitespace-nowrap rounded-md border border-[var(--border-subtle)] bg-[var(--surface-paper)] px-3 py-2 text-sm text-[var(--color-ink)] transition-colors hover:border-[var(--border-emphasis)] hover:bg-[var(--surface-raised)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)] focus-visible:ring-offset-2 active:translate-y-px disabled:cursor-not-allowed disabled:opacity-50";
  const primaryButtonClass = "inline-flex min-h-11 items-center justify-center gap-1.5 whitespace-nowrap rounded-md bg-[var(--color-accent)] px-3 py-2 text-sm font-medium text-[var(--color-on-accent)] transition-colors hover:bg-[var(--color-accent-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)] focus-visible:ring-offset-2 active:translate-y-px disabled:cursor-not-allowed disabled:opacity-50";

  return (
    <div className="mx-auto w-full max-w-screen-2xl space-y-4">
      <header className="border-y border-[var(--border-subtle)] bg-[var(--surface-raised)] px-3 py-4 sm:px-5">
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
          <div className="flex min-w-0 items-start gap-3">
            <button
              onClick={onBack}
              aria-label="返回画像列表"
              className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-md text-[var(--color-ink-muted)] transition-colors hover:bg-[var(--surface-tool)] hover:text-[var(--color-ink-strong)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)] active:translate-y-px"
            >
              <ChevronRight className="h-5 w-5 rotate-180" />
            </button>
            <Feather className="mt-2.5 h-5 w-5 shrink-0 text-[var(--color-accent)]" strokeWidth={1.8} />
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="min-w-0 [overflow-wrap:anywhere] text-xl font-semibold text-[var(--color-ink-strong)]">
                  {profile.name}
                </h2>
                <span className="rounded-full border border-[var(--border-emphasis)] bg-[var(--color-accent-soft)] px-2 py-0.5 text-[10px] font-medium text-[var(--color-ink)]">
                  {statusConfig.label}
                </span>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[var(--color-ink-muted)]">
                {profile.source_books.slice(0, 2).map((book) => (
                  <span key={book.id} className="flex min-w-0 items-center gap-1.5">
                    <BookOpen className="h-3.5 w-3.5 shrink-0" />
                    <span className="max-w-64 truncate">{book.filename}</span>
                  </span>
                ))}
                {profile.source_books.length > 2 && <span>另有 {profile.source_books.length - 2} 本来源</span>}
                {profile.confidence > 0 && (
                  <span className="tabular-nums">置信度 {Math.round(profile.confidence * 100)}%</span>
                )}
                <span className="font-mono tabular-nums">版本 v{profile.version}</span>
              </div>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2 lg:justify-end">
            {profile.version > 1 && (
              <button
                onClick={() => runAction("rollback", onRollback)}
                disabled={actionsDisabled}
                className={neutralButtonClass}
              >
                {pendingAction === "rollback" ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCcw className="h-4 w-4" />}
                回滚版本
              </button>
            )}
            <button
              onClick={() => setConfirmingDelete(true)}
              disabled={actionsDisabled}
              className="inline-flex min-h-11 items-center justify-center gap-1.5 whitespace-nowrap rounded-md border border-[var(--border-subtle)] bg-[var(--surface-paper)] px-3 py-2 text-sm text-[var(--color-ink-muted)] transition-colors hover:border-[var(--color-error)] hover:text-[var(--color-error)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-error)] focus-visible:ring-offset-2 active:translate-y-px disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Trash2 className="h-4 w-4" /> 删除画像
            </button>
          </div>
        </div>

        {confirmingDelete && (
          <div role="alert" className="mt-4 flex flex-col gap-3 border border-[var(--color-error)] bg-[var(--color-error-soft)] p-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-medium text-[var(--color-ink-strong)]">删除“{profile.name}”？</p>
              <p className="mt-1 text-xs text-[var(--color-ink-muted)]">画像及其学习结果会被移除，这项操作无法撤销。</p>
            </div>
            <div className="flex flex-wrap gap-2">
              <button onClick={() => setConfirmingDelete(false)} disabled={actionsDisabled} className={neutralButtonClass}>取消</button>
              <button
                onClick={() => runAction("delete", onDelete)}
                disabled={actionsDisabled}
                className="inline-flex min-h-11 items-center justify-center gap-1.5 whitespace-nowrap rounded-md bg-[var(--color-error)] px-3 py-2 text-sm font-medium text-[var(--color-on-accent)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-error)] focus-visible:ring-offset-2 active:translate-y-px disabled:cursor-not-allowed disabled:opacity-50"
              >
                {pendingAction === "delete" && <Loader2 className="h-4 w-4 animate-spin" />}
                确认删除
              </button>
            </div>
          </div>
        )}
      </header>

      <section aria-labelledby="style-usage-title" className="border-y border-[var(--border-subtle)] bg-[var(--surface-tool)]">
        <div className="grid gap-4 p-4 sm:p-5 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <Power className="h-4 w-4 text-[var(--color-accent)]" />
              <h3 id="style-usage-title" className="text-sm font-semibold text-[var(--color-ink-strong)]">正文使用</h3>
              <span className={clsx(
                "rounded-full border px-2 py-0.5 text-[10px] font-medium",
                influencesWriting
                  ? "border-[var(--border-emphasis)] bg-[var(--color-accent-soft)] text-[var(--color-ink)]"
                  : "border-[var(--border-subtle)] bg-[var(--surface-paper)] text-[var(--color-ink-muted)]",
              )}>
                {influencesWriting ? "正在使用" : "未使用"}
              </span>
            </div>
            <p className="max-w-[65ch] text-sm leading-6 text-[var(--color-ink)]">
              {influencesWriting
                ? "章节 Writer 会在生成前加载这份画像，作为正文的风格依据。"
                : "章节 Writer 当前不会加载这份画像，已有正文不会因此改变。"}
            </p>
          </div>
          {profile.status === "ready" && (
            <button
              onClick={handleWritingToggle}
              disabled={actionsDisabled}
              className={influencesWriting ? neutralButtonClass : primaryButtonClass}
            >
              {pendingAction === "writing" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Power className="h-4 w-4" />}
              {influencesWriting ? "停止用于正文" : "用于正文"}
            </button>
          )}
        </div>
      </section>

      <section className="min-w-0">
        <div className="overflow-x-auto border-b border-[var(--border-subtle)] bg-[var(--surface-tool)] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden" role="tablist" aria-label="画像详情">
          <div className="flex min-w-max items-center px-1">
            {DETAIL_TABS.map((item) => (
            <button
                key={item.id}
                id={`style-tab-${item.id}`}
                role="tab"
                aria-selected={tab === item.id}
                aria-controls={`style-panel-${item.id}`}
                onClick={() => setTab(item.id)}
              className={clsx(
                  "relative min-h-11 whitespace-nowrap px-3 py-2 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--color-accent)] active:translate-y-px sm:px-4",
                  tab === item.id
                    ? "text-[var(--color-ink-strong)] after:absolute after:inset-x-3 after:bottom-0 after:h-0.5 after:bg-[var(--color-accent)]"
                    : "text-[var(--color-ink)] hover:bg-[var(--surface-raised)] hover:text-[var(--color-ink-strong)]",
              )}
            >
                {item.label}
            </button>
            ))}
          </div>
        </div>

        <div
          id={`style-panel-${tab}`}
          role="tabpanel"
          aria-labelledby={`style-tab-${tab}`}
          className="min-h-[28rem] bg-[var(--surface-paper)] px-3 py-5 sm:px-5 sm:py-6"
        >
        {tab === "features" && (
          <div className="space-y-6">
            <div className="border-b border-[var(--border-subtle)] pb-3">
              <h3 className="text-base font-semibold text-[var(--color-ink-strong)]">风格特征</h3>
              <p className="mt-1 text-sm text-[var(--color-ink-muted)]">从来源文本中提炼出的可读特征，以及实际传给生成链路的风格提示。</p>
            </div>
            <dl className="grid lg:grid-cols-2">
            {(Object.keys(STYLE_DIMENSION_LABELS) as string[]).map((key) => {
              const value = profile.style_features[key as keyof typeof profile.style_features];
              if (!value || typeof value !== "string") return null;
              return (
                  <div key={key} className="border-t border-[var(--border-subtle)] py-4 lg:odd:pr-6 lg:even:border-l lg:even:pl-6">
                    <dt className="text-xs font-medium text-[var(--color-ink-muted)]">{STYLE_DIMENSION_LABELS[key]}</dt>
                    <dd className="mt-2 whitespace-pre-wrap text-sm leading-6 text-[var(--color-ink)]">{value}</dd>
                </div>
              );
            })}
            </dl>

            {(profile.style_features.signature_phrases.length > 0 || profile.style_features.avoid_patterns.length > 0) && (
              <div className="grid border-t border-[var(--border-subtle)] lg:grid-cols-2">
                <section className="py-4 lg:pr-6">
                  <h4 className="text-sm font-semibold text-[var(--color-ink-strong)]">标志性表达</h4>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {profile.style_features.signature_phrases.length > 0 ? profile.style_features.signature_phrases.map((phrase, i) => (
                      <span key={i} className="rounded bg-[var(--color-accent-soft)] px-2 py-1 text-xs text-[var(--color-ink)]">{phrase}</span>
                    )) : <span className="text-sm text-[var(--color-ink-muted)]">暂无标志性表达</span>}
                  </div>
                </section>
                <section className="border-t border-[var(--border-subtle)] py-4 lg:border-l lg:border-t-0 lg:pl-6">
                  <h4 className="text-sm font-semibold text-[var(--color-ink-strong)]">应避免模式</h4>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {profile.style_features.avoid_patterns.length > 0 ? profile.style_features.avoid_patterns.map((pattern, i) => (
                      <span key={i} className="rounded bg-[var(--color-error-soft)] px-2 py-1 text-xs text-[var(--color-error)]">{pattern}</span>
                    )) : <span className="text-sm text-[var(--color-ink-muted)]">暂无避免模式</span>}
                  </div>
                </section>
              </div>
            )}

          {profile.style_prompt && (
              <section className="border-t border-[var(--border-subtle)] pt-4">
                <h4 className="text-sm font-semibold text-[var(--color-ink-strong)]">传给 Writer 的风格提示</h4>
                <p className="mt-3 whitespace-pre-wrap bg-[var(--surface-tool)] p-4 text-sm leading-6 text-[var(--color-ink)]">{profile.style_prompt}</p>
              </section>
          )}
          </div>
        )}

        {tab === "embedding" && (
          <div className="space-y-5">
            <div className="border-b border-[var(--border-subtle)] pb-3">
              <h3 className="text-base font-semibold text-[var(--color-ink-strong)]">八维画像</h3>
              <p className="mt-1 text-sm text-[var(--color-ink-muted)]">数值用于比较不同画像的倾向，不代表质量高低。刻度统一为 0—100。</p>
            </div>
            <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_20rem]">
              <EmbeddingBarChart embedding={profile.style_embedding} compact />
              <section className="border-t border-[var(--border-subtle)] pt-4 xl:border-l xl:border-t-0 xl:pl-6 xl:pt-0">
                <h4 className="text-sm font-semibold text-[var(--color-ink-strong)]">轮廓预览</h4>
                <p className="mt-1 text-xs text-[var(--color-ink-muted)]">用于观察各维度之间的相对关系</p>
                <EmbeddingRadarChart embedding={profile.style_embedding} />
              </section>
            </div>
          </div>
        )}

        {tab === "persona" && (
          <div className="space-y-6">
            <div className="border-b border-[var(--border-subtle)] pb-3">
              <h3 className="text-base font-semibold text-[var(--color-ink-strong)]">创作人格</h3>
              <p className="mt-1 text-sm text-[var(--color-ink-muted)]">画像在创作时采取的判断方式、表达习惯和不可越过的规则。</p>
            </div>
          {profile.persona_card && (
            <>
                <dl className="grid lg:grid-cols-2">
                  {[
                    ["身份认知", profile.persona_card.identity],
                    ["决策模式", profile.persona_card.decision_pattern],
                    ["表达风格", profile.persona_card.expression_style],
                    ["人际行为", profile.persona_card.interpersonal_behavior],
                  ].filter((entry) => entry[1]).map(([label, value]) => (
                    <div key={label} className="border-t border-[var(--border-subtle)] py-4 lg:odd:pr-6 lg:even:border-l lg:even:pl-6">
                      <dt className="text-xs font-medium text-[var(--color-ink-muted)]">{label}</dt>
                      <dd className="mt-2 text-sm leading-6 text-[var(--color-ink)]">{value}</dd>
                    </div>
                  ))}
                </dl>
              {profile.persona_card.hard_rules.length > 0 && (
                  <section className="border-t border-[var(--border-subtle)] pt-4">
                    <h4 className="text-sm font-semibold text-[var(--color-ink-strong)]">硬规则</h4>
                    <div className="mt-2">
                    {profile.persona_card.hard_rules.map((rule, i) => (
                        <div key={i} className="flex items-start gap-2 border-t border-[var(--border-subtle)] py-3 first:border-t-0">
                          <Shield className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-error)]" />
                          <span className="text-sm leading-6 text-[var(--color-ink)]">{rule}</span>
                      </div>
                    ))}
                  </div>
                  </section>
              )}
            </>
          )}
          </div>
        )}

        {tab === "samples" && (
          <div className="space-y-6">
            <div className="border-b border-[var(--border-subtle)] pb-3">
              <h3 className="text-base font-semibold text-[var(--color-ink-strong)]">范例片段</h3>
              <p className="mt-1 text-sm text-[var(--color-ink-muted)]">从来源中抽取的代表性片段，用于校准画像和人工复核。</p>
            </div>
          {profile.sample_passages.length === 0 && (
              <p className="py-10 text-center text-sm text-[var(--color-ink-muted)]">暂无范例片段</p>
          )}
          {Object.entries(
            profile.sample_passages.reduce<Record<string, StyleProfile["sample_passages"]>>((acc, p) => {
              const cat = p.category || "other";
              if (!acc[cat]) acc[cat] = [];
              acc[cat].push(p);
              return acc;
            }, {})
          ).map(([category, passages]) => (
              <section key={category} className="border-t border-[var(--border-subtle)] pt-4 first:border-t-0 first:pt-0">
                <h4 className="text-sm font-semibold text-[var(--color-ink-strong)]">
                {PASSAGE_CATEGORY_LABELS[category] || category}（{passages.length}）
                </h4>
                <div className="mt-2">
                {passages.map((passage) => (
                    <blockquote key={passage.id} className="border-t border-[var(--border-subtle)] py-3 first:border-t-0">
                      <p className="whitespace-pre-wrap text-sm leading-7 text-[var(--color-ink)]">{passage.text}</p>
                    </blockquote>
                ))}
              </div>
              </section>
          ))}
          </div>
        )}

        {tab === "statistics" && (
          <div className="space-y-6">
            <div className="border-b border-[var(--border-subtle)] pb-3">
              <h3 className="text-base font-semibold text-[var(--color-ink-strong)]">统计与演变</h3>
              <p className="mt-1 text-sm text-[var(--color-ink-muted)]">来源语料的基础统计，以及学习过程识别到的风格变化。</p>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4">
            <StatTile label="总字数" value={profile.style_statistics.global.total_chars.toLocaleString()} />
            <StatTile label="段落数" value={profile.style_statistics.global.paragraph_count.toString()} />
            <StatTile label="章节数" value={profile.style_statistics.global.chapter_count.toString()} />
            <StatTile label="风格簇" value={profile.style_statistics.evolution.is_multi_style ? "多簇" : "单簇"} />
          </div>

            <section className="border-t border-[var(--border-subtle)] pt-4">
              <h4 className="text-sm font-semibold text-[var(--color-ink-strong)]">句长与段长</h4>
              <dl className="mt-2 grid grid-cols-2 gap-x-6 md:grid-cols-4">
              <div>
                  <dt className="text-xs text-[var(--color-ink-muted)]">平均句长</dt>
                  <dd className="mt-1 font-mono text-sm tabular-nums text-[var(--color-ink-strong)]">{profile.style_statistics.global.sentence_length.mean.toFixed(1)}</dd>
              </div>
              <div>
                  <dt className="text-xs text-[var(--color-ink-muted)]">句长波动</dt>
                  <dd className="mt-1 font-mono text-sm tabular-nums text-[var(--color-ink-strong)]">{profile.style_statistics.global.sentence_length.stdev.toFixed(1)}</dd>
              </div>
              <div>
                  <dt className="text-xs text-[var(--color-ink-muted)]">平均段长</dt>
                  <dd className="mt-1 font-mono text-sm tabular-nums text-[var(--color-ink-strong)]">{profile.style_statistics.global.paragraph_length.mean.toFixed(1)}</dd>
              </div>
              <div>
                  <dt className="text-xs text-[var(--color-ink-muted)]">段长波动</dt>
                  <dd className="mt-1 font-mono text-sm tabular-nums text-[var(--color-ink-strong)]">{profile.style_statistics.global.paragraph_length.stdev.toFixed(1)}</dd>
              </div>
              </dl>
            </section>

            <section className="border-t border-[var(--border-subtle)] pt-4">
              <h4 className="text-sm font-semibold text-[var(--color-ink-strong)]">高频词</h4>
              <div className="mt-3 flex flex-wrap gap-2">
              {profile.style_statistics.global.word_freq_top.slice(0, 8).map((item, index) => (
                  <span key={index} className="rounded bg-[var(--surface-tool)] px-2 py-1 text-xs text-[var(--color-ink)]">
                  {item.word}×{item.count}
                </span>
              ))}
            </div>
            </section>

            <section className="border-t border-[var(--border-subtle)] pt-4">
              <h4 className="text-sm font-semibold text-[var(--color-ink-strong)]">演变摘要</h4>
              <p className="mt-3 max-w-[75ch] text-sm leading-6 text-[var(--color-ink)]">{profile.style_statistics.evolution.explanation || "暂无演变说明"}</p>
              <p className="mt-2 max-w-[75ch] text-sm leading-6 text-[var(--color-ink-muted)]">{profile.style_statistics.evolution.recommended_usage || "暂无推荐用法"}</p>
            {profile.style_statistics.evolution.change_points.length > 0 && (
                <p className="mt-3 text-sm text-[var(--color-accent)]">
                变化点：{profile.style_statistics.evolution.change_points.map((item) => String(item)).join("、")}
              </p>
            )}
            </section>
          </div>
        )}
        </div>
      </section>
    </div>
  );
}

function StatTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="border-t border-[var(--border-subtle)] px-3 py-4 first:pl-0">
      <div className="text-xs text-[var(--color-ink-muted)]">{label}</div>
      <div className="mt-1 font-mono text-base font-semibold tabular-nums text-[var(--color-ink-strong)]">{value}</div>
    </div>
  );
}

function EmbeddingBarChart({ embedding, compact = false }: { embedding: StyleEmbedding; compact?: boolean }) {
  const dims = Object.entries(EMBEDDING_DIMENSION_LABELS) as [keyof StyleEmbedding, string][];

  if (compact) {
    return (
      <div className="grid gap-3 xl:grid-cols-2">
        {EMBEDDING_GROUPS.map((group) => (
          <section
            key={group.label}
            className="min-w-0 rounded-md border border-[var(--border-subtle)] bg-[var(--surface-paper)] p-3"
          >
            <div className="mb-3 flex flex-col gap-1 border-b border-[var(--border-subtle)] pb-2 sm:flex-row sm:items-baseline sm:justify-between sm:gap-3">
              <h4 className="text-xs font-semibold text-[var(--color-ink-strong)]">{group.label}</h4>
              <span className="text-[10px] text-[var(--color-ink-muted)]">{group.description}</span>
            </div>
            <div className="grid gap-x-4 gap-y-3 sm:grid-cols-2">
              {group.dimensions.map((key) => {
                const value = Math.max(0, Math.min(1, embedding[key] ?? 0));
                const percentage = Math.round(value * 100);
                return (
                  <div key={key} className="min-w-0">
                    <div className="mb-1.5 flex items-center justify-between gap-2">
                      <span className="truncate text-xs text-[var(--color-ink)]">
                        {EMBEDDING_DIMENSION_LABELS[key]}
                      </span>
                      <span className="shrink-0 font-mono text-[11px] font-semibold tabular-nums text-[var(--color-ink-strong)]">
                        {percentage}
                      </span>
                    </div>
                    <div
                      className="h-1.5 overflow-hidden rounded-full bg-[var(--color-accent-soft)]"
                      role="meter"
                      aria-label={EMBEDDING_DIMENSION_LABELS[key]}
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-valuenow={percentage}
                    >
                      <div
                        className="h-full rounded-full bg-[var(--color-accent)]"
                        style={{ width: `${percentage}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          </section>
        ))}
      </div>
    );
  }

  return (
    <div className="space-y-1.5">
      {dims.map(([key, label]) => {
        const value = Math.max(0, Math.min(1, embedding[key] ?? 0));
        return (
          <div key={key} className="flex items-center gap-2">
            <span className="w-20 shrink-0 text-[10px] text-[var(--color-ink-muted)]">
              {label}
            </span>
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-[var(--color-accent-soft)]">
              <div
                className="h-full rounded-full bg-[var(--color-accent)]"
                style={{ width: `${Math.round(value * 100)}%` }}
              />
            </div>
            <span className="w-10 shrink-0 text-right font-mono text-[10px] tabular-nums text-[var(--color-ink-muted)]">
              {value.toFixed(2)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function EmbeddingRadarChart({ embedding }: { embedding: StyleEmbedding }) {
  const dims = Object.entries(EMBEDDING_DIMENSION_LABELS) as [keyof StyleEmbedding, string][];
  const size = 280;
  const cx = size / 2;
  const cy = size / 2;
  const r = 110;
  const n = dims.length;
  const angleStep = (2 * Math.PI) / n;

  const getPoint = (index: number, value: number) => {
    const angle = angleStep * index - Math.PI / 2;
    return {
      x: cx + r * value * Math.cos(angle),
      y: cy + r * value * Math.sin(angle),
    };
  };

  const points = dims.map(([key], i) => getPoint(i, embedding[key] ?? 0));
  const pathData = points.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x} ${p.y}`).join(" ") + " Z";

  return (
    <div className="flex flex-col items-center">
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        role="img"
        aria-label="八维风格画像雷达图"
        className="max-w-full"
      >
        {[0.25, 0.5, 0.75, 1.0].map((level) => (
          <polygon
            key={level}
            points={dims.map((_, i) => {
              const p = getPoint(i, level);
              return `${p.x},${p.y}`;
            }).join(" ")}
            fill="none"
            stroke="var(--border-subtle)"
            strokeWidth={1}
          />
        ))}
        {dims.map(([key, label], i) => {
          const p = getPoint(i, 1.15);
          return (
            <text key={key} x={p.x} y={p.y} textAnchor="middle" dominantBaseline="middle" fill="var(--color-ink-muted)" className="text-[9px]">
              {label}
            </text>
          );
        })}
        {dims.map((_, i) => {
          const outer = getPoint(i, 1);
          return (
            <line key={i} x1={cx} y1={cy} x2={outer.x} y2={outer.y} stroke="var(--border-subtle)" strokeWidth={1} />
          );
        })}
        <path d={pathData} fill="var(--color-accent-soft)" stroke="var(--color-accent)" strokeWidth={1.5} />
        {points.map((p, i) => (
          <circle key={i} cx={p.x} cy={p.y} r={3} fill="var(--color-accent)" />
        ))}
      </svg>
    </div>
  );
}

function StyleFusionPanel({
  projectId,
  styles,
  onBack,
  onFused,
}: {
  projectId: string;
  styles: StyleProfile[];
  onBack: () => void;
  onFused: () => void;
}) {
  const readyStyles = styles.filter((s) => s.status === "ready");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [weights, setWeights] = useState<number[]>([]);
  const [mode, setMode] = useState<StyleFusionRequest["mode"]>("negotiation");
  const [dominantId, setDominantId] = useState<string>("");
  const [merging, setMerging] = useState(false);
  const [conflictReport, setConflictReport] = useState<StyleConflictReport | null>(null);

  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      const next = prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id];
      const w = next.map(() => 1.0 / next.length);
      setWeights(w);
      return next;
    });
  };

  const handleCompare = async () => {
    if (selectedIds.length < 2) return;
    try {
      const report = await api.compareStyleProfiles(projectId, selectedIds.slice(0, 2));
      setConflictReport(report);
    } catch {}
  };

  const handleMerge = async () => {
    if (selectedIds.length < 2) return;
    setMerging(true);
    try {
      await api.mergeStyleProfiles(projectId, {
        source_profile_ids: selectedIds,
        weights,
        mode,
        dominant_profile_id: mode === "arbitration" ? dominantId : undefined,
      });
      onFused();
      onBack();
    } catch {
    } finally {
      setMerging(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <button onClick={onBack} className="text-pine-700 hover:text-pine-700">
          <ChevronRight className="h-4 w-4 rotate-180" />
        </button>
        <h2 className="text-sm font-semibold text-pine-700">风格融合</h2>
      </div>

      <div>
        <h3 className="mb-2 text-xs font-medium text-pine-700">选择源风格（至少2个）</h3>
        <div className="space-y-2">
          {readyStyles.map((s) => (
            <button
              key={s.id}
              onClick={() => toggleSelect(s.id)}
              className={clsx(
                "flex w-full items-center gap-3 rounded-lg border p-3 text-left transition-colors",
                selectedIds.includes(s.id)
                  ? "border-violet-500/40 bg-violet-500/10"
                  : "border-pine-200/40 bg-white/50 hover:border-pine-200/60"
              )}
            >
              <div className={clsx("flex h-6 w-6 items-center justify-center rounded-full border", selectedIds.includes(s.id) ? "border-violet-500 bg-violet-500/30" : "border-pine-200")}>
                {selectedIds.includes(s.id) && <span className="text-[10px] text-violet-300">✓</span>}
              </div>
              <span className="text-xs text-pine-700">{s.name}</span>
              {s.style_embedding && (
                <span className="ml-auto text-[9px] text-pine-700">
                  情感{s.style_embedding.emotionality.toFixed(2)} · 句式{s.style_embedding.sentence_complexity.toFixed(2)}
                </span>
              )}
            </button>
          ))}
        </div>
      </div>

      {selectedIds.length >= 2 && (
        <>
          <div>
            <h3 className="mb-2 text-xs font-medium text-pine-700">融合模式</h3>
            <div className="grid grid-cols-2 gap-2">
              {([
                { key: "negotiation", label: "协商模式", desc: "取中间值，创造折衷叙事声音" },
                { key: "arbitration", label: "仲裁模式", desc: "主导风格优先，辅助风格补充" },
                { key: "isolation", label: "隔离模式", desc: "不同风格分配给不同叙事层级" },
                { key: "supervision", label: "监督模式", desc: "完全手动控制，AI仅执行" },
              ] as const).map((m) => (
                <button
                  key={m.key}
                  onClick={() => setMode(m.key)}
                  className={clsx(
                    "rounded-lg border p-2 text-left transition-colors",
                    mode === m.key ? "border-violet-500/40 bg-violet-500/10" : "border-pine-200/40 hover:border-pine-200/60"
                  )}
                >
                  <span className="text-xs font-medium text-pine-700">{m.label}</span>
                  <p className="mt-0.5 text-[10px] text-pine-700">{m.desc}</p>
                </button>
              ))}
            </div>
          </div>

          {mode === "arbitration" && (
            <div>
              <h3 className="mb-2 text-xs font-medium text-pine-700">主导风格</h3>
              <div className="flex gap-2">
                {readyStyles
                  .filter((s) => selectedIds.includes(s.id))
                  .map((s) => (
                    <button
                      key={s.id}
                      onClick={() => setDominantId(s.id)}
                      className={clsx(
                        "rounded-lg border px-3 py-1.5 text-xs transition-colors",
                        dominantId === s.id ? "border-violet-500/40 bg-violet-500/15 text-violet-400" : "border-pine-200 text-pine-700"
                      )}
                    >
                      {s.name}
                    </button>
                  ))}
              </div>
            </div>
          )}

          <div className="flex gap-2">
            <button
              onClick={handleCompare}
              className="flex items-center gap-1.5 rounded-lg border border-pine-200 px-3 py-1.5 text-xs text-pine-700 hover:border-magic-500/30 hover:text-magic-400"
            >
              <AlertTriangle className="h-3.5 w-3.5" />
              检测冲突
            </button>
            <button
              onClick={handleMerge}
              disabled={merging}
              className="flex items-center gap-1.5 rounded-lg bg-violet-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-violet-400 disabled:opacity-50"
            >
              {merging ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <GitMerge className="h-3.5 w-3.5" />}
              {merging ? "融合中..." : "开始融合"}
            </button>
          </div>

          {conflictReport && (
            <div className="rounded-xl border border-magic-500/20 bg-magic-500/5 p-4">
              <h3 className="text-xs font-medium text-magic-400">
                冲突报告 · 兼容度 {Math.round(conflictReport.compatibility_score * 100)}%
              </h3>
              <div className="mt-2 space-y-1">
                {conflictReport.conflicts.map((c, i) => (
                  <div key={i} className="flex items-center gap-2">
                    <span className={clsx("rounded-full border px-1.5 py-0.5 text-[9px]", CONFLICT_LEVEL_COLORS[c.level] || CONFLICT_LEVEL_COLORS.L2)}>
                      {c.level}
                    </span>
                    <span className="text-xs text-pine-700">{c.dimension_label}</span>
                    <span className="text-[10px] text-pine-700">Δ={c.delta.toFixed(2)}</span>
                  </div>
                ))}
                {conflictReport.conflicts.length === 0 && (
                  <p className="text-xs text-emerald-400">未检测到风格冲突</p>
                )}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function StyleConflictPanel({
  projectId,
  styles,
  onBack,
}: {
  projectId: string;
  styles: StyleProfile[];
  onBack: () => void;
}) {
  const [conflictReports, setConflictReports] = useState<StyleConflictReport[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedProfileId, setSelectedProfileId] = useState<string>("");

  const handleCheck = async () => {
    if (!selectedProfileId) return;
    setLoading(true);
    try {
      const reports = await api.getStyleConflicts(projectId, selectedProfileId);
      setConflictReports(reports);
    } catch {
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <button onClick={onBack} className="text-pine-700 hover:text-pine-700">
          <ChevronRight className="h-4 w-4 rotate-180" />
        </button>
        <h2 className="text-sm font-semibold text-pine-700">风格冲突检测</h2>
      </div>

      <div className="flex items-end gap-2">
        <div className="flex-1">
          <label className="mb-1 block text-xs text-pine-700">选择基准风格</label>
          <select
            value={selectedProfileId}
            onChange={(e) => setSelectedProfileId(e.target.value)}
            className="w-full rounded-lg border border-pine-200 bg-white/50 px-3 py-2 text-sm text-pine-700 outline-none focus:border-violet-500/50"
          >
            <option value="">请选择</option>
            {styles.map((s) => (
              <option key={s.id} value={s.id}>{s.name}</option>
            ))}
          </select>
        </div>
        <button
          onClick={handleCheck}
          disabled={!selectedProfileId || loading}
          className="flex items-center gap-1.5 rounded-lg bg-magic-500 px-3 py-2 text-xs font-medium text-white hover:bg-magic-400 disabled:opacity-50"
        >
          {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <AlertTriangle className="h-3.5 w-3.5" />}
          检测冲突
        </button>
      </div>

      {conflictReports.length > 0 && (
        <div className="space-y-3">
          {conflictReports.map((report, ri) => (
            <div key={ri} className="rounded-xl border border-pine-200/60 bg-white/50 p-4">
              <div className="flex items-center gap-2">
                <span className="text-xs font-medium text-pine-700">{report.profile_a_name}</span>
                <span className="text-[10px] text-pine-700">vs</span>
                <span className="text-xs font-medium text-pine-700">{report.profile_b_name}</span>
                <span className="ml-auto rounded-full border border-pine-200 px-2 py-0.5 text-[10px] text-pine-700">
                  兼容度 {Math.round(report.compatibility_score * 100)}%
                </span>
              </div>
              <div className="mt-2 space-y-1">
                {report.conflicts.map((c, ci) => (
                  <div key={ci} className="flex items-center gap-2 rounded-lg border border-pine-200/40 bg-white/50 p-2">
                    <span className={clsx("rounded-full border px-1.5 py-0.5 text-[9px] font-medium", CONFLICT_LEVEL_COLORS[c.level] || CONFLICT_LEVEL_COLORS.L2)}>
                      {c.level}
                    </span>
                    <span className="text-xs text-pine-700">{c.dimension_label}</span>
                    <div className="ml-auto flex items-center gap-2">
                      <span className="text-[10px] text-violet-400">{c.value_a.toFixed(2)}</span>
                      <span className="text-[10px] text-pine-700">→</span>
                      <span className="text-[10px] text-magic-400">{c.value_b.toFixed(2)}</span>
                      <span className="text-[10px] text-pine-700">Δ={c.delta.toFixed(2)}</span>
                    </div>
                  </div>
                ))}
                {report.conflicts.length === 0 && (
                  <p className="py-2 text-center text-xs text-emerald-400">无冲突</p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {conflictReports.length === 0 && selectedProfileId && !loading && (
        <div className="py-8 text-center text-xs text-pine-700">点击"检测冲突"查看结果</div>
      )}

      {styles.length >= 2 && (
        <div>
          <h3 className="mb-2 text-xs font-medium text-pine-700">冲突热力图</h3>
          <div className="overflow-auto">
            <table className="text-xs">
              <thead>
                <tr>
                  <th className="px-2 py-1 text-pine-700" />
                  {styles.map((s) => (
                    <th key={s.id} className="px-2 py-1 text-[10px] font-normal text-pine-700">
                      {s.name.slice(0, 4)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {styles.map((row) => (
                  <tr key={row.id}>
                    <td className="px-2 py-1 text-[10px] text-pine-700">{row.name.slice(0, 4)}</td>
                    {styles.map((col) => {
                      if (row.id === col.id) {
                        return <td key={col.id} className="px-2 py-1 text-center text-[9px] text-pine-700">-</td>;
                      }
                      const emb_a = row.style_embedding || {};
                      const emb_b = col.style_embedding || {};
                      let totalDelta = 0;
                      let count = 0;
                      for (const key of Object.keys(EMBEDDING_DIMENSION_LABELS) as (keyof StyleEmbedding)[]) {
                        totalDelta += Math.abs((emb_a[key] ?? 0) - (emb_b[key] ?? 0));
                        count++;
                      }
                      const avgDelta = count > 0 ? totalDelta / count : 0;
                      const bg =
                        avgDelta > 0.4
                          ? "bg-crimson-500/20 text-crimson-400"
                          : avgDelta > 0.2
                            ? "bg-magic-500/15 text-magic-400"
                            : "bg-emerald-500/15 text-emerald-400";
                      return (
                        <td key={col.id} className={clsx("px-2 py-1 text-center text-[9px]", bg)}>
                          {avgDelta.toFixed(2)}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

function StyleUploadModal({
  onClose,
  onSubmit,
}: {
  onClose: () => void;
  onSubmit: (name: string, file: File) => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFileSelect = (f: File) => {
    const ext = f.name.toLowerCase();
    if (ext.endsWith(".txt") || ext.endsWith(".epub")) {
      setFile(f);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const f = e.dataTransfer.files[0];
    if (f) handleFileSelect(f);
  };

  const handleSubmit = () => {
    if (!file || !name.trim()) return;
    onClose();
    void onSubmit(name.trim(), file);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="w-[480px] rounded-xl border border-pine-200/60 bg-white/50 p-6">
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-pine-700">学习文笔风格</h3>
          <button onClick={onClose} className="text-pine-700 hover:text-pine-700">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-4">
          <div>
            <label className="mb-1 block text-xs text-pine-700">
              风格名称 <span className="text-red-400">*</span>
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例：古龙风格、金庸风格"
              className="w-full rounded-lg border border-pine-200 bg-white/50 px-3 py-2 text-sm text-pine-700 outline-none focus:border-violet-500/50 placeholder:text-pine-700"
            />
          </div>

          <div>
            <label className="mb-1 block text-xs text-pine-700">上传书籍</label>
            <div
              onDragOver={(e) => {
                e.preventDefault();
                setDragOver(true);
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={handleDrop}
              onClick={() => inputRef.current?.click()}
              className={clsx(
                "flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed p-8 transition-colors",
                dragOver
                  ? "border-violet-500 bg-violet-500/5"
                  : file
                    ? "border-emerald-500/40 bg-emerald-500/5"
                    : "border-pine-200 hover:border-pine-200"
              )}
            >
              {file ? (
                <>
                  <BookOpen className="mb-2 h-8 w-8 text-emerald-400" />
                  <p className="text-sm text-pine-700">{file.name}</p>
                  <p className="text-[10px] text-pine-700">{(file.size / 1024).toFixed(1)} KB</p>
                </>
              ) : (
                <>
                  <Upload className="mb-2 h-8 w-8 text-pine-700" />
                  <p className="text-sm text-pine-700">拖拽文件到此处或点击选择</p>
                  <p className="text-[10px] text-pine-700">支持 .txt 和 .epub 格式</p>
                </>
              )}
              <input
                ref={inputRef}
                type="file"
                accept=".txt,.epub"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) handleFileSelect(f);
                }}
              />
            </div>
          </div>

        </div>

        <div className="mt-5 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded-lg px-4 py-2 text-sm text-pine-700 hover:text-pine-700 disabled:opacity-50"
          >
            取消
          </button>
          <button
            onClick={handleSubmit}
            disabled={!name.trim() || !file}
            className="rounded-lg bg-violet-500 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-violet-400 disabled:opacity-50"
          >
            开始学习
          </button>
        </div>
      </div>
    </div>
  );
}

function StyleLearningWorkflowPanel({
  workflow,
  onBack,
}: {
  workflow: StyleLearningWorkflow | null;
  onBack: () => void;
}) {
  const currentIndex = workflow ? Math.max(0, Math.min(workflow.currentStepIndex, STYLE_LEARNING_STEPS.length - 1)) : 0;
  const activeStep = workflow?.status === "error"
    ? null
    : STYLE_LEARNING_STEPS[currentIndex] || STYLE_LEARNING_STEPS[0];

  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button onClick={onBack} className="text-pine-700 hover:text-pine-700">
            <ChevronRight className="h-4 w-4 rotate-180" />
          </button>
          <div>
            <h2 className="text-sm font-semibold text-pine-700">风格学习流程</h2>
            <p className="text-xs text-pine-700">
              {workflow ? `${workflow.name} · ${workflow.fileName}` : "等待开始"}
            </p>
          </div>
        </div>
        <div className={clsx(
          "rounded-full border px-2.5 py-1 text-[10px]",
          workflow?.status === "done"
            ? "border-emerald-500/30 bg-emerald-500/15 text-emerald-400"
            : workflow?.status === "error"
              ? "border-crimson-500/30 bg-crimson-500/15 text-crimson-400"
              : "border-magic-500/30 bg-magic-500/15 text-magic-400"
        )}>
          {workflow?.status === "done" ? "已完成" : workflow?.status === "error" ? "已失败" : "进行中"}
        </div>
      </div>

      <div className="rounded-xl border border-pine-200/60 bg-white/50 p-4">
        <div className="mb-4 flex items-center gap-3">
          <Loader2 className={clsx("h-5 w-5", workflow?.status === "running" && "animate-spin text-violet-400", workflow?.status === "done" && "text-emerald-400", workflow?.status === "error" && "text-crimson-400")} />
          <div>
            <p className="text-sm font-medium text-pine-700">
              {workflow?.message || "等待上传"}
            </p>
            <p className="text-xs text-pine-700">
              {activeStep ? `${activeStep.label} · ${activeStep.desc}` : "流程已结束"}
            </p>
          </div>
        </div>

        <div className="space-y-3">
          {STYLE_LEARNING_STEPS.map((step, index) => {
            const done = workflow?.status === "done" ? true : index < currentIndex;
            const current = workflow?.status === "running" && index === currentIndex;
            const failed = workflow?.status === "error" && index === currentIndex;
            return (
              <div key={step.key} className={clsx("flex items-start gap-3 rounded-lg border p-3", current ? "border-violet-500/30 bg-violet-500/10" : "border-pine-200/40 bg-white/50")}>
                <div className={clsx(
                  "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-[10px] font-medium",
                  done ? "border-emerald-500/30 bg-emerald-500/15 text-emerald-400" :
                  failed ? "border-crimson-500/30 bg-crimson-500/15 text-crimson-400" :
                  current ? "border-violet-500/30 bg-violet-500/15 text-violet-400" :
                  "border-pine-200 text-pine-700"
                )}>
                  {done ? "✓" : failed ? "×" : index + 1}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-medium text-pine-700">{step.label}</span>
                    {current && <span className="text-[10px] text-violet-400">当前阶段</span>}
                  </div>
                  <p className="mt-0.5 text-[10px] text-pine-700">{step.desc}</p>
                </div>
              </div>
            );
          })}
        </div>

        {workflow?.status === "error" && workflow.error && (
          <div className="mt-4 rounded-lg border border-crimson-500/20 bg-crimson-500/5 p-3 text-xs text-crimson-300">
            {workflow.error}
          </div>
        )}

        {workflow?.status === "done" && (
          <div className="mt-4 rounded-lg border border-emerald-500/20 bg-emerald-500/5 p-3 text-xs text-emerald-300">
            风格画像已生成完成，可以回到列表查看新画像。
          </div>
        )}
      </div>
    </div>
  );
}

const loadingPhrases = [
  "正在唤醒文笔工坊...",
  "检索名家风格记忆库...",
  "校准叙事基调与节奏...",
  "提取遣词造句特征模型...",
  "构建文字神经元网络...",
  "即将完成，请稍候..."
];

function StyleLoadingScreen() {
  const [phraseIndex, setPhraseIndex] = useState(0);

  useEffect(() => {
    const timer = setInterval(() => {
      setPhraseIndex((p) => (p + 1) % loadingPhrases.length);
    }, 1800);
    return () => clearInterval(timer);
  }, []);

  return (
    <div className="flex h-full flex-col items-center justify-center bg-transparent">
      {/* 核心羽毛动画 */}
      <motion.div
        animate={{ y: [0, -12, 0] }}
        transition={{ duration: 2.5, repeat: Infinity, ease: "easeInOut" }}
        className="relative mb-8 flex h-20 w-20 items-center justify-center rounded-2xl border border-violet-500/20 bg-violet-500/10 shadow-[0_0_40px_rgba(139,92,246,0.15)]"
      >
        <motion.div
          animate={{ rotate: [-5, 5, -5] }}
          transition={{ duration: 4, repeat: Infinity, ease: "easeInOut" }}
        >
          <Feather className="h-10 w-10 text-violet-400" strokeWidth={1.5} />
        </motion.div>

        {/* 外圈光晕动画 */}
        <motion.div
          animate={{ scale: [1, 1.2, 1], opacity: [0.5, 0, 0.5] }}
          transition={{ duration: 2.5, repeat: Infinity, ease: "easeOut" }}
          className="absolute inset-0 rounded-2xl border border-violet-400/30"
        />
      </motion.div>
      
      <div className="flex flex-col items-center gap-5">
        {/* 滚动的文字提示 */}
        <div className="h-6 overflow-hidden">
          <AnimatePresence mode="wait">
            <motion.p
              key={phraseIndex}
              initial={{ opacity: 0, y: 15 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -15 }}
              transition={{ duration: 0.4, ease: "easeOut" }}
              className="text-sm font-medium tracking-widest text-pine-700/80"
            >
              {loadingPhrases[phraseIndex]}
            </motion.p>
          </AnimatePresence>
        </div>
        
        {/* 赛博朋克风进度条 */}
        <div className="relative h-1 w-40 overflow-hidden rounded-full bg-pine-200/50">
          <motion.div
            className="absolute inset-y-0 left-0 w-1/3 rounded-full bg-gradient-to-r from-violet-500/0 via-violet-400 to-violet-500/0"
            animate={{ x: ["-100%", "300%"] }}
            transition={{ duration: 1.5, repeat: Infinity, ease: "easeInOut" }}
          />
        </div>
      </div>
    </div>
  );
}
