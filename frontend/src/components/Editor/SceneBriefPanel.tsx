import { useState, useEffect, useCallback, useMemo } from "react";
import {
  ChevronLeft,
  ChevronRight,
  Sparkles,
  Trash2,
  Save,
  Pencil,
  X,
  CheckCircle2,
  Loader2,
} from "lucide-react";
import * as api from "@/api/client";
import type { SceneBrief, SceneBriefItem, ChapterSpineItem } from "@/types";
import SceneBriefCard from "@/components/Editor/SceneBriefCard";

interface SceneBriefPanelProps {
  projectId: string;
  spineData: Array<Partial<ChapterSpineItem>>;
}

function isExpired(expiresWhen: string): boolean {
  if (!expiresWhen) return false;
  return new Date(expiresWhen) < new Date();
}

export default function SceneBriefPanel({
  projectId,
  spineData,
}: SceneBriefPanelProps) {
  const [currentIndex, setCurrentIndex] = useState(0);
  const [briefData, setBriefData] = useState<SceneBrief | null>(null);
  const [editedScenes, setEditedScenes] = useState<SceneBriefItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [discarding, setDiscarding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isEditing, setIsEditing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const currentChapter = spineData[currentIndex];
  const chapterNumber = currentChapter?.chapter_number ?? 1;
  const chapterTitle = currentChapter?.title ?? "";
  const hasChanges = useMemo(
    () => JSON.stringify(editedScenes) !== JSON.stringify(briefData?.scenes ?? []),
    [briefData, editedScenes]
  );

  const loadBrief = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    setError(null);
    try {
      const data = await api.getSceneBrief(projectId, chapterNumber);
      setBriefData(data);
      setEditedScenes(data?.scenes ? [...data.scenes] : []);
      setIsEditing(false);
      setNotice(null);
    } catch (e) {
      setError((e as Error).message);
      setBriefData(null);
      setEditedScenes([]);
    } finally {
      setLoading(false);
    }
  }, [projectId, chapterNumber]);

  useEffect(() => {
    loadBrief();
  }, [loadBrief]);

  const canLeaveCurrentBrief = () =>
    !hasChanges || window.confirm("当前修改尚未保存，确定放弃修改并切换章节吗？");

  const handlePrev = () => {
    if (!canLeaveCurrentBrief()) return;
    setCurrentIndex((i) => Math.max(0, i - 1));
  };

  const handleNext = () => {
    if (!canLeaveCurrentBrief()) return;
    setCurrentIndex((i) => Math.min(spineData.length - 1, i + 1));
  };

  const handleStartEditing = () => {
    setError(null);
    setNotice(null);
    setIsEditing(true);
  };

  const handleCancelEditing = () => {
    if (hasChanges && !window.confirm("确定放弃尚未保存的修改吗？")) return;
    setEditedScenes(briefData?.scenes ? [...briefData.scenes] : []);
    setError(null);
    setIsEditing(false);
  };

  const handleFieldChange = (
    sceneIndex: number,
    field: keyof SceneBriefItem,
    value: string
  ) => {
    setNotice(null);
    setEditedScenes((prev) =>
      prev.map((s, i) => (i === sceneIndex ? { ...s, [field]: value } : s))
    );
  };

  const handleGenerate = async () => {
    if (!projectId || generating) return;
    if (briefData && !window.confirm("重新生成会覆盖当前场景简报，是否继续？")) return;
    setGenerating(true);
    setError(null);
    try {
      await api.generateSceneBrief(projectId, chapterNumber);
      await loadBrief();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setGenerating(false);
    }
  };

  const handleDiscard = async () => {
    if (!projectId || discarding) return;
    if (!window.confirm(`确定丢弃第 ${chapterNumber} 章的整份场景简报吗？此操作无法撤销。`)) return;
    setDiscarding(true);
    setError(null);
    try {
      await api.discardSceneBrief(projectId, chapterNumber);
      setBriefData(null);
      setEditedScenes([]);
      setIsEditing(false);
      setNotice("场景简报已丢弃");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setDiscarding(false);
    }
  };

  const handleSave = async () => {
    if (!projectId || saving) return;
    setSaving(true);
    setError(null);
    try {
      const payload = {
        ...briefData,
        scenes: editedScenes,
        source: "user_edited",
      };
      await api.saveSceneBrief(projectId, chapterNumber, payload);
      await loadBrief();
      setIsEditing(false);
      setNotice("修改已保存");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const handleKeepCurrentCount = async () => {
    if (!projectId || saving || !briefData) return;
    setSaving(true);
    setError(null);
    try {
      await api.saveSceneBrief(projectId, chapterNumber, {
        ...briefData,
        scenes: editedScenes,
        source: "user_edited",
        scene_count_user_overridden: true,
      });
      await loadBrief();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const expired = briefData ? isExpired(briefData.expires_when) : false;

  return (
    <div className="scene-brief-panel flex h-full min-h-0 flex-col">
      <div className="scene-brief-panel__chapter-nav flex items-center justify-between border-b border-pine-200/40 px-5 py-3.5">
        <button
          onClick={handlePrev}
          disabled={currentIndex <= 0}
          className="scene-brief-panel__chapter-button group rounded-lg p-1.5 text-pine-600 transition-all hover:bg-white/50 hover:text-pine-950 disabled:opacity-30 disabled:hover:bg-transparent"
        >
          <ChevronLeft className="h-4 w-4 transition-transform group-enabled:hover:-translate-x-0.5" />
        </button>
        <div className="flex min-w-0 flex-1 items-center justify-center gap-2.5 px-4">
          <span className="scene-brief-panel__chapter-count flex h-6 items-center rounded-full bg-white/60 px-2.5 text-[11px] font-medium tabular-nums text-pine-700 ring-1 ring-inset ring-white/60">
            {String(currentIndex + 1).padStart(2, "0")}
            <span className="mx-1 text-pine-400">/</span>
            {String(spineData.length).padStart(2, "0")}
          </span>
          <div className="flex min-w-0 items-baseline gap-2">
            <span className="text-xs font-medium text-pine-500">第{chapterNumber}章</span>
            <span className="truncate text-sm font-semibold text-pine-950">
              {chapterTitle || "—"}
            </span>
          </div>
        </div>
        <button
          onClick={handleNext}
          disabled={currentIndex >= spineData.length - 1}
          className="scene-brief-panel__chapter-button group rounded-lg p-1.5 text-pine-600 transition-all hover:bg-white/50 hover:text-pine-950 disabled:opacity-30 disabled:hover:bg-transparent"
        >
          <ChevronRight className="h-4 w-4 transition-transform group-enabled:hover:translate-x-0.5" />
        </button>
      </div>

      <div className="scene-brief-panel__toolbar flex shrink-0 items-center justify-between gap-3 border-b border-pine-200/40 bg-white/35 px-5 py-2.5">
        <div className="flex min-w-0 items-center gap-2 text-xs">
          <Pencil className="scene-brief-panel__title-icon h-3.5 w-3.5 shrink-0 text-pine-600" />
          <span className="font-medium text-pine-900">
            {isEditing ? "正在编辑场景简报" : "场景简报"}
          </span>
          {hasChanges && (
            <span className="rounded bg-magic-500/15 px-1.5 py-0.5 text-[10px] font-medium text-magic-700">
              未保存
            </span>
          )}
          {!hasChanges && notice && (
            <span className="flex items-center gap-1 text-[11px] text-pine-600">
              <CheckCircle2 className="h-3.5 w-3.5" />
              {notice}
            </span>
          )}
        </div>

        {briefData && (
          <div className="flex shrink-0 items-center gap-2">
            {isEditing ? (
              <>
                <button
                  type="button"
                  onClick={handleCancelEditing}
                  disabled={saving}
                  className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium text-pine-700 transition-colors hover:bg-pine-900/[0.05] hover:text-pine-950 disabled:opacity-50"
                >
                  <X className="h-3.5 w-3.5" />
                  取消
                </button>
                <button
                  type="button"
                  onClick={handleSave}
                  disabled={saving || !hasChanges || editedScenes.length === 0}
                  className="inline-flex items-center gap-1.5 rounded-md bg-pine-700 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-pine-800 disabled:cursor-not-allowed disabled:opacity-45"
                >
                  {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
                  保存修改
                </button>
              </>
            ) : (
              <button
                type="button"
                onClick={handleStartEditing}
                className="scene-brief-panel__edit-button inline-flex items-center gap-1.5 rounded-md border border-pine-700/20 bg-white/70 px-3 py-1.5 text-xs font-medium text-pine-800 transition-colors hover:border-pine-700/35 hover:bg-white hover:text-pine-950"
              >
                <Pencil className="h-3.5 w-3.5" />
                编辑简报
              </button>
            )}
          </div>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        {briefData?.recommended_scene_count &&
          editedScenes.length < briefData.recommended_scene_count &&
          !briefData.scene_count_user_overridden && (
            <div className="mb-4 overflow-hidden rounded-xl border border-monet-300/50 bg-gradient-to-br from-monet-50 to-monet-100 p-4 text-xs shadow-sm">
              <div className="flex items-start gap-2.5">
                <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-monet-500 text-white">
                  <Sparkles className="h-3 w-3" />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="font-medium text-monet-800">
                    当前 {editedScenes.length} 个场景，建议补充到 {briefData.recommended_scene_count} 个
                  </p>
                  {briefData.scene_budget_reasons && briefData.scene_budget_reasons.length > 0 && (
                    <p className="mt-1 text-monet-600">
                      {briefData.scene_budget_reasons.join("；")}
                    </p>
                  )}
                  <button
                    onClick={handleKeepCurrentCount}
                    disabled={saving}
                    className="mt-2.5 inline-flex items-center gap-1 rounded-md bg-monet-600 px-2.5 py-1 text-[11px] font-medium text-white transition-colors hover:bg-monet-700 disabled:opacity-50"
                  >
                    保持当前数量
                  </button>
                </div>
              </div>
            </div>
          )}
        {loading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="h-6 w-6 animate-spin text-pine-700" />
          </div>
        ) : editedScenes.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-pine-700">
            <p className="text-sm">暂无场景简报</p>
            <p className="mt-1 text-xs">点击下方"生成场景简报"按钮创建</p>
          </div>
        ) : (
          <div className="space-y-4">
            {editedScenes.map((scene, idx) => (
              <SceneBriefCard
                key={scene.scene_id || idx}
                scene={scene}
                index={idx}
                editing={isEditing}
                expired={expired}
                source={briefData?.source}
                onChange={(field, value) => handleFieldChange(idx, field, value)}
              />
            ))}
          </div>
        )}
      </div>

      {error && (
        <div className="mx-4 mb-2 rounded-lg border border-crimson-500/30 bg-crimson-500/10 p-3 text-xs text-crimson-400">
          {error}
        </div>
      )}

      <div className="scene-brief-panel__footer flex shrink-0 items-center gap-2 border-t border-pine-200/40 bg-white/25 px-5 py-3">
        <button
          onClick={handleGenerate}
          disabled={generating || loading}
          className="scene-brief-panel__generate flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-gradient-to-r from-magic-500 to-magic-400 px-3 py-2 text-sm font-medium text-pine-950 shadow-sm transition-all hover:from-magic-400 hover:to-magic-300 hover:shadow disabled:opacity-50 disabled:shadow-none"
        >
          {generating ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Sparkles className="h-4 w-4" />
          )}
          {briefData ? "重新生成" : "生成场景简报"}
        </button>
        <button
          onClick={handleDiscard}
          disabled={discarding || loading || !briefData}
          className="scene-brief-panel__discard flex items-center justify-center gap-1.5 rounded-lg border border-white/40 bg-white/30 px-3 py-2 text-sm text-pine-700 transition-colors hover:bg-white/50 hover:text-pine-950 disabled:opacity-50"
        >
          {discarding ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Trash2 className="h-4 w-4" />
          )}
          丢弃
        </button>
      </div>
    </div>
  );
}
