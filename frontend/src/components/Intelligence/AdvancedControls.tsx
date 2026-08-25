import { useState, useEffect, useCallback } from "react";
import { useParams } from "react-router-dom";
import {
  Save,
  RotateCcw,
  Sliders,
  Clock,
  Type,
  Palette,
  Loader2,
} from "lucide-react";
import * as api from "@/api/client";

interface StoryStateData {
  timeline_id: string;
  narrative_time: string | null;
  active_chapter: number;
  active_scene: number;
  pov_character: string | null;
  objective_state: Record<string, unknown>;
  subjective_views: Record<string, unknown>;
}

interface StyleProfile {
  formality: number;
  descriptiveness: number;
  dialogue_density: number;
  pacing: number;
  emotional_intensity: number;
}

const defaultStyle: StyleProfile = {
  formality: 50,
  descriptiveness: 60,
  dialogue_density: 50,
  pacing: 50,
  emotional_intensity: 50,
};

export default function AdvancedControls() {
  const { id } = useParams<{ id: string }>();
  const [state, setState] = useState<StoryStateData | null>(null);
  const [style, setStyle] = useState<StyleProfile>(defaultStyle);
  const [saving, setSaving] = useState(false);
  const [activeSection, setActiveSection] = useState<"state" | "style" | "rhythm">("state");

  useEffect(() => {
    if (!id) return;
    api.getState(id).then((data) => {
      setState(data as StoryStateData);
    }).catch(() => {});
  }, [id]);

  const handleSaveState = useCallback(async () => {
    if (!id || !state) return;
    setSaving(true);
    try {
      await api.updateState(id, state as unknown as Record<string, unknown>);
    } catch (e) {
      console.error(e);
    } finally {
      setSaving(false);
    }
  }, [id, state]);

  const handleResetState = useCallback(async () => {
    if (!id) return;
    try {
      const data = await api.getState(id);
      setState(data as StoryStateData);
    } catch (e) {
      console.error(e);
    }
  }, [id]);

  const updateStateField = (field: string, value: unknown) => {
    if (!state) return;
    setState({ ...state, [field]: value });
  };

  const sliders: { key: keyof StyleProfile; label: string; icon: React.ReactNode; leftLabel: string; rightLabel: string }[] = [
    { key: "formality", label: "正式度", icon: <Type className="h-3.5 w-3.5" />, leftLabel: "口语化", rightLabel: "书面化" },
    { key: "descriptiveness", label: "描写密度", icon: <Palette className="h-3.5 w-3.5" />, leftLabel: "简洁", rightLabel: "细腻" },
    { key: "dialogue_density", label: "对话密度", icon: <Sliders className="h-3.5 w-3.5" />, leftLabel: "叙述为主", rightLabel: "对话为主" },
    { key: "pacing", label: "节奏", icon: <Clock className="h-3.5 w-3.5" />, leftLabel: "舒缓", rightLabel: "紧凑" },
    { key: "emotional_intensity", label: "情感强度", icon: <Palette className="h-3.5 w-3.5" />, leftLabel: "克制", rightLabel: "浓烈" },
  ];

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-1 border-b border-pine-200/40 pb-2">
        {[
          { key: "state" as const, label: "状态编辑" },
          { key: "style" as const, label: "文风滑块" },
          { key: "rhythm" as const, label: "节奏控制" },
        ].map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveSection(tab.key)}
            className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
              activeSection === tab.key
                ? "bg-magic-500/15 text-magic-400"
                : "text-pine-700 hover:text-pine-700 hover:bg-white/50"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeSection === "state" && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-medium text-pine-700">状态编辑器</h3>
            <div className="flex gap-2">
              <button
                onClick={handleResetState}
                className="flex items-center gap-1 rounded-lg border border-pine-200 px-2 py-1 text-xs text-pine-700 hover:text-pine-700"
              >
                <RotateCcw className="h-3 w-3" /> 重置
              </button>
              <button
                onClick={handleSaveState}
                disabled={saving}
                className="flex items-center gap-1 rounded-lg bg-magic-500/20 px-2 py-1 text-xs text-magic-400 hover:bg-magic-500/30 disabled:opacity-50"
              >
                {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
                保存
              </button>
            </div>
          </div>

          {state && (
            <div className="space-y-2">
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="mb-0.5 block text-[11px] text-pine-700">叙事时间</label>
                  <input
                    type="text"
                    value={state.narrative_time || ""}
                    onChange={(e) => updateStateField("narrative_time", e.target.value)}
                    className="w-full rounded-lg border border-pine-200 bg-white/50 px-2 py-1.5 text-xs text-pine-700"
                    placeholder="例：第三天黄昏"
                  />
                </div>
                <div>
                  <label className="mb-0.5 block text-[11px] text-pine-700">视角角色</label>
                  <input
                    type="text"
                    value={state.pov_character || ""}
                    onChange={(e) => updateStateField("pov_character", e.target.value)}
                    className="w-full rounded-lg border border-pine-200 bg-white/50 px-2 py-1.5 text-xs text-pine-700"
                    placeholder="例：李明"
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="mb-0.5 block text-[11px] text-pine-700">当前章节</label>
                  <input
                    type="number"
                    value={state.active_chapter}
                    onChange={(e) => updateStateField("active_chapter", parseInt(e.target.value) || 1)}
                    className="w-full rounded-lg border border-pine-200 bg-white/50 px-2 py-1.5 text-xs text-pine-700"
                    min={1}
                  />
                </div>
                <div>
                  <label className="mb-0.5 block text-[11px] text-pine-700">当前场景</label>
                  <input
                    type="number"
                    value={state.active_scene}
                    onChange={(e) => updateStateField("active_scene", parseInt(e.target.value) || 1)}
                    className="w-full rounded-lg border border-pine-200 bg-white/50 px-2 py-1.5 text-xs text-pine-700"
                    min={1}
                  />
                </div>
              </div>

              <div>
                <label className="mb-0.5 block text-[11px] text-pine-700">客观状态层（结构化数据）</label>
                <textarea
                  value={JSON.stringify(state.objective_state, null, 2)}
                  onChange={(e) => {
                    try {
                      const parsed = JSON.parse(e.target.value);
                      updateStateField("objective_state", parsed);
                    } catch {}
                  }}
                  className="w-full rounded-lg border border-pine-200 bg-white/50 px-2 py-1.5 text-xs text-pine-700 font-mono"
                  rows={6}
                />
              </div>

              <div>
                <label className="mb-0.5 block text-[11px] text-pine-700">主观认知层（结构化数据）</label>
                <textarea
                  value={JSON.stringify(state.subjective_views, null, 2)}
                  onChange={(e) => {
                    try {
                      const parsed = JSON.parse(e.target.value);
                      updateStateField("subjective_views", parsed);
                    } catch {}
                  }}
                  className="w-full rounded-lg border border-pine-200 bg-white/50 px-2 py-1.5 text-xs text-pine-700 font-mono"
                  rows={4}
                />
              </div>
            </div>
          )}
        </div>
      )}

      {activeSection === "style" && (
        <div className="space-y-4">
          <h3 className="text-sm font-medium text-pine-700">文风微调滑块</h3>
          <p className="text-[11px] text-pine-700">调整生成文本的风格倾向，影响后续章节的AI创作方向。</p>
          {sliders.map((s) => (
            <div key={s.key} className="space-y-1">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-1.5 text-xs text-pine-700">
                  {s.icon}
                  {s.label}
                </div>
                <span className="text-xs font-mono text-magic-400">{style[s.key]}</span>
              </div>
              <input
                type="range"
                min={0}
                max={100}
                value={style[s.key]}
                onChange={(e) => setStyle({ ...style, [s.key]: parseInt(e.target.value) })}
                className="w-full accent-magic-500 h-1.5"
              />
              <div className="flex justify-between text-[10px] text-pine-700">
                <span>{s.leftLabel}</span>
                <span>{s.rightLabel}</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {activeSection === "rhythm" && (
        <div className="space-y-4">
          <h3 className="text-sm font-medium text-pine-700">节奏控制</h3>
          <p className="text-[11px] text-pine-700">控制叙事节奏参数，影响场景切换频率和张力分布。</p>

          <div className="space-y-3">
            <div>
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs text-pine-700">场景长度偏好</span>
                <span className="text-xs font-mono text-magic-400">{style.pacing > 60 ? "短场景" : style.pacing < 40 ? "长场景" : "适中"}</span>
              </div>
              <div className="grid grid-cols-3 gap-2">
                {[
                  { label: "短场景", value: 80, desc: "快节奏，频繁切换" },
                  { label: "适中", value: 50, desc: "均衡节奏" },
                  { label: "长场景", value: 20, desc: "沉浸式，深入描写" },
                ].map((opt) => (
                  <button
                    key={opt.value}
                    onClick={() => setStyle({ ...style, pacing: opt.value })}
                    className={`rounded-lg border px-2 py-2 text-center transition-colors ${
                      Math.abs(style.pacing - opt.value) < 20
                        ? "border-magic-500/40 bg-magic-500/10 text-magic-400"
                        : "border-pine-200 bg-white/50 text-pine-700 hover:border-pine-200"
                    }`}
                  >
                    <div className="text-xs font-medium">{opt.label}</div>
                    <div className="text-[10px] text-pine-700">{opt.desc}</div>
                  </button>
                ))}
              </div>
            </div>

            <div>
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs text-pine-700">张力曲线模式</span>
              </div>
              <div className="grid grid-cols-2 gap-2">
                {[
                  { label: "渐进式", desc: "缓慢升温至高潮" },
                  { label: "波浪式", desc: "多次起伏，层层递进" },
                  { label: "突袭式", desc: "平静后突然爆发" },
                  { label: "持续高压", desc: "全程紧张，无喘息" },
                ].map((mode) => (
                  <button
                    key={mode.label}
                    className="rounded-lg border border-pine-200 bg-white/50 px-2 py-2 text-left transition-colors hover:border-magic-500/40 hover:bg-magic-500/5"
                  >
                    <div className="text-xs font-medium text-pine-700">{mode.label}</div>
                    <div className="text-[10px] text-pine-700">{mode.desc}</div>
                  </button>
                ))}
              </div>
            </div>

            <div>
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs text-pine-700">信息释放速率</span>
                <span className="text-xs font-mono text-magic-400">
                  {style.descriptiveness > 60 ? "快" : style.descriptiveness < 40 ? "慢" : "中"}
                </span>
              </div>
              <input
                type="range"
                min={0}
                max={100}
                value={style.descriptiveness}
                onChange={(e) => setStyle({ ...style, descriptiveness: parseInt(e.target.value) })}
                className="w-full accent-magic-500 h-1.5"
              />
              <div className="flex justify-between text-[10px] text-pine-700">
                <span>悬念保留</span>
                <span>快速揭示</span>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
