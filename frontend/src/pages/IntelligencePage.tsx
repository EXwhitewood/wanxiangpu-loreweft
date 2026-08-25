import { lazy, Suspense, useEffect, useState } from "react";
import type { CSSProperties } from "react";
import { useParams } from "react-router-dom";
import {
  BarChart3,
  MessageSquare,
  GitBranch,
  Search,
  Loader2,
  Network,
  Sliders,
} from "lucide-react";
import * as api from "@/api/client";
import type { ChapterListItem, ForeshadowingLine, Character, TimelineItem } from "@/types";
import BranchTree from "@/components/Intelligence/BranchTree";
import AdvancedControls from "@/components/Intelligence/AdvancedControls";
import { displayValue, formatChapterLabel } from "@/utils/chineseDisplay";

const IntelligenceChart = lazy(() => import("@/components/Intelligence/IntelligenceChart"));

function NarrativeChart({
  option,
  style,
}: {
  option: Record<string, unknown>;
  style?: CSSProperties;
}) {
  return (
    <Suspense
      fallback={(
        <div
          className="flex items-center justify-center text-xs text-pine-700"
          style={style}
          role="status"
        >
          正在加载图表
        </div>
      )}
    >
      <IntelligenceChart option={option} style={style} />
    </Suspense>
  );
}

type TabKey = "dashboard" | "commentary" | "branches" | "detective" | "tree" | "controls";

interface CommentaryReport {
  overall_score: number;
  dimensions?: Record<string, { score: number; comment: string }>;
  highlights?: string[];
  issues?: Array<Record<string, string>>;
  suggestions?: string[];
  foreshadowing_analysis?: string;
  emotional_arc?: string;
}

interface DetectiveReport {
  total_issues: number;
  severity_breakdown?: Record<string, number>;
  issues?: Array<Record<string, string>>;
  consistency_score: number;
  summary: string;
}

interface BranchData {
  branch_point?: string;
  branches?: Array<Record<string, unknown>>;
  recommendation?: string;
}

export default function IntelligencePage() {
  const { id } = useParams<{ id: string }>();
  const [activeTab, setActiveTab] = useState<TabKey>("dashboard");
  const [chapters, setChapters] = useState<ChapterListItem[]>([]);
  const [foreshadowing, setForeshadowing] = useState<ForeshadowingLine[]>([]);
  const [characters, setCharacters] = useState<Character[]>([]);
  const [commentary, setCommentary] = useState<CommentaryReport | null>(null);
  const [detectiveReport, setDetectiveReport] = useState<DetectiveReport | null>(null);
  const [branches, setBranches] = useState<BranchData | null>(null);
  const [loading, setLoading] = useState(false);
  const [selectedChapter, setSelectedChapter] = useState<number | null>(null);
  const [timelines, setTimelines] = useState<TimelineItem[]>([]);
  const [errorMessage, setErrorMessage] = useState("");

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    setErrorMessage("");
    void Promise.allSettled([
      api.getChapters(id),
      api.listForeshadowing(id),
      api.listCharacters(id),
      api.listTimelines(id),
    ]).then((results) => {
      if (cancelled) return;
      const [chapterResult, foreshadowResult, characterResult, timelineResult] = results;
      if (chapterResult.status === "fulfilled") setChapters(chapterResult.value);
      if (foreshadowResult.status === "fulfilled") setForeshadowing(foreshadowResult.value);
      if (characterResult.status === "fulfilled") setCharacters(characterResult.value);
      if (timelineResult.status === "fulfilled") setTimelines(timelineResult.value);
      if (results.some((result) => result.status === "rejected")) {
        setErrorMessage("部分情报数据加载失败，请检查软件连接状态后重试。");
      }
    });
    return () => {
      cancelled = true;
    };
  }, [id]);

  const handleCommentary = async () => {
    if (!id) return;
    setLoading(true);
    setErrorMessage("");
    try {
      const result = await api.generateCommentary(id, {
        chapter_number: selectedChapter,
      });
      setCommentary(result.report as unknown as CommentaryReport);
    } catch (e) {
      setErrorMessage(e instanceof Error ? e.message : "情节评论生成失败");
    } finally {
      setLoading(false);
    }
  };

  const handleDetective = async () => {
    if (!id) return;
    setLoading(true);
    setErrorMessage("");
    try {
      const result = await api.runDetective(id, {
        chapter_number: selectedChapter,
        scan_scope: "chapter",
      });
      setDetectiveReport(result.report as unknown as DetectiveReport);
    } catch (e) {
      setErrorMessage(e instanceof Error ? e.message : "细节侦探运行失败");
    } finally {
      setLoading(false);
    }
  };

  const handleBranches = async () => {
    if (!id) return;
    setLoading(true);
    setErrorMessage("");
    try {
      const result = await api.generateBranches(id, {
        chapter_number: selectedChapter || 1,
        branch_count: 3,
      });
      setBranches(result.branches as BranchData);
    } catch (e) {
      setErrorMessage(e instanceof Error ? e.message : "分支探索生成失败");
    } finally {
      setLoading(false);
    }
  };

  const tabs: { key: TabKey; label: string; icon: React.ReactNode }[] = [
    { key: "dashboard", label: "叙事仪表盘", icon: <BarChart3 className="h-4 w-4" /> },
    { key: "commentary", label: "情节评论", icon: <MessageSquare className="h-4 w-4" /> },
    { key: "branches", label: "分支探索", icon: <GitBranch className="h-4 w-4" /> },
    { key: "detective", label: "细节侦探", icon: <Search className="h-4 w-4" /> },
    { key: "tree", label: "分支树", icon: <Network className="h-4 w-4" /> },
    ...(import.meta.env.DEV
      ? [{ key: "controls" as TabKey, label: "开发调试", icon: <Sliders className="h-4 w-4" /> }]
      : []),
  ];

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-1 border-b border-pine-200/40 bg-white/50 px-4 py-2">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
              activeTab === tab.key
                ? "bg-magic-500/15 text-magic-400"
                : "text-pine-700 hover:text-pine-700 hover:bg-white/50"
            }`}
          >
            {tab.icon}
            {tab.label}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-auto p-6">
        {errorMessage && (
          <div className="mb-4 rounded-xl border border-rose-300/50 bg-rose-50/80 px-4 py-3 text-sm text-rose-700">
            {errorMessage}
          </div>
        )}
        {activeTab === "dashboard" && (
          <DashboardPanel
            chapters={chapters}
            foreshadowing={foreshadowing}
            characters={characters}
          />
        )}
        {activeTab === "commentary" && (
          <CommentaryPanel
            chapters={chapters}
            selectedChapter={selectedChapter}
            onSelectChapter={setSelectedChapter}
            onGenerate={handleCommentary}
            commentary={commentary}
            loading={loading}
          />
        )}
        {activeTab === "branches" && (
          <BranchPanel
            chapters={chapters}
            selectedChapter={selectedChapter}
            onSelectChapter={setSelectedChapter}
            onGenerate={handleBranches}
            branches={branches}
            loading={loading}
          />
        )}
        {activeTab === "detective" && (
          <DetectivePanel
            chapters={chapters}
            selectedChapter={selectedChapter}
            onSelectChapter={setSelectedChapter}
            onGenerate={handleDetective}
            report={detectiveReport}
            loading={loading}
          />
        )}
        {activeTab === "tree" && (
          <div className="space-y-4">
            <h2 className="text-lg font-semibold text-pine-700">分支树可视化</h2>
            <BranchTree
              timelines={timelines}
              chapters={chapters}
              onSwitchTimeline={async (timelineId) => {
                if (!id) return;
                try {
                  await api.switchTimeline(id, timelineId);
                  setTimelines(await api.listTimelines(id));
                } catch (e) {
                  setErrorMessage(e instanceof Error ? e.message : "时间线切换失败");
                }
              }}
            />
            {timelines.length > 0 && (
              <div className="rounded-xl border border-pine-200/40 bg-white/50 p-4">
                <h3 className="mb-2 text-sm font-medium text-pine-700">时间线列表</h3>
                <div className="space-y-1">
                  {timelines.map((t) => (
                    <div key={t.id} className="flex items-center justify-between rounded-lg px-3 py-1.5 text-xs">
                      <div className="flex items-center gap-2">
                        {t.is_active && <span className="h-1.5 w-1.5 rounded-full bg-magic-400" />}
                        <span className={t.is_active ? "text-magic-400 font-medium" : "text-pine-700"}>{t.name}</span>
                      </div>
                      <span className="text-pine-700">
                        {t.parent ? `从第${t.branch_chapter || "?"}章分出` : "主线"}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
        {import.meta.env.DEV && activeTab === "controls" && <AdvancedControls />}
      </div>
    </div>
  );
}

function DashboardPanel({
  chapters,
  foreshadowing,
  characters,
}: {
  chapters: ChapterListItem[];
  foreshadowing: ForeshadowingLine[];
  characters: Character[];
}) {
  const rhythmOption = {
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" as const },
    xAxis: {
      type: "category" as const,
      data: chapters.map((c) => `第${c.chapter_number}章`),
      axisLabel: { color: "#9ca3af", fontSize: 10 },
      axisLine: { lineStyle: { color: "#374151" } },
    },
    yAxis: {
      type: "value" as const,
      name: "张力值",
      nameTextStyle: { color: "#9ca3af" },
      axisLabel: { color: "#9ca3af" },
      splitLine: { lineStyle: { color: "#1f2937" } },
    },
    series: [
      {
        type: "line",
        data: chapters.map((c) => {
          const wordCount = c.word_count || 0;
          return Math.min(10, Math.max(1, Math.round(wordCount / 500)));
        }),
        smooth: true,
        lineStyle: { color: "#f59e0b", width: 2 },
        areaStyle: {
          color: {
            type: "linear" as const,
            x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: "rgba(245,158,11,0.3)" },
              { offset: 1, color: "rgba(245,158,11,0.02)" },
            ],
          },
        },
        itemStyle: { color: "#f59e0b" },
      },
    ],
    grid: { left: 50, right: 20, top: 30, bottom: 30 },
  };

  const povData = characters.reduce<Record<string, number>>((acc, char) => {
    const name = char.name || "未命名";
    acc[name] = (acc[name] || 0) + 1;
    return acc;
  }, {});
  const povOption = {
    backgroundColor: "transparent",
    tooltip: { trigger: "item" as const },
    series: [
      {
        type: "pie",
        radius: ["40%", "70%"],
        data: Object.entries(povData).map(([name, value]) => ({
          name,
          value,
          itemStyle: {
            color: ["#f59e0b", "#ef4444", "#10b981", "#6366f1", "#ec4899", "#14b8a6"][
              Object.keys(povData).indexOf(name) % 6
            ],
          },
        })),
        label: { color: "#9ca3af", fontSize: 11 },
      },
    ],
  };

  const foreshadowStatuses = foreshadowing.reduce<Record<string, number>>((acc, f) => {
    const status = f.status || "unknown";
    acc[status] = (acc[status] || 0) + 1;
    return acc;
  }, {});
  const foreshadowOption = {
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" as const },
    xAxis: {
      type: "category" as const,
      data: Object.keys(foreshadowStatuses).map((s) =>
        displayValue(s, "其他状态")
      ),
      axisLabel: { color: "#9ca3af" },
      axisLine: { lineStyle: { color: "#374151" } },
    },
    yAxis: {
      type: "value" as const,
      axisLabel: { color: "#9ca3af" },
      splitLine: { lineStyle: { color: "#1f2937" } },
    },
    series: [
      {
        type: "bar",
        data: Object.values(foreshadowStatuses),
        itemStyle: {
          color: (params: { dataIndex: number }) =>
            ["#f59e0b", "#10b981", "#6b7280", "#ef4444"][params.dataIndex % 4],
        },
        barWidth: 40,
      },
    ],
    grid: { left: 40, right: 20, top: 20, bottom: 30 },
  };

  return (
    <div className="space-y-6">
      <h2 className="text-lg font-semibold text-pine-700">叙事仪表盘</h2>

      <div className="grid grid-cols-2 gap-6">
        <div className="rounded-xl border border-pine-200/40 bg-white/50 p-4">
          <h3 className="mb-3 text-sm font-medium text-pine-700">节奏曲线</h3>
          <NarrativeChart option={rhythmOption} style={{ height: 250 }} />
        </div>

        <div className="rounded-xl border border-pine-200/40 bg-white/50 p-4">
          <h3 className="mb-3 text-sm font-medium text-pine-700">视角分布</h3>
          {Object.keys(povData).length > 0 ? (
            <NarrativeChart option={povOption} style={{ height: 250 }} />
          ) : (
            <div className="flex h-[250px] items-center justify-center text-xs text-pine-700">
              暂无人物数据
            </div>
          )}
        </div>

        <div className="rounded-xl border border-pine-200/40 bg-white/50 p-4">
          <h3 className="mb-3 text-sm font-medium text-pine-700">伏笔进度</h3>
          {foreshadowing.length > 0 ? (
            <NarrativeChart option={foreshadowOption} style={{ height: 250 }} />
          ) : (
            <div className="flex h-[250px] items-center justify-center text-xs text-pine-700">
              暂无伏笔数据
            </div>
          )}
        </div>

        <div className="rounded-xl border border-pine-200/40 bg-white/50 p-4">
          <h3 className="mb-3 text-sm font-medium text-pine-700">章节字数</h3>
          <NarrativeChart
            option={{
              backgroundColor: "transparent",
              tooltip: { trigger: "axis" as const },
              xAxis: {
                type: "category" as const,
                data: chapters.map((c) => `第${c.chapter_number}章`),
                axisLabel: { color: "#9ca3af", fontSize: 10 },
                axisLine: { lineStyle: { color: "#374151" } },
              },
              yAxis: {
                type: "value" as const,
                name: "字数",
                nameTextStyle: { color: "#9ca3af" },
                axisLabel: { color: "#9ca3af" },
                splitLine: { lineStyle: { color: "#1f2937" } },
              },
              series: [{
                type: "bar",
                data: chapters.map((c) => c.word_count || 0),
                itemStyle: { color: "#6366f1" },
                barWidth: 20,
              }],
              grid: { left: 60, right: 20, top: 30, bottom: 30 },
            }}
            style={{ height: 250 }}
          />
        </div>
      </div>

      {foreshadowing.length > 0 && (
        <div className="rounded-xl border border-pine-200/40 bg-white/50 p-4">
          <h3 className="mb-3 text-sm font-medium text-pine-700">伏笔追踪表</h3>
          <div className="overflow-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-pine-200/40 text-pine-700">
                  <th className="py-2 text-left font-medium">名称</th>
                  <th className="py-2 text-left font-medium">核心秘密</th>
                  <th className="py-2 text-left font-medium">埋设章</th>
                  <th className="py-2 text-left font-medium">揭示章</th>
                  <th className="py-2 text-left font-medium">状态</th>
                </tr>
              </thead>
              <tbody>
                {foreshadowing.map((f) => (
                  <tr key={f.id} className="border-b border-pine-200/20">
                    <td className="py-2 text-pine-700">{f.name}</td>
                    <td className="py-2 text-pine-700 max-w-[200px] truncate">{f.secret_canonical_statement}</td>
                    <td className="py-2 text-pine-700">{f.bury_window_start ?? "—"}</td>
                    <td className="py-2 text-pine-700">{f.reveal_window_start ?? "—"}</td>
                    <td className="py-2">
                      <span
                        className={`rounded px-1.5 py-0.5 text-xs ${
                          f.status === "active"
                            ? "bg-green-500/15 text-green-400"
                            : f.status === "resolved"
                            ? "bg-blue-500/15 text-blue-400"
                            : f.status === "dormant"
                            ? "bg-yellow-500/15 text-yellow-400"
                            : f.status === "revealing"
                            ? "bg-purple-500/15 text-purple-400"
                            : f.status === "aborted"
                            ? "bg-red-500/15 text-red-400"
                            : "bg-white/50 text-pine-700"
                        }`}
                      >
                        {displayValue(String(f.status), "其他状态")}
                      </span>
                    </td>
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

const DIMENSION_LABELS: Record<string, string> = {
  plot_coherence: "情节连贯",
  character_depth: "人物深度",
  pacing: "节奏控制",
  dialogue_quality: "对话质量",
  world_consistency: "世界观一致",
  emotional_impact: "情感冲击",
  thematic_depth: "主题深度",
  foreshadowing: "伏笔运用",
  tension: "张力营造",
  originality: "原创性",
  readability: "可读性",
  immersion: "沉浸感",
};

function CommentaryPanel({
  chapters,
  selectedChapter,
  onSelectChapter,
  onGenerate,
  commentary,
  loading,
}: {
  chapters: ChapterListItem[];
  selectedChapter: number | null;
  onSelectChapter: (n: number | null) => void;
  onGenerate: () => void;
  commentary: CommentaryReport | null;
  loading: boolean;
}) {
  const dims = commentary?.dimensions;

  return (
    <div className="space-y-6">
      <h2 className="text-lg font-semibold text-pine-700">情节评论</h2>

      <div className="flex items-center gap-3">
        <select
          value={selectedChapter ?? ""}
          onChange={(e) => onSelectChapter(e.target.value ? parseInt(e.target.value) : null)}
          className="rounded-lg border border-pine-200 bg-white/50 px-3 py-2 text-sm text-pine-700"
        >
          <option value="">全本</option>
          {chapters.map((c) => (
            <option key={c.chapter_number} value={c.chapter_number}>
              {formatChapterLabel(c.chapter_number, c.title)}
            </option>
          ))}
        </select>
        <button
          onClick={onGenerate}
          disabled={loading}
          className="flex items-center gap-2 rounded-lg bg-magic-500/20 px-4 py-2 text-sm font-medium text-magic-400 transition-colors hover:bg-magic-500/30 disabled:opacity-50"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <MessageSquare className="h-4 w-4" />}
          生成评论
        </button>
      </div>

      {commentary && (
        <div className="space-y-4">
          <div className="flex items-center gap-4 rounded-xl border border-pine-200/40 bg-white/50 p-4">
            <div className="text-center">
              <div className="text-3xl font-bold text-magic-400">
                {commentary.overall_score || 0}
              </div>
              <div className="text-xs text-pine-700">总体评分</div>
            </div>
            <div className="flex-1">
              <NarrativeChart
                option={{
                  backgroundColor: "transparent",
                  radar: {
                    indicator: dims
                      ? Object.keys(dims).map((k) => ({
                          name: DIMENSION_LABELS[k] || "其他评价维度",
                          max: 10,
                        }))
                      : [],
                    axisName: { color: "#9ca3af", fontSize: 10 },
                    splitArea: { areaStyle: { color: ["rgba(245,158,11,0.02)", "rgba(245,158,11,0.05)"] } },
                    axisLine: { lineStyle: { color: "#374151" } },
                    splitLine: { lineStyle: { color: "#1f2937" } },
                  },
                  series: [{
                    type: "radar",
                    data: [{
                      value: dims ? Object.values(dims).map((d) => d.score) : [],
                      areaStyle: { color: "rgba(245,158,11,0.2)" },
                      lineStyle: { color: "#f59e0b" },
                      itemStyle: { color: "#f59e0b" },
                    }],
                  }],
                }}
                style={{ height: 250 }}
              />
            </div>
          </div>

          {dims && (
            <div className="grid grid-cols-2 gap-3">
              {Object.entries(dims).map(([key, val]) => (
                <div key={key} className="rounded-lg border border-pine-200/40 bg-white/50 p-3">
                  <div className="mb-1 flex items-center justify-between">
                    <span className="text-xs font-medium text-pine-700">{DIMENSION_LABELS[key] || "其他评价维度"}</span>
                    <span className="text-sm font-bold text-magic-400">{val.score}/10</span>
                  </div>
                  <p className="text-xs text-pine-700">{val.comment}</p>
                </div>
              ))}
            </div>
          )}

          {commentary.highlights && commentary.highlights.length > 0 && (
            <div className="rounded-xl border border-emerald-800/30 bg-emerald-900/10 p-4">
              <h3 className="mb-2 text-sm font-medium text-emerald-400">亮点</h3>
              <ul className="space-y-1">
                {commentary.highlights.map((h, i) => (
                  <li key={i} className="text-xs text-pine-700">• {String(h)}</li>
                ))}
              </ul>
            </div>
          )}

          {commentary.issues && commentary.issues.length > 0 && (
            <div className="rounded-xl border border-red-800/30 bg-red-900/10 p-4">
              <h3 className="mb-2 text-sm font-medium text-red-400">问题</h3>
              <ul className="space-y-1">
                {commentary.issues.map((issue, i) => (
                  <li key={i} className="text-xs text-pine-700">
                    • {issue.description || "这处问题暂时没有可读说明。"}
                    {issue.suggestion && <span className="text-pine-700"> — 建议: {issue.suggestion}</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {commentary.suggestions && commentary.suggestions.length > 0 && (
            <div className="rounded-xl border border-pine-200/40 bg-white/50 p-4">
              <h3 className="mb-2 text-sm font-medium text-pine-700">改进建议</h3>
              <ul className="space-y-1">
                {commentary.suggestions.map((s, i) => (
                  <li key={i} className="text-xs text-pine-700">• {String(s)}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function BranchPanel({
  chapters,
  selectedChapter,
  onSelectChapter,
  onGenerate,
  branches,
  loading,
}: {
  chapters: ChapterListItem[];
  selectedChapter: number | null;
  onSelectChapter: (n: number | null) => void;
  onGenerate: () => void;
  branches: BranchData | null;
  loading: boolean;
}) {
  const branchList = branches?.branches;

  return (
    <div className="space-y-6">
      <h2 className="text-lg font-semibold text-pine-700">分支探索</h2>

      <div className="flex items-center gap-3">
        <select
          value={selectedChapter ?? 1}
          onChange={(e) => onSelectChapter(parseInt(e.target.value))}
          className="rounded-lg border border-pine-200 bg-white/50 px-3 py-2 text-sm text-pine-700"
        >
          {chapters.map((c) => (
            <option key={c.chapter_number} value={c.chapter_number}>
              {formatChapterLabel(c.chapter_number, c.title)}
            </option>
          ))}
        </select>
        <button
          onClick={onGenerate}
          disabled={loading}
          className="flex items-center gap-2 rounded-lg bg-magic-500/20 px-4 py-2 text-sm font-medium text-magic-400 transition-colors hover:bg-magic-500/30 disabled:opacity-50"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <GitBranch className="h-4 w-4" />}
          生成分支
        </button>
      </div>

      {branches && (
        <div className="space-y-4">
          <div className="rounded-lg border border-pine-200/40 bg-white/50 p-3">
            <p className="text-xs text-pine-700">
              分支点: {branches.branch_point || "当前章节末尾"}
            </p>
          </div>

          {branchList && branchList.map((branch, i) => (
            <div
              key={i}
              className="rounded-xl border border-pine-200/40 bg-white/50 p-4"
            >
              <div className="mb-2 flex items-center justify-between">
                <h3 className="text-sm font-medium text-pine-700">
                  {String(branch.name || `分支 ${i + 1}`)}
                </h3>
                <div className="flex gap-2">
                  <span className="rounded bg-magic-500/15 px-2 py-0.5 text-xs text-magic-400">
                    惊喜 {String(branch.surprise_score)}/10
                  </span>
                  <span className="rounded bg-emerald-500/15 px-2 py-0.5 text-xs text-emerald-400">
                    一致 {String(branch.consistency_score)}/10
                  </span>
                  <span className="rounded bg-indigo-500/15 px-2 py-0.5 text-xs text-indigo-400">
                    潜力 {String(branch.narrative_potential)}/10
                  </span>
                </div>
              </div>
              <p className="mb-2 text-xs font-medium text-pine-700">
                关键决策: {String(branch.key_decision || "")}
              </p>
              <p className="text-xs text-pine-700">{String(branch.summary || "")}</p>
              {Array.isArray(branch.foreshadowing_opportunities) && branch.foreshadowing_opportunities.length > 0 && (
                <div className="mt-2">
                  <p className="text-xs text-pine-700">伏笔机会:</p>
                  <ul className="ml-3 mt-1 space-y-0.5">
                    {(branch.foreshadowing_opportunities as string[]).map((f, fi) => (
                      <li key={fi} className="text-xs text-pine-700">• {String(f)}</li>
                    ))}
                  </ul>
                </div>
              )}
              {Array.isArray(branch.risks) && branch.risks.length > 0 && (
                <div className="mt-2">
                  <p className="text-xs text-pine-700">潜在风险:</p>
                  <ul className="ml-3 mt-1 space-y-0.5">
                    {(branch.risks as string[]).map((r, ri) => (
                      <li key={ri} className="text-xs text-red-400/70">• {String(r)}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          ))}

          {branches.recommendation && (
            <div className="rounded-xl border border-magic-800/30 bg-magic-900/10 p-4">
              <h3 className="mb-1 text-sm font-medium text-magic-400">推荐</h3>
              <p className="text-xs text-pine-700">{String(branches.recommendation)}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

const SEVERITY_LABELS: Record<string, string> = {
  critical: "严重",
  major: "重要",
  minor: "轻微",
  suggestion: "建议",
};

const CATEGORY_LABELS: Record<string, string> = {
  plot_hole: "情节漏洞",
  character_inconsistency: "人物矛盾",
  timeline_error: "时间线错误",
  world_violation: "世界观违反",
  dialogue_issue: "对话问题",
  description_issue: "描写问题",
  pacing_issue: "节奏问题",
  logic_error: "逻辑错误",
  fact_contradiction: "事实矛盾",
};

function DetectivePanel({
  chapters,
  selectedChapter,
  onSelectChapter,
  onGenerate,
  report,
  loading,
}: {
  chapters: ChapterListItem[];
  selectedChapter: number | null;
  onSelectChapter: (n: number | null) => void;
  onGenerate: () => void;
  report: DetectiveReport | null;
  loading: boolean;
}) {
  const issues = report?.issues;
  const breakdown = report?.severity_breakdown;

  return (
    <div className="space-y-6">
      <h2 className="text-lg font-semibold text-pine-700">细节侦探</h2>

      <div className="flex items-center gap-3">
        <select
          value={selectedChapter ?? ""}
          onChange={(e) => onSelectChapter(e.target.value ? parseInt(e.target.value) : null)}
          className="rounded-lg border border-pine-200 bg-white/50 px-3 py-2 text-sm text-pine-700"
        >
          <option value="">全本</option>
          {chapters.map((c) => (
            <option key={c.chapter_number} value={c.chapter_number}>
              {formatChapterLabel(c.chapter_number, c.title)}
            </option>
          ))}
        </select>
        <button
          onClick={onGenerate}
          disabled={loading}
          className="flex items-center gap-2 rounded-lg bg-magic-500/20 px-4 py-2 text-sm font-medium text-magic-400 transition-colors hover:bg-magic-500/30 disabled:opacity-50"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
          开始排查
        </button>
      </div>

      {report && (
        <div className="space-y-4">
          <div className="flex items-center gap-6 rounded-xl border border-pine-200/40 bg-white/50 p-4">
            <div className="text-center">
              <div className="text-3xl font-bold text-magic-400">
                {report.consistency_score || 0}
              </div>
              <div className="text-xs text-pine-700">一致性评分</div>
            </div>
            <div className="flex-1">
              <p className="text-sm text-pine-700">{report.summary}</p>
            </div>
            {breakdown && (
              <div className="flex gap-3">
                <div className="text-center">
                  <div className="text-lg font-bold text-red-400">{breakdown.critical || 0}</div>
                  <div className="text-xs text-pine-700">严重</div>
                </div>
                <div className="text-center">
                  <div className="text-lg font-bold text-magic-400">{breakdown.major || 0}</div>
                  <div className="text-xs text-pine-700">重要</div>
                </div>
                <div className="text-center">
                  <div className="text-lg font-bold text-yellow-400">{breakdown.minor || 0}</div>
                  <div className="text-xs text-pine-700">轻微</div>
                </div>
                <div className="text-center">
                  <div className="text-lg font-bold text-pine-700">{breakdown.suggestion || 0}</div>
                  <div className="text-xs text-pine-700">建议</div>
                </div>
              </div>
            )}
          </div>

          {issues && issues.length > 0 && (
            <div className="space-y-2">
              {issues.map((issue, i) => (
                <div
                  key={i}
                  className={`rounded-lg border p-3 ${
                    issue.severity === "critical"
                      ? "border-red-800/40 bg-red-900/10"
                      : issue.severity === "major"
                      ? "border-magic-800/40 bg-magic-900/10"
                      : "border-pine-200/40 bg-white/50"
                  }`}
                >
                  <div className="mb-1 flex items-center gap-2">
                    <span
                      className={`rounded px-1.5 py-0.5 text-xs font-medium ${
                        issue.severity === "critical"
                          ? "bg-red-500/20 text-red-400"
                          : issue.severity === "major"
                          ? "bg-magic-500/20 text-magic-400"
                          : issue.severity === "minor"
                          ? "bg-yellow-500/20 text-yellow-400"
                          : "bg-white/50 text-pine-700"
                      }`}
                    >
                      {SEVERITY_LABELS[issue.severity] || "其他级别"}
                    </span>
                    <span className="text-xs text-pine-700">{CATEGORY_LABELS[issue.category] || "其他问题类型"}</span>
                  </div>
                  <p className="text-xs text-pine-700">{issue.description}</p>
                  {issue.evidence && (
                    <p className="mt-1 text-xs text-pine-700 italic">证据: {issue.evidence}</p>
                  )}
                  {issue.suggestion && (
                    <p className="mt-1 text-xs text-emerald-400/70">建议: {issue.suggestion}</p>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
