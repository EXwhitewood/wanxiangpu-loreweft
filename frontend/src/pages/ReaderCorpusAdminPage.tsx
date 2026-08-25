import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { Database, RefreshCw, UploadCloud, Trash2, Wand2 } from "lucide-react";
import {
  deleteReaderCorpusSource,
  getReaderCorpusSummary,
  importReaderCorpusFolder,
  listReaderCorpusSources,
  listReaderExperiencePatterns,
  previewReaderCorpusGuidance,
  rebuildReaderCorpusPatterns,
  uploadReaderCorpusFiles,
} from "@/api/client";
import type {
  ReaderCorpusSource,
  ReaderCorpusSummary,
  ReaderExperienceGuidance,
  ReaderExperiencePattern,
} from "@/types";

const emptySummary: ReaderCorpusSummary = {
  source_count: 0,
  chapter_count: 0,
  pattern_count: 0,
  genres: {},
  pattern_types: {},
};

export default function ReaderCorpusAdminPage() {
  const [summary, setSummary] = useState<ReaderCorpusSummary>(emptySummary);
  const [sources, setSources] = useState<ReaderCorpusSource[]>([]);
  const [patterns, setPatterns] = useState<ReaderExperiencePattern[]>([]);
  const [guidance, setGuidance] = useState<ReaderExperienceGuidance | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [genre, setGenre] = useState("");
  const [platform, setPlatform] = useState("");
  const [qualityTier, setQualityTier] = useState("A");
  const [tags, setTags] = useState("");
  const [folderPath, setFolderPath] = useState("data/private_corpus/inbox");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const topPatternTypes = useMemo(
    () => Object.entries(summary.pattern_types || {}).sort((a, b) => b[1] - a[1]).slice(0, 6),
    [summary.pattern_types]
  );

  async function refresh() {
    const [nextSummary, sourceResp, patternResp] = await Promise.all([
      getReaderCorpusSummary(),
      listReaderCorpusSources(50, 0),
      listReaderExperiencePatterns({ limit: 30 }),
    ]);
    setSummary(nextSummary);
    setSources(sourceResp.items);
    setPatterns(patternResp.items);
  }

  useEffect(() => {
    refresh().catch((err) => setMessage(err.message || "加载失败"));
  }, []);

  async function runAction(action: () => Promise<unknown>, success: string) {
    setBusy(true);
    setMessage("");
    try {
      await action();
      await refresh();
      setMessage(success);
    } catch (err) {
      setMessage((err as Error).message || "操作失败");
    } finally {
      setBusy(false);
    }
  }

  async function handleUpload() {
    if (!files.length) {
      setMessage("请先选择 txt、md、epub、docx 或 zip 文件");
      return;
    }
    await runAction(
      () => uploadReaderCorpusFiles({
        files,
        genre,
        platform,
        quality_tier: qualityTier,
        tags,
      }),
      "上传与训练完成"
    );
    setFiles([]);
  }

  async function handlePreview() {
    setBusy(true);
    setMessage("");
    try {
      const result = await previewReaderCorpusGuidance({
        genre,
        chapter_number: 1,
        scene_index: 0,
        limit: 5,
      });
      setGuidance(result);
      setMessage("已生成预览指导");
    } catch (err) {
      setMessage((err as Error).message || "预览失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-full bg-white/50 p-6 text-pine-700">
      <div className="mx-auto max-w-7xl space-y-6">
        <header className="rounded-2xl border border-magic-500/20 bg-white/50 p-6 shadow-xl">
          <div className="flex items-center justify-between gap-4">
            <div>
              <div className="flex items-center gap-3">
                <Database className="h-6 w-6 text-magic-400" />
                <h1 className="text-2xl font-semibold">内部读者经验库</h1>
              </div>
              <p className="mt-2 max-w-3xl text-sm text-pine-700">
                这是管理员私有入口。上传的小说会被离线拆章、提炼节奏/爽点/留读规律，
                生成时只注入少量聚合建议，不向普通用户暴露。
              </p>
            </div>
            <button
              onClick={() => runAction(refresh, "已刷新")}
              disabled={busy}
              className="inline-flex items-center gap-2 rounded-lg border border-pine-200 px-4 py-2 text-sm text-pine-700 hover:bg-white/50 disabled:opacity-60"
            >
              <RefreshCw className="h-4 w-4" />
              刷新
            </button>
          </div>
        </header>

        <section className="grid gap-4 md:grid-cols-4">
          <MetricCard label="作品数" value={summary.source_count} />
          <MetricCard label="章节样本" value={summary.chapter_count} />
          <MetricCard label="经验模式" value={summary.pattern_count} />
          <MetricCard label="题材数" value={Object.keys(summary.genres || {}).length} />
        </section>

        {message && (
          <div className="rounded-xl border border-magic-500/30 bg-magic-500/10 px-4 py-3 text-sm text-magic-200">
            {message}
          </div>
        )}

        <section className="grid gap-6 lg:grid-cols-[1.1fr_0.9fr]">
          <div className="rounded-2xl border border-pine-200 bg-white/50 p-5">
            <h2 className="mb-4 flex items-center gap-2 text-lg font-semibold">
              <UploadCloud className="h-5 w-5 text-magic-400" />
              上传并训练
            </h2>
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="题材">
                <input value={genre} onChange={(e) => setGenre(e.target.value)} placeholder="如：玄幻、悬疑、都市"
                  className="w-full rounded-lg border border-pine-200 bg-white/50 px-3 py-2 text-sm outline-none focus:border-magic-400" />
              </Field>
              <Field label="平台/来源标签">
                <input value={platform} onChange={(e) => setPlatform(e.target.value)} placeholder="可选"
                  className="w-full rounded-lg border border-pine-200 bg-white/50 px-3 py-2 text-sm outline-none focus:border-magic-400" />
              </Field>
              <Field label="质量等级">
                <select value={qualityTier} onChange={(e) => setQualityTier(e.target.value)}
                  className="w-full rounded-lg border border-pine-200 bg-white/50 px-3 py-2 text-sm outline-none focus:border-magic-400">
                  <option value="S">S</option>
                  <option value="A">A</option>
                  <option value="B">B</option>
                  <option value="C">C</option>
                  <option value="unknown">unknown</option>
                </select>
              </Field>
              <Field label="标签">
                <input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="逗号分隔，如：强开头,高留存"
                  className="w-full rounded-lg border border-pine-200 bg-white/50 px-3 py-2 text-sm outline-none focus:border-magic-400" />
              </Field>
            </div>
            <div className="mt-4 rounded-xl border border-dashed border-pine-200 bg-white/50 p-4">
              <input
                type="file"
                multiple
                accept=".txt,.md,.epub,.docx,.zip"
                onChange={(e) => setFiles(Array.from(e.target.files || []))}
                className="block w-full text-sm text-pine-700 file:mr-4 file:rounded-lg file:border-0 file:bg-magic-500 file:px-4 file:py-2 file:text-sm file:font-medium file:text-pine-700"
              />
              <p className="mt-2 text-xs text-pine-700">
                支持 txt、md、epub、docx、zip。zip 内会批量导入支持格式。
              </p>
            </div>
            <div className="mt-4 flex flex-wrap gap-3">
              <button
                onClick={handleUpload}
                disabled={busy}
                className="rounded-lg bg-magic-500 px-4 py-2 text-sm font-medium text-pine-700 hover:bg-magic-400 disabled:opacity-60"
              >
                上传并训练
              </button>
              <button
                onClick={() => runAction(() => rebuildReaderCorpusPatterns(), "经验模式已重建")}
                disabled={busy}
                className="rounded-lg border border-pine-200 px-4 py-2 text-sm text-pine-700 hover:bg-white/50 disabled:opacity-60"
              >
                重建经验模式
              </button>
            </div>
          </div>

          <div className="rounded-2xl border border-pine-200 bg-white/50 p-5">
            <h2 className="mb-4 flex items-center gap-2 text-lg font-semibold">
              <Wand2 className="h-5 w-5 text-magic-400" />
              内部预览
            </h2>
            <div className="space-y-3">
              <Field label="从本地目录导入">
                <input value={folderPath} onChange={(e) => setFolderPath(e.target.value)}
                  className="w-full rounded-lg border border-pine-200 bg-white/50 px-3 py-2 text-sm outline-none focus:border-magic-400" />
              </Field>
              <button
                onClick={() => runAction(
                  () => importReaderCorpusFolder({ folder_path: folderPath, genre, platform, quality_tier: qualityTier, tags: tags.split(",").filter(Boolean) }),
                  "目录导入完成"
                )}
                disabled={busy}
                className="rounded-lg border border-pine-200 px-4 py-2 text-sm text-pine-700 hover:bg-white/50 disabled:opacity-60"
              >
                导入目录
              </button>
              <div className="border-t border-pine-200 pt-4">
                <button
                  onClick={handlePreview}
                  disabled={busy}
                  className="rounded-lg bg-emerald-500 px-4 py-2 text-sm font-medium text-pine-700 hover:bg-emerald-400 disabled:opacity-60"
                >
                  预览生成注入建议
                </button>
                {guidance && (
                  <div className="mt-4 rounded-xl border border-emerald-500/20 bg-emerald-500/5 p-4 text-sm">
                    <p className="text-emerald-300">
                      {guidance.genre || "未标注题材"} / {guidance.chapter_position} / {guidance.scene_position}
                    </p>
                    <ul className="mt-3 space-y-2 text-pine-700">
                      {guidance.guidance.map((item) => <li key={item}>• {item}</li>)}
                      {guidance.avoid_patterns.map((item) => <li key={item}>• 避免：{item}</li>)}
                    </ul>
                  </div>
                )}
              </div>
            </div>
          </div>
        </section>

        <section className="grid gap-6 lg:grid-cols-[1fr_1fr]">
          <div className="rounded-2xl border border-pine-200 bg-white/50 p-5">
            <h2 className="mb-4 text-lg font-semibold">已导入作品</h2>
            <div className="space-y-3">
              {sources.length === 0 && <p className="text-sm text-pine-700">暂无语料。</p>}
              {sources.map((source) => (
                <div key={source.id} className="rounded-xl border border-pine-200 bg-white/50 p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="font-medium text-pine-700">{source.title}</p>
                      <p className="mt-1 text-xs text-pine-700">
                        {source.genre || "未标注"} · {source.quality_tier} · {source.chapter_count}章 · {source.word_count}字
                      </p>
                    </div>
                    <button
                      onClick={() => runAction(() => deleteReaderCorpusSource(source.id), "已删除语料")}
                      disabled={busy}
                      className="rounded-lg p-2 text-pine-700 hover:bg-red-500/10 hover:text-red-300 disabled:opacity-60"
                      title="删除"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-2xl border border-pine-200 bg-white/50 p-5">
            <h2 className="mb-4 text-lg font-semibold">经验模式</h2>
            <div className="mb-4 flex flex-wrap gap-2">
              {topPatternTypes.map(([key, count]) => (
                <span key={key} className="rounded-full border border-magic-500/20 bg-magic-500/10 px-3 py-1 text-xs text-magic-200">
                  {key}: {count}
                </span>
              ))}
            </div>
            <div className="space-y-3">
              {patterns.slice(0, 12).map((pattern) => (
                <div key={pattern.id} className="rounded-xl border border-pine-200 bg-white/50 p-4">
                  <p className="text-sm font-medium text-pine-700">{pattern.pattern_type} / {pattern.pattern_key}</p>
                  <p className="mt-1 text-xs text-pine-700">
                    {pattern.genre || "通用"} · {pattern.chapter_position} · score {pattern.score}
                  </p>
                  <p className="mt-2 text-sm text-pine-700">{String(pattern.guidance?.prompt || "")}</p>
                </div>
              ))}
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}

function MetricCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-2xl border border-pine-200 bg-white/50 p-5">
      <p className="text-sm text-pine-700">{label}</p>
      <p className="mt-2 text-3xl font-semibold text-magic-300">{value}</p>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs text-pine-700">{label}</span>
      {children}
    </label>
  );
}
