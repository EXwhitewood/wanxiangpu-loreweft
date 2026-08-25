/* Hallmark · pre-emit critique: P5 H5 E5 S5 R5 V5 · macrostructure: Workbench · design-system: DESIGN.md · tone: calm utilitarian · anchor hue: pine */
import {
  forwardRef,
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
  type TextareaHTMLAttributes,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  AlertTriangle,
  ArrowDown,
  ArrowRight,
  ArrowUp,
  BookOpenCheck,
  CheckCircle2,
  ChevronDown,
  CircleDot,
  GitBranch,
  Link2,
  Loader2,
  Lock,
  Plus,
  Save,
  Sparkles,
  Target,
  Trash2,
} from "lucide-react";

import * as api from "@/api/client";
import type {
  ChapterBlueprint,
  ChapterBlueprintDiagnostic,
  ChapterSpineItem,
  SceneBriefItem,
} from "@/types";


export interface ChapterBlueprintStatus {
  loading: boolean;
  saving: boolean;
  dirty: boolean;
  persisted: boolean;
  frozen: boolean;
  revision: number | null;
  blockingCount: number;
  error: string | null;
}


export interface ChapterBlueprintPanelHandle {
  saveIfNeeded: () => Promise<ChapterBlueprint>;
  reload: () => Promise<void>;
}


interface ChapterBlueprintPanelProps {
  projectId: string;
  chapterNumber: number;
  onStatusChange?: (status: ChapterBlueprintStatus) => void;
  contextContent?: ReactNode;
  reminderContent?: ReactNode;
  reminderCount?: number;
}


type StateEffect = NonNullable<ChapterSpineItem["state_effects_expected"]>[number];


const SCENE_TYPES: Array<{ value: SceneBriefItem["type"] | "turning_point"; label: string }> = [
  { value: "development", label: "推进" },
  { value: "dialogue", label: "对话" },
  { value: "action", label: "行动" },
  { value: "discovery", label: "发现" },
  { value: "reflection", label: "反思" },
  { value: "transition", label: "过渡" },
  { value: "turning_point", label: "转折" },
];

const CHAPTER_FUNCTIONS: Array<{ value: ChapterSpineItem["function"]; label: string }> = [
  { value: "setup", label: "建立" },
  { value: "escalation", label: "升级" },
  { value: "reversal", label: "反转" },
  { value: "payoff", label: "兑现" },
  { value: "recovery", label: "余波" },
  { value: "transition", label: "过渡" },
];

const BEAT_ROLES: Array<{ value: NonNullable<SceneBriefItem["beat_role"]>; label: string }> = [
  { value: "setup", label: "铺垫" },
  { value: "pressure", label: "施压" },
  { value: "decision", label: "决策" },
  { value: "turning_point", label: "转折" },
  { value: "hook", label: "钩子" },
];

const inputClass = "chapter-blueprint-field chapter-blueprint-field--spaced";
const compactInputClass = "chapter-blueprint-field chapter-blueprint-field--compact";
const textAreaClass = "chapter-blueprint-field chapter-blueprint-field--textarea blueprint-field-scrollbar";
const sectionLabelClass = "chapter-blueprint-group-title";


interface ScrollMetrics {
  scrollable: boolean;
  thumbHeight: number;
  thumbOffset: number;
}


type BlueprintTextareaProps = TextareaHTMLAttributes<HTMLTextAreaElement>;


function BlueprintTextarea({
  className,
  disabled,
  onScroll,
  rows,
  value,
  ...textareaProps
}: BlueprintTextareaProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const railRef = useRef<HTMLSpanElement>(null);
  const thumbRef = useRef<HTMLSpanElement>(null);
  const dragRef = useRef<{ pointerId: number; grabOffset: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  const [metrics, setMetrics] = useState<ScrollMetrics>({
    scrollable: false,
    thumbHeight: 0,
    thumbOffset: 0,
  });

  const updateScrollMetrics = useCallback(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;

    const railHeight = railRef.current?.clientHeight || Math.max(textarea.clientHeight - 18, 0);
    const maximumScroll = Math.max(textarea.scrollHeight - textarea.clientHeight, 0);
    const scrollable = maximumScroll > 1 && railHeight > 0;
    const thumbHeight = scrollable
      ? Math.min(railHeight, 32, Math.max(18, railHeight * (textarea.clientHeight / textarea.scrollHeight)))
      : railHeight;
    const thumbTravel = Math.max(railHeight - thumbHeight, 0);
    const thumbOffset = maximumScroll > 0
      ? (textarea.scrollTop / maximumScroll) * thumbTravel
      : 0;

    setMetrics((current) => {
      if (
        current.scrollable === scrollable
        && Math.abs(current.thumbHeight - thumbHeight) < 0.5
        && Math.abs(current.thumbOffset - thumbOffset) < 0.5
      ) {
        return current;
      }
      return { scrollable, thumbHeight, thumbOffset };
    });
  }, []);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return undefined;

    updateScrollMetrics();
    if (typeof ResizeObserver === "undefined") return undefined;

    const observer = new ResizeObserver(updateScrollMetrics);
    observer.observe(textarea);
    return () => observer.disconnect();
  }, [updateScrollMetrics]);

  useEffect(() => {
    updateScrollMetrics();
  }, [rows, updateScrollMetrics, value]);

  const scrollFromPointer = useCallback((clientY: number, grabOffset: number) => {
    const textarea = textareaRef.current;
    const rail = railRef.current;
    if (!textarea || !rail || !metrics.scrollable) return;

    const railRect = rail.getBoundingClientRect();
    const thumbTravel = Math.max(railRect.height - metrics.thumbHeight, 0);
    const maximumScroll = Math.max(textarea.scrollHeight - textarea.clientHeight, 0);
    if (thumbTravel <= 0 || maximumScroll <= 0) return;

    const nextThumbOffset = Math.min(
      thumbTravel,
      Math.max(0, clientY - railRect.top - grabOffset),
    );
    textarea.scrollTop = (nextThumbOffset / thumbTravel) * maximumScroll;
    updateScrollMetrics();
  }, [metrics.scrollable, metrics.thumbHeight, updateScrollMetrics]);

  const handleRailPointerDown = (event: ReactPointerEvent<HTMLSpanElement>) => {
    if (disabled || !metrics.scrollable) return;
    event.preventDefault();

    const thumbRect = thumbRef.current?.getBoundingClientRect();
    const grabbedThumb = event.target === thumbRef.current && thumbRect;
    const grabOffset = grabbedThumb
      ? event.clientY - thumbRect.top
      : metrics.thumbHeight / 2;

    dragRef.current = { pointerId: event.pointerId, grabOffset };
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragging(true);
    scrollFromPointer(event.clientY, grabOffset);
  };

  const handleRailPointerMove = (event: ReactPointerEvent<HTMLSpanElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    event.preventDefault();
    scrollFromPointer(event.clientY, drag.grabOffset);
  };

  const finishRailDrag = (event: ReactPointerEvent<HTMLSpanElement>) => {
    if (dragRef.current?.pointerId !== event.pointerId) return;
    dragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    setDragging(false);
  };

  return (
    <span className={`blueprint-textarea-shell${metrics.scrollable ? " is-scrollable" : ""}${dragging ? " is-dragging" : ""}${disabled ? " is-disabled" : ""}`}>
      <textarea
        {...textareaProps}
        ref={textareaRef}
        className={className}
        disabled={disabled}
        rows={rows}
        value={value}
        onScroll={(event) => {
          updateScrollMetrics();
          onScroll?.(event);
        }}
      />
      <span
        ref={railRef}
        aria-hidden="true"
        className="blueprint-scroll-rail"
        onPointerDown={handleRailPointerDown}
        onPointerMove={handleRailPointerMove}
        onPointerUp={finishRailDrag}
        onPointerCancel={finishRailDrag}
        onWheel={(event) => {
          const textarea = textareaRef.current;
          if (!textarea || !metrics.scrollable) return;
          event.preventDefault();
          textarea.scrollTop += event.deltaY;
          updateScrollMetrics();
        }}
      >
        <span
          ref={thumbRef}
          className="blueprint-scroll-thumb"
          style={{
            height: metrics.thumbHeight,
            transform: `translateY(${metrics.thumbOffset}px)`,
          } as CSSProperties}
        />
      </span>
    </span>
  );
}


function localDiagnostics(scenes: SceneBriefItem[]): ChapterBlueprintDiagnostic[] {
  const diagnostics: ChapterBlueprintDiagnostic[] = [];
  scenes.forEach((scene, index) => {
    if (!scene.goal?.trim()) {
      diagnostics.push({
        code: "scene_goal_missing",
        severity: "blocking",
        field_path: `scenes[${index}].goal`,
        message: `场景 ${index + 1} 缺少目标`,
        blocks_generation: true,
      });
    }
    if (!scene.conflict?.trim()) {
      diagnostics.push({
        code: "scene_conflict_missing",
        severity: "blocking",
        field_path: `scenes[${index}].conflict`,
        message: `场景 ${index + 1} 缺少冲突`,
        blocks_generation: true,
      });
    }
  });
  return diagnostics;
}


function blankScene(chapterNumber: number, index: number): SceneBriefItem {
  return {
    scene_id: `ch_${String(chapterNumber).padStart(3, "0")}_s${index + 1}`,
    type: "development",
    goal: "",
    conflict: "",
    outcome: "",
    info_release: "",
    hook: "",
    required_context_refs: [],
  };
}


const ChapterBlueprintPanel = forwardRef<
  ChapterBlueprintPanelHandle,
  ChapterBlueprintPanelProps
>(function ChapterBlueprintPanel({
  projectId,
  chapterNumber,
  onStatusChange,
  contextContent,
  reminderContent,
  reminderCount = 0,
}, ref) {
  const [blueprint, setBlueprint] = useState<ChapterBlueprint | null>(null);
  const [chapter, setChapter] = useState<Partial<ChapterSpineItem>>({});
  const [scenes, setScenes] = useState<SceneBriefItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    setError(null);
    try {
      const loaded = await api.getChapterBlueprint(projectId, chapterNumber);
      setBlueprint(loaded);
      setChapter(loaded.chapter || {});
      setScenes(loaded.scenes || []);
      setDirty(false);
    } catch (cause) {
      setBlueprint(null);
      setChapter({});
      setScenes([]);
      setError((cause as Error).message || "本章蓝图加载失败");
    } finally {
      setLoading(false);
    }
  }, [projectId, chapterNumber]);

  useEffect(() => {
    void load();
  }, [load]);

  const diagnostics = useMemo(() => {
    if (dirty) return localDiagnostics(scenes);
    return blueprint?.diagnostics || localDiagnostics(scenes);
  }, [blueprint?.diagnostics, dirty, scenes]);
  const blockingCount = diagnostics.filter((item) => item.blocks_generation).length;
  const recoveryPending = chapter.recovery_generated === true || chapter.status === "needs_review";
  const recoveryDetailBlocking = diagnostics.some(
    (item) => item.blocks_generation && item.code !== "recovery_confirmation_required",
  );

  const saveIfNeeded = useCallback(async (confirmRecovery = false) => {
    if (!blueprint) throw new Error(error || "本章蓝图尚未加载");
    if (!confirmRecovery && !dirty && blueprint.persisted) return blueprint;
    if (blueprint.frozen) throw new Error("本章蓝图已冻结，需要通过修订案修改");

    setSaving(true);
    setError(null);
    try {
      const saved = await api.saveChapterBlueprint(projectId, chapterNumber, {
        expected_revision: blueprint.revision,
        chapter: chapter as Record<string, unknown>,
        scenes: scenes as unknown as Array<Record<string, unknown>>,
        confirm_recovery: confirmRecovery,
      });
      setBlueprint(saved);
      setChapter(saved.chapter || {});
      setScenes(saved.scenes || []);
      setDirty(false);
      return saved;
    } catch (cause) {
      const message = (cause as Error).message || "本章蓝图保存失败";
      setError(message);
      throw cause;
    } finally {
      setSaving(false);
    }
  }, [blueprint, chapter, chapterNumber, dirty, error, projectId, scenes]);

  useImperativeHandle(ref, () => ({
    saveIfNeeded,
    reload: load,
  }), [load, saveIfNeeded]);

  useEffect(() => {
    onStatusChange?.({
      loading,
      saving,
      dirty,
      persisted: Boolean(blueprint?.persisted),
      frozen: Boolean(blueprint?.frozen),
      revision: blueprint?.revision ?? null,
      blockingCount,
      error,
    });
  }, [blockingCount, blueprint, dirty, error, loading, onStatusChange, saving]);

  const updateChapter = (field: keyof ChapterSpineItem, value: unknown) => {
    setChapter((current) => ({ ...current, [field]: value }));
    setDirty(true);
  };

  const updateScene = (index: number, field: keyof SceneBriefItem, value: unknown) => {
    setScenes((current) => current.map((scene, sceneIndex) => (
      sceneIndex === index ? { ...scene, [field]: value } : scene
    )));
    setDirty(true);
  };

  const moveScene = (index: number, direction: -1 | 1) => {
    const target = index + direction;
    if (target < 0 || target >= scenes.length) return;
    setScenes((current) => {
      const next = [...current];
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
    setDirty(true);
  };

  const stateEffects = chapter.state_effects_expected || [];
  const updateStateEffect = (index: number, field: keyof StateEffect, value: string) => {
    const next = stateEffects.map((effect, effectIndex) => (
      effectIndex === index ? { ...effect, [field]: value } : effect
    ));
    updateChapter("state_effects_expected", next);
  };

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center gap-2 text-sm text-pine-600">
        <Loader2 className="h-4 w-4 animate-spin" />
        正在读取本章蓝图……
      </div>
    );
  }

  if (!blueprint) {
    return (
      <div className="flex h-full flex-col items-center justify-center px-8 text-center">
        <AlertTriangle className="mb-3 h-8 w-8 text-amber-600" />
        <p className="text-sm text-pine-800">{error || "本章没有可用蓝图"}</p>
        <button type="button" onClick={() => void load()} className="mt-3 text-xs text-magic-700 hover:underline">
          重新加载
        </button>
      </div>
    );
  }

  const coreConflict = chapter.core_conflict || {
    desire: "", obstacle: "", action: "", turn: "",
  };
  const valueShift = typeof chapter.value_shift === "object" && chapter.value_shift
    ? chapter.value_shift
    : { axis: "", from: "", to: "" };

  const chapterFunction = CHAPTER_FUNCTIONS.find((item) => item.value === chapter.function)?.label || "未设定";
  const recommendedSceneCount = blueprint.scene_brief.recommended_scene_count;
  const minimumSceneCount = blueprint.scene_brief.minimum_scene_count;
  const progressLabel = recommendedSceneCount
    ? `${scenes.length}/${recommendedSceneCount} 个场景`
    : `${scenes.length} 个场景`;
  const updateListField = (field: "depends_on" | "must_not", value: string) => {
    updateChapter(
      field,
      value.split(/[、,，\s]+/).map((item) => item.trim()).filter(Boolean),
    );
  };

  return (
    <div data-testid="chapter-blueprint-panel" className="chapter-blueprint-workbench">
      <div className="chapter-blueprint-dossier">
        <header className="chapter-blueprint-header">
          <div className="chapter-blueprint-header-row">
            <div className="chapter-blueprint-title">
              <BookOpenCheck className="h-4 w-4" />
              <strong>本章蓝图</strong>
              <span>第 {chapterNumber} 章</span>
            </div>
            <div className="chapter-blueprint-actions">
              <span className={`chapter-blueprint-status ${
                blueprint.frozen
                  ? "is-frozen"
                  : dirty
                    ? "is-dirty"
                    : "is-synced"
              }`}>
                {blueprint.frozen ? <Lock className="h-3 w-3" /> : dirty ? <CircleDot className="h-3 w-3" /> : <CheckCircle2 className="h-3 w-3" />}
                {blueprint.frozen ? "已冻结" : dirty ? "待保存" : "已同步"}
              </span>
              {!blueprint.frozen && (
                <>
                  {recoveryPending && (
                    <button
                      type="button"
                      onClick={() => void saveIfNeeded(true)}
                      disabled={saving || recoveryDetailBlocking}
                      className="chapter-blueprint-save"
                      title={recoveryDetailBlocking ? "请先细化所有恢复占位内容" : "确认该恢复章已细化，可以参与正文生成"}
                    >
                      <CheckCircle2 className="h-3 w-3" />确认可生成
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => void saveIfNeeded()}
                    disabled={saving || (!dirty && blueprint.persisted)}
                    className="chapter-blueprint-save"
                  >
                    {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
                    {saving ? "保存中" : "保存"}
                  </button>
                </>
              )}
            </div>
          </div>
          <div className="chapter-blueprint-meta">
            <div className="chapter-blueprint-meta-item">
              <span>章节作用</span>
              <strong>{chapterFunction}</strong>
            </div>
            <div className="chapter-blueprint-meta-item">
              <span>POV</span>
              <strong>{chapter.pov_character || "未设定"}</strong>
            </div>
            <div className="chapter-blueprint-meta-item">
              <span>蓝图版本</span>
              <strong>v{blueprint.revision}</strong>
            </div>
          </div>
        </header>

        {blueprint.frozen && (
          <div className="chapter-blueprint-alert is-frozen flex items-start gap-2 border border-amber-200 bg-amber-50/80 p-3 text-xs text-amber-800">
            <Lock className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>本章蓝图所在的大纲层已冻结。可以据此生成，但修改需通过修订案。</span>
          </div>
        )}

      {error && (
        <div className="chapter-blueprint-alert is-error border border-red-400 bg-red-100 p-3 text-sm font-medium text-red-900">
          {error}
        </div>
      )}

      <section className="chapter-blueprint-section chapter-blueprint-section--primary">
        <div className="chapter-blueprint-section-head">
          <div>
            <h3>本章任务</h3>
            <p>标题、视角、冲突和章末落点</p>
          </div>
          <span className="chapter-blueprint-read-marker">生成工作流读取</span>
        </div>

        <div className="chapter-blueprint-form">
          <div className="chapter-blueprint-field-grid chapter-blueprint-field-grid--two">
            <label>
              标题
            <input
              value={chapter.title || ""}
              onChange={(event) => updateChapter("title", event.target.value)}
              disabled={blueprint.frozen}
              className={inputClass}
            />
            </label>
            <label>
              POV 角色
            <input
              value={chapter.pov_character || ""}
              onChange={(event) => updateChapter("pov_character", event.target.value)}
              disabled={blueprint.frozen}
              className={inputClass}
            />
            </label>
          </div>

          <div className="chapter-blueprint-field-grid chapter-blueprint-field-grid--two">
            <label>
              章节作用
              <select
                value={chapter.function || ""}
                onChange={(event) => updateChapter("function", event.target.value)}
                disabled={blueprint.frozen}
                className={inputClass}
              >
                <option value="">未设定</option>
                {CHAPTER_FUNCTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>
            </label>
            <label>
              外部事件
              <input
                value={chapter.external_event || ""}
                onChange={(event) => updateChapter("external_event", event.target.value)}
                disabled={blueprint.frozen}
                placeholder="这一章外部发生了什么"
                className={inputClass}
              />
            </label>
          </div>

          <div className="chapter-blueprint-field-group">
            <div className="chapter-blueprint-group-head">
              <p className={sectionLabelClass}>核心冲突</p>
              <span>欲望 → 阻力 → 行动 → 转折</span>
            </div>
            <div className="chapter-blueprint-field-grid chapter-blueprint-field-grid--two">
              {([
                ["desire", "想要什么"],
                ["obstacle", "受到什么阻碍"],
                ["action", "采取什么行动"],
                ["turn", "发生什么转折"],
              ] as const).map(([field, label]) => (
                <BlueprintTextarea
                  key={field}
                  value={coreConflict[field] || ""}
                  onChange={(event) => {
                    const next = { ...coreConflict, [field]: event.target.value };
                    setChapter((current) => ({
                      ...current,
                      core_conflict: next,
                      conflict_text: [next.desire, next.obstacle, next.action, next.turn].filter(Boolean).join("，但"),
                    }));
                    setDirty(true);
                  }}
                  disabled={blueprint.frozen}
                  placeholder={label}
                  rows={2}
                  className={textAreaClass}
                />
              ))}
            </div>
          </div>

          <div className="chapter-blueprint-field-group">
            <div className="chapter-blueprint-group-head">
              <p className={sectionLabelClass}>价值转变</p>
              <ArrowRight className="h-3.5 w-3.5" />
            </div>
            <div className="chapter-blueprint-field-grid chapter-blueprint-field-grid--three">
              {([
                ["axis", "维度"], ["from", "从"], ["to", "到"],
              ] as const).map(([field, label]) => (
                <input
                  key={field}
                  value={valueShift[field] || ""}
                  onChange={(event) => updateChapter("value_shift", {
                    ...valueShift,
                    [field]: event.target.value,
                  })}
                  disabled={blueprint.frozen}
                  placeholder={label}
                  className={compactInputClass}
                />
              ))}
            </div>
          </div>

          <label>
            章末钩子
            <BlueprintTextarea
              value={chapter.hook || ""}
              onChange={(event) => updateChapter("hook", event.target.value)}
              disabled={blueprint.frozen}
              rows={2}
              className={textAreaClass}
            />
          </label>
        </div>
      </section>

      <section className="chapter-blueprint-section">
        <div className="chapter-blueprint-section-head">
          <div>
            <h3>依赖与禁区</h3>
            <p>生成时必须继承与不得推翻的事实</p>
          </div>
          <GitBranch className="h-4 w-4" />
        </div>
        <div className="chapter-blueprint-field-grid chapter-blueprint-field-grid--two">
          <label>
            前置依赖
            <input
              value={(chapter.depends_on || []).join("、")}
              onChange={(event) => updateListField("depends_on", event.target.value)}
              disabled={blueprint.frozen}
              placeholder="人物、伏笔或规则 ID"
              className={inputClass}
            />
          </label>
          <label>
            本章不可发生
            <input
              value={(chapter.must_not || []).join("、")}
              onChange={(event) => updateListField("must_not", event.target.value)}
              disabled={blueprint.frozen}
              placeholder="不能推翻的事实"
              className={inputClass}
            />
          </label>
        </div>
        {chapter.thread_ops && chapter.thread_ops.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-1.5 border-t border-[var(--border-emphasis)] pt-3">
            {chapter.thread_ops.map((thread) => (
              <span key={`${thread.thread_id}-${thread.op}`} className="inline-flex items-center gap-1 rounded-full bg-pine-100 px-2 py-1 text-xs font-medium text-pine-800">
                <Sparkles className="h-3 w-3" />{thread.thread_id} · {thread.op}
              </span>
            ))}
          </div>
        )}
      </section>

      <section className="chapter-blueprint-section chapter-blueprint-section--scenes">
        <div className="chapter-blueprint-section-head">
          <div>
            <h3>场景序列</h3>
            <p>主编会沿着这条路径推进正文</p>
          </div>
          <div className="chapter-blueprint-section-actions">
            <span>{progressLabel}</span>
          {!blueprint.frozen && (
            <button
              type="button"
              onClick={() => {
                setScenes((current) => [...current, blankScene(chapterNumber, current.length)]);
                setDirty(true);
              }}
              className="chapter-blueprint-secondary-action"
            >
              <Plus className="h-3 w-3" />
              添加场景
            </button>
          )}
          </div>
        </div>

        <div className="chapter-scene-list">
          <div className="chapter-scene-rail" aria-hidden="true" />
          {scenes.map((scene, index) => {
            const sceneEffects = stateEffects.filter((effect) => effect.scene_id === scene.scene_id);
            return (
              <details key={`${scene.scene_id}-${index}`} className="chapter-scene-card group relative" open={index === 0}>
                <summary className="flex cursor-pointer list-none items-center gap-2.5 px-3 py-3.5 marker:hidden">
                  <span className="chapter-scene-index">
                    {index + 1}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <span className="chapter-scene-kind">
                        {SCENE_TYPES.find((item) => item.value === scene.type)?.label || "场景"}
                      </span>
                      {scene.beat_role && <span className="text-xs text-pine-700">{BEAT_ROLES.find((item) => item.value === scene.beat_role)?.label}</span>}
                    </div>
                    <p className="mt-1 truncate text-sm font-semibold text-[var(--color-ink-strong)]">{scene.goal || "未填写场景目标"}</p>
                    <p className="mt-0.5 truncate text-xs text-pine-700">{scene.conflict || "缺少冲突"}</p>
                  </div>
                  {sceneEffects.length > 0 && (
                    <span className="chapter-scene-effect-count hidden rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-900 sm:inline-flex">
                      {sceneEffects.length} 项状态变化
                    </span>
                  )}
                  <ChevronDown className="h-4 w-4 shrink-0 text-pine-700 transition-transform group-open:rotate-180" />
                </summary>

                <div className="space-y-3 border-t border-[var(--border-emphasis)] px-3 py-3.5">
                  <div className="chapter-scene-controls flex items-center gap-2">
                    <select
                      value={scene.type}
                      onChange={(event) => updateScene(index, "type", event.target.value)}
                      disabled={blueprint.frozen}
                      className={compactInputClass}
                    >
                      {SCENE_TYPES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                    </select>
                    <select
                      value={scene.beat_role || ""}
                      onChange={(event) => updateScene(index, "beat_role", event.target.value || undefined)}
                      disabled={blueprint.frozen}
                      className={compactInputClass}
                      aria-label="场景节拍"
                    >
                      <option value="">节拍</option>
                      {BEAT_ROLES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                    </select>
                    <span className="min-w-0 flex-1 truncate font-mono text-xs text-pine-700">{scene.scene_id}</span>
                    {!blueprint.frozen && (
                      <>
                        <button type="button" onClick={() => moveScene(index, -1)} disabled={index === 0} className="rounded-md p-1 text-pine-700 outline outline-2 outline-offset-1 outline-transparent transition-colors hover:bg-pine-100 focus-visible:outline-[var(--focus-ring)] disabled:opacity-25" aria-label="上移场景"><ArrowUp className="h-3.5 w-3.5" /></button>
                        <button type="button" onClick={() => moveScene(index, 1)} disabled={index === scenes.length - 1} className="rounded-md p-1 text-pine-700 outline outline-2 outline-offset-1 outline-transparent transition-colors hover:bg-pine-100 focus-visible:outline-[var(--focus-ring)] disabled:opacity-25" aria-label="下移场景"><ArrowDown className="h-3.5 w-3.5" /></button>
                        <button type="button" onClick={() => {
                          setScenes((current) => current.filter((_, sceneIndex) => sceneIndex !== index));
                          setDirty(true);
                        }} disabled={scenes.length <= 1} className="rounded-md p-1 text-red-500 transition-colors hover:bg-red-50 disabled:opacity-25" aria-label="删除场景"><Trash2 className="h-3.5 w-3.5" /></button>
                      </>
                    )}
                  </div>
                  <div className="grid gap-2 sm:grid-cols-2">
                  {([
                    ["goal", "目标"],
                    ["conflict", "冲突"],
                    ["outcome", "结果"],
                    ["info_release", "信息释放"],
                    ["hook", "场景钩子"],
                  ] as const).map(([field, label]) => (
                    <label key={field} className="block min-w-0 text-xs font-medium text-pine-700">
                      {label}
                      <BlueprintTextarea
                        value={scene[field] || ""}
                        onChange={(event) => updateScene(index, field, event.target.value)}
                        disabled={blueprint.frozen}
                        rows={field === "goal" || field === "conflict" ? 2 : 1}
                        className={textAreaClass}
                      />
                    </label>
                  ))}
                  </div>
                  <label className="block text-xs font-medium text-pine-700">
                    关联上下文 ID
                    <input
                      value={(scene.required_context_refs || []).join("、")}
                      onChange={(event) => updateScene(
                        index,
                        "required_context_refs",
                        event.target.value.split(/[、,，\s]+/).map((item) => item.trim()).filter(Boolean),
                      )}
                      disabled={blueprint.frozen}
                      placeholder="人物、规则或伏笔 ID，用顿号分隔"
                      className={compactInputClass + " mt-1 w-full"}
                    />
                  </label>
                </div>
              </details>
            );
          })}
        </div>
      </section>

      <section className="chapter-blueprint-section">
        <div className="chapter-blueprint-section-head">
          <div>
            <h3>关键状态变化</h3>
            <p>人物生死、位置、持有物等硬变化只在这里定义一次</p>
          </div>
          {!blueprint.frozen && (
            <button
              type="button"
              onClick={() => updateChapter("state_effects_expected", [
                ...stateEffects,
                { entity: "", field: "", before: "", after: "", scene_id: scenes[0]?.scene_id || "" },
              ])}
              className="chapter-blueprint-secondary-action"
              aria-label="添加状态变化"
            >
              <Plus className="h-3.5 w-3.5" />
              添加
            </button>
          )}
        </div>
        {stateEffects.length === 0 ? (
          <div className="flex items-center gap-2 rounded-xl border border-dashed border-[var(--border-emphasis)] bg-[var(--surface-paper)] px-3 py-3 text-xs text-pine-700">
            <Target className="h-3.5 w-3.5 text-pine-700" />
            本章暂无结构化状态变化
          </div>
        ) : (
          <div className="space-y-2">
            {stateEffects.map((effect, index) => (
              <div key={index} className="rounded-xl border border-[var(--border-emphasis)] bg-[var(--surface-paper)] p-3">
                <div className="grid grid-cols-2 gap-2">
                  <input value={effect.entity_id || effect.entity || ""} onChange={(event) => updateStateEffect(index, effect.entity_id !== undefined ? "entity_id" : "entity", event.target.value)} disabled={blueprint.frozen} placeholder="角色 / 实体" className={compactInputClass} />
                  <input value={effect.field || ""} onChange={(event) => updateStateEffect(index, "field", event.target.value)} disabled={blueprint.frozen} placeholder="字段，如 life_status" className={compactInputClass} />
                  <input value={effect.before || ""} onChange={(event) => updateStateEffect(index, "before", event.target.value)} disabled={blueprint.frozen} placeholder="变化前" className={compactInputClass} />
                  <input value={effect.after || effect.delta || ""} onChange={(event) => updateStateEffect(index, effect.after !== undefined ? "after" : "delta", event.target.value)} disabled={blueprint.frozen} placeholder="变化后" className={compactInputClass} />
                </div>
                <div className="mt-2 flex items-center gap-2">
                  <select value={effect.scene_id || ""} onChange={(event) => updateStateEffect(index, "scene_id", event.target.value)} disabled={blueprint.frozen} className={compactInputClass + " min-w-0 flex-1"}>
                    <option value="">未指定发生场景</option>
                    {scenes.map((scene, sceneIndex) => <option key={scene.scene_id} value={scene.scene_id}>场景 {sceneIndex + 1}</option>)}
                  </select>
                  {!blueprint.frozen && (
                    <button type="button" onClick={() => updateChapter("state_effects_expected", stateEffects.filter((_, effectIndex) => effectIndex !== index))} className="rounded-md p-1.5 text-red-500 transition-colors hover:bg-red-50" aria-label="删除状态变化"><Trash2 className="h-3.5 w-3.5" /></button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {diagnostics.length > 0 && (
        <section className="rounded-2xl border border-amber-200/80 bg-amber-50/70 p-3.5">
          <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-amber-900">
            <AlertTriangle className="h-3.5 w-3.5" />
            生成前需要处理
            <span className="ml-auto rounded-full bg-amber-200 px-2 py-0.5 text-xs">{blockingCount} 项阻断</span>
          </div>
          <ul className="space-y-1.5 text-sm leading-6 text-amber-900">
            {diagnostics.map((item, index) => <li key={`${item.code}-${index}`}>· {item.message}</li>)}
          </ul>
        </section>
      )}

      {diagnostics.length === 0 && (
        <div className="flex items-center gap-2 rounded-xl border border-emerald-400 bg-emerald-100 px-3 py-2.5 text-xs font-semibold text-emerald-900">
          <CheckCircle2 className="h-3.5 w-3.5" />
          生成前检查通过，当前蓝图可以交给工作流
        </div>
      )}

      {contextContent && (
        <details className="chapter-blueprint-disclosure">
          <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-3 text-xs font-semibold text-pine-900">
            <Link2 className="h-3.5 w-3.5" />
            关联上下文
            <span className="ml-auto text-xs font-normal text-pine-700">人物 · 规则 · 伏笔</span>
            <ChevronDown className="h-3.5 w-3.5 transition-transform" />
          </summary>
          <div className="chapter-blueprint-scroll-region max-h-[420px] overflow-y-auto border-t border-[var(--border-emphasis)]">{contextContent}</div>
        </details>
      )}

      {reminderContent && (
        <details className="chapter-blueprint-disclosure">
          <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-3 text-xs font-semibold text-pine-900">
            {reminderCount > 0 ? <AlertTriangle className="h-3.5 w-3.5 text-amber-600" /> : <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />}
            一致性提醒
            {reminderCount > 0 && <span className="rounded-full bg-amber-200 px-1.5 py-0.5 text-xs text-amber-900">{reminderCount}</span>}
            <span className="ml-auto text-xs font-normal text-pine-700">非阻断建议</span>
            <ChevronDown className="h-3.5 w-3.5 transition-transform" />
          </summary>
          <div className="chapter-blueprint-scroll-region max-h-[520px] overflow-y-auto border-t border-[var(--border-emphasis)]">{reminderContent}</div>
        </details>
      )}

      <div className="chapter-blueprint-footer">
        {dirty || !blueprint.persisted
          ? <span className="inline-flex items-center gap-1.5 text-amber-700"><AlertTriangle className="h-3 w-3" />有尚未保存的内容</span>
          : <span className="inline-flex items-center gap-1.5"><CheckCircle2 className="h-3 w-3 text-emerald-600" />蓝图 v{blueprint.plan_version} · 场景 v{blueprint.scene_brief_version}</span>}
        {minimumSceneCount && <span>至少 {minimumSceneCount} 个场景</span>}
      </div>
    </div>
    </div>
  );
});


export default ChapterBlueprintPanel;
