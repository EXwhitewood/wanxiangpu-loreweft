import { useMemo } from "react";

interface ChapterOutline {
  chapter_number: number;
  title?: string;
  conflict?: string;
  value_shift?: string;
  scenes?: Array<unknown>;
}

interface ForeshadowingItem {
  id: string;
  name: string;
  plant_chapter: number | null;
  reveal_chapter: number | null;
  status: string;
}

export default function OutlineVisualization({
  chapters,
  foreshadowing,
}: {
  chapters: ChapterOutline[];
  foreshadowing: ForeshadowingItem[];
}) {
  const valueCurve = useMemo(() => {
    if (!chapters.length) return [];
    return chapters.map((ch, i) => {
      const shift = ch.value_shift || "";
      let value = 50;
      if (/高潮|巅峰|爆发|顶点/.test(shift)) value = 90;
      else if (/低谷|绝望|崩溃|坠落/.test(shift)) value = 15;
      else if (/上升|好转|突破|成长/.test(shift)) value = 70;
      else if (/下降|挫折|失败|损失/.test(shift)) value = 30;
      else if (/转折|反转|揭示/.test(shift)) value = 60;
      else value = 40 + (i % 2 === 0 ? 10 : -10);
      return { chapter: ch.chapter_number, value, title: ch.title || `第${ch.chapter_number}章` };
    });
  }, [chapters]);

  const svgWidth = Math.max(600, chapters.length * 60);
  const svgHeight = 200;
  const padding = { top: 20, right: 20, bottom: 30, left: 40 };
  const chartWidth = svgWidth - padding.left - padding.right;
  const chartHeight = svgHeight - padding.top - padding.bottom;

  const points = valueCurve.map((p, i) => ({
    x: padding.left + (i / Math.max(1, valueCurve.length - 1)) * chartWidth,
    y: padding.top + (1 - p.value / 100) * chartHeight,
    ...p,
  }));

  const pathD = points
    .map((p, i) => `${i === 0 ? "M" : "L"} ${p.x} ${p.y}`)
    .join(" ");

  const activeForeshadowing = foreshadowing.filter(
    (f) => f.status === "active" && f.plant_chapter != null && f.reveal_chapter != null
  );

  return (
    <div className="space-y-6">
      <div>
        <h4 className="mb-2 text-sm font-medium text-pine-700">价值曲线</h4>
        <div className="overflow-x-auto rounded-lg border border-pine-200/60 bg-white/50 p-4">
          <svg width={svgWidth} height={svgHeight} className="min-w-full">
            <line x1={padding.left} y1={padding.top + chartHeight / 2} x2={svgWidth - padding.right} y2={padding.top + chartHeight / 2} stroke="rgba(255,255,255,0.1)" strokeDasharray="4" />
            {points.map((p, i) => (
              <g key={i}>
                <line x1={p.x} y1={padding.top} x2={p.x} y2={padding.top + chartHeight} stroke="rgba(255,255,255,0.05)" />
                <text x={p.x} y={svgHeight - 5} textAnchor="middle" className="fill-surface-500" fontSize="10">{p.chapter}</text>
              </g>
            ))}
            <path d={pathD} fill="none" stroke="#f59e0b" strokeWidth="2" />
            {points.map((p, i) => (
              <g key={`dot-${i}`}>
                <circle cx={p.x} cy={p.y} r="4" fill="#f59e0b" />
                <title>{p.title}：价值 {p.value}</title>
              </g>
            ))}
          </svg>
        </div>
      </div>

      {activeForeshadowing.length > 0 && (
        <div>
          <h4 className="mb-2 text-sm font-medium text-pine-700">伏笔追踪</h4>
          <div className="overflow-x-auto rounded-lg border border-pine-200/60 bg-white/50 p-4">
            <svg width={svgWidth} height={Math.max(80, activeForeshadowing.length * 30 + 20)}>
              {activeForeshadowing.map((f, i) => {
                const y = 15 + i * 30;
                const startX = padding.left + ((f.plant_chapter! - 1) / Math.max(1, chapters.length)) * chartWidth;
                const endX = padding.left + ((f.reveal_chapter! - 1) / Math.max(1, chapters.length)) * chartWidth;
                return (
                  <g key={f.id}>
                    <line x1={startX} y1={y} x2={endX} y2={y} stroke="#8b5cf6" strokeWidth="2" strokeDasharray={f.status === "resolved" ? "" : "6 3"} />
                    <circle cx={startX} cy={y} r="3" fill="#8b5cf6" />
                    <circle cx={endX} cy={y} r="3" fill={f.status === "resolved" ? "#10b981" : "#8b5cf6"} />
                    <text x={startX + 5} y={y - 5} className="fill-surface-400" fontSize="10">{f.name}</text>
                  </g>
                );
              })}
            </svg>
          </div>
        </div>
      )}
    </div>
  );
}
