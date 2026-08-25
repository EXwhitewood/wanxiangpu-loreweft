import { AlertTriangle, Trash2 } from "lucide-react";
import clsx from "clsx";
import type { SceneBriefItem } from "@/types";

type EditableSceneField = "type" | "goal" | "conflict" | "outcome" | "info_release" | "hook";

interface SceneBriefCardProps {
  scene: SceneBriefItem;
  index: number;
  editing?: boolean;
  expired?: boolean;
  source?: string;
  removable?: boolean;
  onChange?: (field: EditableSceneField, value: string) => void;
  onRemove?: () => void;
}

const TYPE_ACCENT: Record<
  SceneBriefItem["type"],
  { bar: string; dot: string; chip: string; label: string }
> = {
  dialogue: {
    bar: "bg-gradient-to-b from-sky-400 to-blue-500",
    dot: "bg-sky-400",
    chip: "border-sky-400/25 bg-sky-500/10 text-blue-700",
    label: "对话",
  },
  action: {
    bar: "bg-gradient-to-b from-rose-400 to-red-500",
    dot: "bg-rose-400",
    chip: "border-rose-400/25 bg-rose-500/10 text-rose-700",
    label: "行动",
  },
  discovery: {
    bar: "bg-gradient-to-b from-emerald-400 to-green-500",
    dot: "bg-emerald-400",
    chip: "border-emerald-400/25 bg-emerald-500/10 text-emerald-700",
    label: "发现",
  },
  reflection: {
    bar: "bg-gradient-to-b from-violet-400 to-purple-500",
    dot: "bg-violet-400",
    chip: "border-violet-400/25 bg-violet-500/10 text-violet-700",
    label: "反思",
  },
  transition: {
    bar: "bg-gradient-to-b from-pine-300 to-pine-500",
    dot: "bg-pine-400",
    chip: "border-pine-400/25 bg-pine-500/10 text-pine-700",
    label: "过渡",
  },
  development: {
    bar: "bg-gradient-to-b from-magic-300 to-magic-500",
    dot: "bg-magic-400",
    chip: "border-magic-400/25 bg-magic-500/10 text-magic-700",
    label: "推进",
  },
};

const FALLBACK_ACCENT = {
  bar: "bg-gradient-to-b from-monet-300 to-monet-500",
  dot: "bg-monet-400",
  chip: "border-monet-400/25 bg-monet-500/10 text-monet-700",
  label: "未分类",
};

const TYPE_OPTIONS = Object.entries(TYPE_ACCENT) as Array<[
  SceneBriefItem["type"],
  (typeof TYPE_ACCENT)[SceneBriefItem["type"]]
]>;

const FIELDS: Array<{
  field: Exclude<EditableSceneField, "type">;
  label: string;
  wide?: boolean;
}> = [
  { field: "goal", label: "目标" },
  { field: "conflict", label: "冲突" },
  { field: "outcome", label: "结果" },
  { field: "info_release", label: "信息释放", wide: true },
  { field: "hook", label: "钩子", wide: true },
];

export default function SceneBriefCard({
  scene,
  index,
  editing = false,
  expired = false,
  source,
  removable = false,
  onChange,
  onRemove,
}: SceneBriefCardProps) {
  const accent = TYPE_ACCENT[scene.type] || FALLBACK_ACCENT;
  const sceneNumber = String(index + 1).padStart(2, "0");
  const hasKnownType = Boolean(TYPE_ACCENT[scene.type]);

  return (
    <section
      className={clsx(
        "chapter-outline-scene-card group relative overflow-hidden rounded-2xl border",
        expired
          ? "border-dashed border-pine-200/50 opacity-60"
          : "border-white/60 shadow-[0_4px_24px_-8px_rgba(64,89,122,0.15)] transition-shadow duration-200 hover:shadow-[0_8px_28px_-10px_rgba(64,89,122,0.22)]"
      )}
    >
      <div className="chapter-outline-scene-surface monet-card absolute inset-0" />
      <div className={clsx("chapter-outline-scene-accent absolute left-0 top-0 h-full w-1", accent.bar)} />
      <div
        className="chapter-outline-scene-watermark scene-watermark absolute right-4 top-1 z-0 text-[88px]"
        aria-hidden="true"
      >
        {sceneNumber}
      </div>

      <div className="relative z-10 min-w-0 py-4 pl-5 pr-4">
        <div className="mb-3.5 flex items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2.5">
            <span className="chapter-outline-scene-index flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-white/70 text-[10px] font-semibold tabular-nums text-monet-700 ring-1 ring-inset ring-white/80 backdrop-blur-sm">
              {sceneNumber}
            </span>
            <span className="truncate font-mono text-[11px] text-monet-500">
              {scene.scene_id || `scene_${sceneNumber}`}
            </span>
            <label
              className={clsx(
                "chapter-outline-scene-type inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-0.5 text-[10px] font-medium",
                accent.chip
              )}
            >
              <span className={clsx("h-1.5 w-1.5 shrink-0 rounded-full", accent.dot)} />
              {editing ? (
                <select
                  value={hasKnownType ? scene.type : ""}
                  onChange={(event) => onChange?.("type", event.target.value)}
                  aria-label={`场景${index + 1}类型`}
                  className="max-w-20 appearance-none bg-transparent pr-1 text-[10px] font-medium text-inherit outline-none"
                >
                  {!hasKnownType && <option value="">未分类</option>}
                  {TYPE_OPTIONS.map(([value, meta]) => (
                    <option key={value} value={value}>{meta.label}</option>
                  ))}
                </select>
              ) : (
                accent.label
              )}
            </label>
          </div>

          <div className="flex shrink-0 items-center gap-2">
            {expired && (
              <span className="flex items-center gap-1 text-xs text-amber-700">
                <AlertTriangle className="h-3.5 w-3.5" />
                已过期
              </span>
            )}
            {source === "legacy" && (
              <span className="rounded bg-blue-100 px-2 py-0.5 text-[10px] text-blue-700">迁移</span>
            )}
            {source === "user_edited" && (
              <span className="rounded bg-emerald-100 px-2 py-0.5 text-[10px] text-emerald-700">已编辑</span>
            )}
            {removable && (
              <button
                type="button"
                onClick={onRemove}
                title="删除场景"
                className="inline-flex h-7 w-7 items-center justify-center rounded-md text-pine-600 transition-colors hover:bg-red-50 hover:text-red-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/20"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
        </div>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {FIELDS.map(({ field, label, wide }) => (
            <div key={field} className={wide ? "sm:col-span-2" : ""}>
              <label className="chapter-outline-scene-field-label mb-1 flex items-center gap-1 text-[11px] font-medium text-monet-600">
                <span className="h-px w-3 bg-monet-300/60" />
                {label}
              </label>
              {editing ? (
                <textarea
                  value={scene[field] || ""}
                  onChange={(event) => onChange?.(field, event.target.value)}
                  rows={2}
                  aria-label={`场景${index + 1}${label}`}
                  className="chapter-outline-scene-field w-full resize-y rounded-md border border-monet-400/45 bg-white/95 px-3 py-2 text-sm leading-6 text-monet-900 outline-none transition-colors placeholder:text-monet-300 focus:border-pine-600 focus:ring-2 focus:ring-pine-600/15"
                />
              ) : (
                <div className="chapter-outline-scene-field min-h-[58px] rounded-md border border-white/50 bg-white/60 px-3 py-2 text-sm leading-6 text-monet-900">
                  {scene[field] || <span className="text-monet-400">未填写</span>}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
