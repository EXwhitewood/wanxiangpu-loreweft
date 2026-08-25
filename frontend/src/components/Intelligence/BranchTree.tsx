import { useCallback, useMemo } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  type Node,
  type Edge,
  type NodeTypes,
  MarkerType,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

interface BranchNodeData {
  label: string;
  chapterNumber: number;
  isMain: boolean;
  isActive: boolean;
  summary?: string;
}

interface BranchTreeProps {
  timelines: Array<{
    id: string;
    name: string;
    parent?: string | null;
    branch_chapter?: number | null;
    is_active?: boolean;
  }>;
  chapters: Array<{ chapter_number: number; title: string }>;
  onSwitchTimeline?: (timelineId: string) => void;
}

function BranchNode({ data }: { data: BranchNodeData }) {
  return (
    <div
      className={`rounded-lg border px-3 py-2 text-xs shadow-md min-w-[120px] max-w-[180px] ${
        data.isActive
          ? "border-magic-500/60 bg-magic-500/10 shadow-magic-500/10"
          : data.isMain
          ? "border-pine-200 bg-white/50"
          : "border-pine-200/60 bg-white/50"
      }`}
    >
      <div className="flex items-center gap-1.5 mb-0.5">
        {data.isActive && (
          <span className="h-1.5 w-1.5 rounded-full bg-magic-400" />
        )}
        <span className={`font-medium ${data.isActive ? "text-magic-400" : "text-pine-700"}`}>
          {data.label}
        </span>
      </div>
      {data.summary && (
        <p className="text-[10px] text-pine-700 truncate">{data.summary}</p>
      )}
    </div>
  );
}

const nodeTypes: NodeTypes = {
  branch: BranchNode,
};

export default function BranchTree({
  timelines,
  chapters,
  onSwitchTimeline,
}: BranchTreeProps) {
  const { nodes, edges } = useMemo(() => {
    const ns: Node[] = [];
    const es: Edge[] = [];

    if (timelines.length === 0 && chapters.length > 0) {
      const mainChapters = chapters.slice(0, 8);
      mainChapters.forEach((ch, i) => {
        ns.push({
          id: `ch-${ch.chapter_number}`,
          type: "branch",
          position: { x: i * 180, y: 0 },
          data: {
            label: `第${ch.chapter_number}章 ${ch.title}`,
            chapterNumber: ch.chapter_number,
            isMain: true,
            isActive: i === mainChapters.length - 1,
          } as Record<string, unknown>,
        });
        if (i > 0) {
          es.push({
            id: `e-${chapters[i - 1].chapter_number}-${ch.chapter_number}`,
            source: `ch-${chapters[i - 1].chapter_number}`,
            target: `ch-${ch.chapter_number}`,
            animated: true,
            style: { stroke: "#f59e0b", strokeWidth: 2 },
            markerEnd: { type: MarkerType.ArrowClosed, color: "#f59e0b" },
          });
        }
      });
      return { nodes: ns, edges: es };
    }

    const mainTimeline = timelines.find((t) => !t.parent);
    const branchTimelines = timelines.filter((t) => t.parent);

    if (mainTimeline) {
      ns.push({
        id: mainTimeline.id,
        type: "branch",
        position: { x: 0, y: 0 },
        data: {
          label: mainTimeline.name || "主线",
          chapterNumber: 0,
          isMain: true,
          isActive: mainTimeline.is_active ?? true,
        } as Record<string, unknown>,
      });
    }

    const mainChCount = Math.min(chapters.length, 6);
    for (let i = 1; i <= mainChCount; i++) {
      const ch = chapters[i - 1];
      ns.push({
        id: `main-ch-${i}`,
        type: "branch",
        position: { x: i * 180, y: 0 },
        data: {
          label: `第${i}章 ${ch?.title || ""}`,
          chapterNumber: i,
          isMain: true,
          isActive: mainTimeline?.is_active ?? (i === mainChCount),
        } as Record<string, unknown>,
      });
      es.push({
        id: `e-main-${i}`,
        source: i === 1 ? (mainTimeline?.id || "main") : `main-ch-${i - 1}`,
        target: `main-ch-${i}`,
        animated: true,
        style: { stroke: "#f59e0b", strokeWidth: 2 },
        markerEnd: { type: MarkerType.ArrowClosed, color: "#f59e0b" },
      });
    }

    branchTimelines.forEach((branch, bi) => {
      const branchCh = branch.branch_chapter || 1;
      const yPos = (bi + 1) * 100;
      ns.push({
        id: branch.id,
        type: "branch",
        position: { x: branchCh * 180, y: yPos },
        data: {
          label: branch.name || `分支${bi + 1}`,
          chapterNumber: branchCh,
          isMain: false,
          isActive: branch.is_active ?? false,
          summary: `从第${branchCh}章分出`,
        } as Record<string, unknown>,
      });

      const sourceId = `main-ch-${branchCh}`;
      const sourceExists = ns.find((n) => n.id === sourceId);
      if (sourceExists) {
        es.push({
          id: `e-branch-${bi}`,
          source: sourceId,
          target: branch.id,
          style: { stroke: "#6366f1", strokeWidth: 1.5, strokeDasharray: "5 3" },
          markerEnd: { type: MarkerType.ArrowClosed, color: "#6366f1" },
        });
      }
    });

    return { nodes: ns, edges: es };
  }, [timelines, chapters]);

  const onNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      if (onSwitchTimeline && node.id !== "main" && !node.id.startsWith("main-ch-") && !node.id.startsWith("ch-")) {
        onSwitchTimeline(node.id);
      }
    },
    [onSwitchTimeline]
  );

  return (
    <div className="h-[400px] w-full rounded-xl border border-pine-200/40 bg-white/50">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodeClick={onNodeClick}
        fitView
        fitViewOptions={{ padding: 0.3 }}
        proOptions={{ hideAttribution: true }}
        style={{ background: "transparent" }}
      >
        <Background color="#1f2937" gap={20} size={1} />
        <Controls
          showInteractive={false}
          style={{
            background: "rgba(15,15,30,0.8)",
            borderColor: "#374151",
            borderRadius: 8,
          }}
        />
        <MiniMap
          nodeColor={(n) => {
            const d = n.data as Record<string, unknown>;
            if (d?.isActive) return "#f59e0b";
            if (d?.isMain) return "#374151";
            return "#4f46e5";
          }}
          style={{
            background: "rgba(15,15,30,0.8)",
            borderColor: "#374151",
            borderRadius: 8,
          }}
        />
      </ReactFlow>
    </div>
  );
}
