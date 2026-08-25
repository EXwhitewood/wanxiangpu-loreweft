import { useCallback, useEffect, useMemo, useState } from "react";
import { Archive, CheckCircle2, Gauge, RefreshCw, ShieldCheck, Zap } from "lucide-react";
import {
  compactContextEnvelope,
  getLatestContextLedger,
  previewContextEnvelope,
  type ContextEnvelopeBlock,
  type ContextEnvelopeResponse,
} from "@/api/client";

interface Props {
  projectId: string;
  chapterNumber: number;
}

function n(value: unknown): number {
  const parsed = Number(value || 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function fmt(value: unknown): string {
  return n(value).toLocaleString("zh-CN");
}

function priorityClass(priority?: string): string {
  if (priority === "P0" || priority === "P1") return "border-emerald-500/30 bg-emerald-500/5 text-emerald-300";
  if (priority === "P2" || priority === "P3") return "border-sky-500/30 bg-sky-500/5 text-sky-300";
  if (priority === "P4") return "border-magic-500/30 bg-magic-500/5 text-magic-300";
  return "border-pine-200 bg-white/50 text-pine-700";
}

function Stat({ icon: Icon, label, value }: { icon: typeof Gauge; label: string; value: string }) {
  return (
    <div className="rounded-lg border border-pine-200/60 bg-white/50 px-3 py-2">
      <div className="flex items-center gap-2 text-xs text-pine-700">
        <Icon className="h-3.5 w-3.5" />
        {label}
      </div>
      <div className="mt-1 text-sm font-semibold text-pine-700">{value}</div>
    </div>
  );
}

function BlockRow({ block }: { block: ContextEnvelopeBlock }) {
  return (
    <div className="grid grid-cols-[1fr_auto] gap-2 rounded-lg border border-pine-200/50 bg-white/50 px-3 py-2">
      <div className="min-w-0">
        <div className="truncate text-xs font-medium text-pine-700">{block.block_id}</div>
        <div className="mt-0.5 truncate text-[11px] text-pine-700">{block.block_type || "context_block"}</div>
        {block.content_preview && (
          <div className="mt-1 line-clamp-2 text-[10px] leading-4 text-pine-700">
            {block.content_preview}
          </div>
        )}
      </div>
      <div className="flex items-center gap-2">
        <span className={`rounded border px-1.5 py-0.5 text-[10px] ${priorityClass(block.priority)}`}>
          {block.priority || "P?"}
        </span>
        <span className="w-14 text-right text-[11px] text-pine-700">
          {fmt(block.estimated_tokens ?? block.tokens)}
        </span>
        {block.compacted ? (
          <Archive className="h-3.5 w-3.5 text-magic-300" />
        ) : block.compressible ? (
          <Zap className="h-3.5 w-3.5 text-sky-300" />
        ) : (
          <ShieldCheck className="h-3.5 w-3.5 text-emerald-300" />
        )}
      </div>
    </div>
  );
}

export default function ContextEnvelopePanel({ projectId, chapterNumber }: Props) {
  const [data, setData] = useState<ContextEnvelopeResponse | null>(null);
  const [ledger, setLedger] = useState<Record<string, unknown> | null>(null);
  const [focus, setFocus] = useState("");
  const [loading, setLoading] = useState(false);
  const [compacting, setCompacting] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    setError("");
    try {
      const result = await previewContextEnvelope(projectId, {
        chapter_number: chapterNumber,
        manual_focus: focus || null,
      });
      setData(result);
      try {
        setLedger(await getLatestContextLedger(projectId, chapterNumber));
      } catch {
        setLedger(result.ledger || null);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [projectId, chapterNumber, focus]);

  useEffect(() => {
    load();
  }, [load]);

  const runCompact = async () => {
    if (!projectId) return;
    setCompacting(true);
    setError("");
    try {
      const result = await compactContextEnvelope(projectId, {
        chapter_number: chapterNumber,
        manual_focus: focus || null,
      });
      setData(result);
      setLedger(result.ledger || null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setCompacting(false);
    }
  };

  const tokenReport = data?.token_report || {};
  const used = n(tokenReport.estimated_input_tokens);
  const limit = n(tokenReport.limit_tokens);
  const ratio = limit > 0 ? Math.min(100, Math.round((used / limit) * 100)) : 0;
  const validation = data?.validation || {};
  const checks = (validation.checks || {}) as Record<string, unknown>;
  const blockGroups = useMemo(() => {
    const blocks = data?.blocks || [];
    return [...blocks].sort((a, b) => String(a.priority || "").localeCompare(String(b.priority || "")));
  }, [data]);

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="border-b border-pine-200/40 px-4 py-3">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold text-pine-700">上下文 Envelope</h3>
            <p className="mt-0.5 text-xs text-pine-700">第{chapterNumber}章 · chapter_writer</p>
          </div>
          <button
            onClick={load}
            disabled={loading || compacting}
            className="rounded-lg p-1.5 text-pine-700 transition-colors hover:bg-white/50 hover:text-pine-700 disabled:opacity-50"
            title="刷新上下文预览"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          </button>
        </div>
      </div>

      <div className="flex-1 space-y-3 overflow-auto px-4 py-3">
        {error && (
          <div className="rounded-lg border border-crimson-500/30 bg-crimson-500/10 px-3 py-2 text-xs text-crimson-300">
            {error}
          </div>
        )}

        <div className="space-y-2">
          <div className="h-2 rounded bg-white/50">
            <div className="h-2 rounded bg-magic-400" style={{ width: `${ratio}%` }} />
          </div>
          <div className="flex justify-between text-[11px] text-pine-700">
            <span>{fmt(used)} / {fmt(limit)} tokens</span>
            <span>{ratio}%</span>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-2">
          <Stat icon={Gauge} label="Soft Limit" value={fmt(tokenReport.soft_input_limit_tokens)} />
          <Stat icon={Archive} label="Saved" value={fmt((data?.compaction || {}).saved_tokens)} />
          <Stat icon={ShieldCheck} label="Pinned" value={fmt((ledger?.pinned_blocks as unknown[] | undefined)?.length || 0)} />
          <Stat icon={CheckCircle2} label="Validation" value={validation.passed === false ? "未通过" : "通过"} />
        </div>

        <div className="rounded-lg border border-pine-200/60 bg-white/50 p-3">
          <label className="text-xs font-medium text-pine-700">压缩 focus</label>
          <textarea
            value={focus}
            onChange={(e) => setFocus(e.target.value)}
            rows={2}
            placeholder="例如：保留前世身份伏笔、书册物件状态、婚约禁揭示"
            className="mt-2 w-full resize-none rounded-lg border border-pine-200 bg-white/50 px-2.5 py-2 text-xs text-pine-700 outline-none focus:border-magic-500/60"
          />
          <button
            onClick={runCompact}
            disabled={compacting || loading}
            className="mt-2 flex w-full items-center justify-center gap-2 rounded-lg bg-white/50 px-3 py-2 text-xs text-pine-700 transition-colors hover:bg-white/50 disabled:opacity-50"
          >
            <Archive className="h-3.5 w-3.5" />
            {compacting ? "压缩中" : "手动压缩"}
          </button>
        </div>

        <div className="rounded-lg border border-pine-200/60 bg-white/50 p-3">
          <div className="mb-2 text-xs font-medium text-pine-700">保护校验</div>
          <div className="space-y-1.5 text-xs">
            {[
              ["P0/P1 哈希", checks.protected_hash_unchanged],
              ["Scene ID 完整", checks.all_scene_ids_present],
              ["禁揭示保留", checks.forbidden_reveals_preserved],
              ["Source 覆盖", checks.source_coverage_ratio],
            ].map(([label, value]) => (
              <div key={String(label)} className="flex items-center justify-between">
                <span className="text-pine-700">{String(label)}</span>
                <span className={value === false ? "text-crimson-300" : "text-emerald-300"}>
                  {typeof value === "number" ? value : value === false ? "失败" : "通过"}
                </span>
              </div>
            ))}
          </div>
        </div>

        <div className="space-y-2">
          <div className="text-xs font-medium text-pine-700">Context Blocks</div>
          {blockGroups.map((block) => (
            <BlockRow key={block.block_id} block={block} />
          ))}
        </div>
      </div>
    </div>
  );
}
