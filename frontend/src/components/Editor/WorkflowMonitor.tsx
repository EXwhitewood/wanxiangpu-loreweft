import React, { useEffect, useState, useCallback, useMemo, useRef } from "react";
import { createPortal } from "react-dom";
import { motion, AnimatePresence, type Variants } from "framer-motion";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Diff,
  EyeOff,
  FileText,
  Send,
  Sparkles,
  ShieldCheck,
  Cpu,
  History,
} from "lucide-react";
import {
  listWorkflowExecutions,
  getWorkflowExecution,
  resumeWorkflowReview,
  retryValidator,
  cancelWorkflow,
  chatWorkflowReviewIssue,
  ignoreWorkflowReviewIssue,
  regenerateFromOutline,
  workflowSSEUrl,
  reReviewApi,
  editorTraceApi,
  type WorkflowExecution,
  type WorkflowExecutionDetail,
  type WorkflowStep,
  type WorkflowSSEEvent,
  type WorkflowReview,
  type WorkflowReviewIssue,
} from "@/api/client";
import type { EffectivenessProof } from "@/types/editorV2";
import {
  createHumanWorkbenchPreviewDetail,
  HUMAN_WORKBENCH_PREVIEW_ID,
} from "./humanWorkbenchPreview";

interface Props {
  projectId: string;
  activeExecutionId?: string | null;
  onWorkflowComplete?: (executionId: string) => void;
}

const AGENT_LABELS: Record<string, string> = {
  editor_planning: "主编规划",
  context_compile: "上下文编译",
  chapter_writer: "章节 Writer",
  scene_alignment: "场景对齐",
  scene_preparation: "场景准备",
  core_generation: "场景处理",
  detail_harvest: "细节收割",
  state_update: "状态更新",
  consistency_check: "一致性校验",
  fact_extraction: "事实提取",
  style_polish: "文笔润色",
  write_chapter: "写入章节",
  chapter_review: "章节审查",
  scene_review: "场景审查",
  parallel_review: "并行审查",
  fbi_case_intake: "问题归档",
  fbi_chapter_case_intake: "FBI 章节受理",
  scene_repair: "场景修订",
  parallel_repair: "并行修订",
  scene_recheck: "场景复检",
  parallel_recheck: "并行复检",
  review_case_delta_merge: "修订结果合并",
  ordered_commit: "场景顺序提交",
  state_commit: "状态提交",
  final_acceptance_delta_intake: "最终验收受理",
  final_acceptance_delta_blueprint: "最终修订方案",
  final_acceptance_delta_execute: "最终修订执行",
  final_acceptance_delta_recheck: "最终验收复检",
};

const REVIEW_WAITING_STATUSES = new Set([
  "waiting_review",
  "waiting_human_content_review",
  "waiting_human_system_review",
  "waiting_contract_repair",
  "waiting_alias_decision",
  "waiting_retcon_decision",
]);

const CONTENT_REVIEW_WAITING_STATUSES = new Set([
  "waiting_review",
  "waiting_human_content_review",
  "waiting_contract_repair",
  "waiting_alias_decision",
  "waiting_retcon_decision",
]);

const PROTOCOL_CLASSIFICATION_LABELS: Record<string, string> = {
  contradiction: "事实冲突",
  progression: "状态推进",
  time_jump: "时间推进",
  referenced_time: "时间参照",
  character_claim: "角色主张",
  retcon: "追溯改写",
  alias_needed: "别名决策",
  contract_conflict: "合同冲突",
  advisory_quality: "质量建议",
};

function isReviewWaitingStatus(status?: string): boolean {
  return REVIEW_WAITING_STATUSES.has(String(status || ""));
}

function isContentReviewWaitingStatus(status?: string): boolean {
  return CONTENT_REVIEW_WAITING_STATUSES.has(String(status || ""));
}

function isLiveWorkflowStatus(status?: string): boolean {
  const value = String(status || "");
  return value === "running" || value === "pending_validator_retry" || isReviewWaitingStatus(value);
}

function workflowTitle(triggerType?: string): string {
  const value = String(triggerType || "");
  const chapterMatch = value.match(/^editor_generate_ch(\d+)$/);
  if (chapterMatch) return `第 ${chapterMatch[1]} 章·生成工作流`;
  return value || "章节生成工作流";
}

function protocolClassificationLabel(value?: unknown): string {
  const key = String(value || "");
  return PROTOCOL_CLASSIFICATION_LABELS[key] || key;
}

const ISSUE_TYPE_LABELS: Record<string, string> = {
  forbidden_triggered: "触发禁用内容",
  forbidden_recap_violation: "禁止回顾违规",
  fact_conflict: "事实冲突",
  canon_fact_confusion: "事实冲突",
  canon_state_confusion: "事实冲突",
  clue_provenance_error: "线索来源错误",
  clue_missing_source: "线索缺少来源",
  spatial_consistency_error: "空间一致性错误",
  missing_must_show: "必要信息缺失",
  event_repeated: "事件重复",
  scene_too_short: "场景篇幅过短",
  scene_too_long: "场景篇幅过长",
  ghost_character: "幽灵角色",
  insufficient_sensory_in_opening: "开场感官锚点不足",
  unprovenanced_clue: "未溯源线索",
  timeline_conflict: "时间线冲突",
  timeline_confusion: "时间线冲突",
  canon_timeline_confusion: "时间线冲突",
  canon_time_confusion: "时间线冲突",
  canon_chronology_confusion: "时间线冲突",
  setting_conflict: "设定冲突",
  pov_conflict: "视角冲突",
  identity_conflict: "身份冲突",
  canon_identity_confusion: "身份冲突",
  causal_chain_error: "因果链错误",
  canon_causal_confusion: "因果链错误",
  knowledge_boundary_violation: "认知边界违规",
  canon_knowledge_confusion: "认知边界违规",
  ending_state_not_reached: "结尾状态未达成",
  semantic_quality_error: "语义质量错误",
  info_dump_high_density: "信息倾倒过密",
  info_dump_moderate_density: "信息密度偏高",
  setting_paragraph_too_long: "设定段落过长",
  info_reveal_burst: "信息释放过猛",
  repeated_body_language: "肢体动作重复",
  emotion_expression_monotone: "情绪表达单一",
  possible_style_contract_conflict: "可能存在风格合同冲突",
  style_contract_conflict: "风格合同冲突",
  truth_layer_conflict: "事实层级冲突",
  certainty_escalation: "确定性越级",
  responsibility_polarity_conflict: "责任归属反转",
  spatial_conflict: "空间关系冲突",
  canon_spatial_confusion: "空间关系冲突",
  temporal_conflict: "时间关系冲突",
  temporal_layer_confusion: "时间关系冲突",
  canon_temporal_confusion: "时间关系冲突",
  ownership_conflict: "归属关系冲突",
  forbidden_assertion_triggered: "触发禁止断言",
  required_ambiguity_broken: "必要模糊性被破坏",
  low_reading_drive: "阅读驱动力不足",
  low_scene_pressure: "场景压力不足",
  passive_protagonist: "主角主动性不足",
  conflict_only_explained: "冲突只被解释",
  exposition_driven_reveal: "信息揭示依赖说明",
  missing_dialogue_pressure: "互动压力不足",
  weak_hook_out: "出场钩子偏弱",
  mode_mismatch: "写作模式不匹配",
  specificity_budget_unmet: "具体性预算不足",
  abstraction_over_budget: "抽象解释过量",
  emotional_claim_without_scene_evidence: "情绪缺少场景证据",
  prose_identity_weak: "文体辨识度偏弱",
  over_literary_for_mode: "相对模式过度文学化",
  too_plain_for_literary_mode: "相对文学模式偏平",
  style_experience_conflict: "风格与体验冲突",
  weak_opening_hook: "商业开场钩子偏弱",
  low_event_density: "商业事件密度偏低",
  low_conflict_density: "商业冲突密度偏低",
  flat_pressure_ramp: "商业压力曲线偏平",
  weak_curiosity_engine: "悬念驱动偏弱",
  low_reversal_density: "反转/变局密度偏低",
  missing_micro_payoff: "阶段性兑现不足",
  weak_chapter_end_hook: "章末追读钩子偏弱",
  low_reader_retention: "留读驱动力不足",
  ai_template_phrase: "模板化表达",
  ai_format_artifact: "格式化痕迹",
  ai_dialogue_tag_overuse: "对话标签过量",
  ai_emotion_label: "情绪直接标注",
  ai_technical_register: "技术性叙述",
  ai_explanatory_narration: "解释性叙述",
  ai_formulaic_transition: "公式化转折",
  ai_rhythm_uniformity: "节奏过于均匀",
  ai_punctuation_artifact: "AI 标点停顿痕迹",
  reader_confusion: "读者理解困惑",
  reader_boredom: "读者无聊风险",
  reader_inconsistency: "读者感知矛盾",
  reader_cognitive_load: "读者认知负担偏高",
  reader_experience_advice: "读者体验建议",
  quality_checker_unavailable: "质量检查器暂不可用",
  critic_parse_error: "审校器解析失败",
  consistency_check_unavailable: "一致性检查暂不可用",
  fcip_check_unavailable: "伏笔检查暂不可用",
  scene_contract_compiler_unavailable: "场景合同编译暂不可用",
  proposition_layer_unavailable: "命题层暂不可用",
  proposition_extractor_unavailable: "命题抽取暂不可用",
};

function issueTypeLabel(type?: string): string {
  const raw = String(type || "violation");
  const normalized = raw.startsWith("enforced_") ? raw.slice("enforced_".length) : raw;
  if (ISSUE_TYPE_LABELS[raw]) return ISSUE_TYPE_LABELS[raw];
  if (ISSUE_TYPE_LABELS[normalized]) return ISSUE_TYPE_LABELS[normalized];
  return "未分类审校问题";
}

function fbiStatusLabel(status?: string): string {
  const map: Record<string, string> = {
    resolved: "FBI 已验收",
    partial: "FBI 部分修复",
    needs_human: "FBI 待确认",
    needs_rewrite_approval: "FBI 需授权重写",
    failed: "FBI 修复失败",
  };
  return status ? map[status] || `FBI ${status}` : "FBI 已介入";
}

function getLabel(agentName: string): string {
  const match = agentName.match(/^(.+?)_(\d+)$/);
  if (match) {
    const base = match[1];
    const num = match[2];
    const baseLabel = AGENT_LABELS[base] || base;
    return `${baseLabel} ${num}`;
  }
  return AGENT_LABELS[agentName] || agentName;
}

function formatDuration(ms: number): string {
  if (!ms) return "";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function formatElapsedDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms <= 0) return "0秒";
  const totalSeconds = Math.floor(ms / 1000);
  if (totalSeconds < 60) return `${totalSeconds}秒`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes < 60) return `${minutes}分${seconds > 0 ? `${seconds}秒` : ""}`;
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return `${hours}小时${remainingMinutes > 0 ? `${remainingMinutes}分` : ""}`;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function asNumber(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function compactMetricList(value: unknown): string {
  return Array.isArray(value)
    ? value.map((item) => String(item)).filter(Boolean).slice(0, 3).join(", ")
    : "";
}

function parseWorkflowTimestamp(iso: string): number {
  const value = String(iso || "").trim();
  if (!value) return Number.NaN;
  // 后端序列化的 UTC datetime 是无时区字符串；无后缀时显式补 Z，
  // 否则浏览器会把它当本地时间，导致历时与历史时刻同时偏移。
  const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value);
  return Date.parse(hasTimezone ? value : `${value}Z`);
}

function formatTime(iso: string | null): string {
  if (!iso) return "-";
  return new Date(parseWorkflowTimestamp(iso)).toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function summarizeWorkflowError(message: string): string {
  if (
    message.includes("validation errors for StoryState")
    || message.includes("状态补丁格式不兼容")
  ) {
    return "本章状态没有通过完整性检查，系统已阻止错误内容写入。请重新尝试。";
  }
  const blockingMatch = message.match(/Current text review has\s+(\d+)\s+content blocking issue/i);
  if (blockingMatch) {
    return `正文仍有 ${blockingMatch[1]} 处必须处理的问题，请在审校工作台中确认。`;
  }
  if (message.includes("validator_retry_exhausted") || message.includes("Validator continuous failure")) {
    return "自动审校多次未能稳定完成，需要你确认当前正文后再继续。";
  }
  if (message.includes("Proposition extractor") || message.includes("proposition set")) {
    return "章节命题提取不完整，系统已暂停提交以避免写入缺失信息。";
  }
  if (message.includes("clue_provenance") || message.includes("provenance")) {
    return "线索来源没有通过一致性检查，系统已暂停提交。";
  }
  if (message.includes("dash_per_1000")) {
    return "正文标点密度未通过检查，需要调整后重新审校。";
  }
  if (/^[\u3400-\u9fff\s，。；：、“”‘’！？（）《》【】0-9-]+$/.test(message) && message.length <= 180) {
    return message;
  }
  return "工作流未能完成。请重新尝试；若问题持续出现，可从设置中的缓存目录取得诊断记录。";
}

type PhaseStatus = "pending" | "running" | "recovering" | "waiting" | "completed" | "skipped" | "failed";

interface WorkflowPhaseDefinition {
  id: string;
  title: string;
  description: string;
  matches: (agentName: string) => boolean;
}

interface WorkflowPhase {
  id: string;
  title: string;
  description: string;
  steps: WorkflowStep[];
  status: PhaseStatus;
  durationMs: number;
  completedCount: number;
}

const WORKFLOW_PHASES: WorkflowPhaseDefinition[] = [
  {
    id: "prepare",
    title: "生成准备",
    description: "编译上下文并确定本章生成方案",
    matches: (name) => ["editor_planning", "context_compile"].includes(name),
  },
  {
    id: "draft",
    title: "正文生成",
    description: "生成正文、对齐场景并收集写作结果",
    matches: (name) => /^(chapter_writer|scene_alignment|scene_preparation|core_generation)(?:_\d+)?$/.test(name),
  },
  {
    id: "review",
    title: "质量审查",
    description: "并行检查场景、章节契约与硬正确性",
    matches: (name) => /^(chapter_review|scene_review|parallel_review|consistency_check)(?:_\d+)?$/.test(name),
  },
  {
    id: "intake",
    title: "问题受理",
    description: "归并审查问题并建立可执行的修订边界",
    matches: (name) => name === "fbi_case_intake" || name === "fbi_chapter_case_intake",
  },
  {
    id: "repair",
    title: "自动修订",
    description: "仅在受理边界内修改问题片段",
    matches: (name) => /^(scene_repair|parallel_repair)(?:_\d+)?$/.test(name),
  },
  {
    id: "recheck",
    title: "修订复检",
    description: "验证修订有效且没有引入新问题",
    matches: (name) => /^(scene_recheck|parallel_recheck)(?:_\d+)?$/.test(name),
  },
  {
    id: "merge",
    title: "结果合并",
    description: "合并各场景修订结果与最终候选正文",
    matches: (name) => name === "review_case_delta_merge",
  },
  {
    id: "commit",
    title: "状态提交",
    description: "按场景顺序写入状态、事实和伏笔变化",
    matches: (name) => /^(ordered_commit|state_commit|detail_harvest|state_update|fact_extraction)(?:_\d+)?$/.test(name),
  },
  {
    id: "acceptance",
    title: "最终验收",
    description: "执行提交前的最终差量审查与必要修订",
    matches: (name) => name === "final_acceptance" || name.startsWith("final_acceptance_delta_"),
  },
  {
    id: "publish",
    title: "章节完成",
    description: "整理文风并把最终正文写入章节",
    matches: (name) => name === "style_polish" || name === "write_chapter",
  },
];

const STEP_SETTLED_STATUSES = new Set(["completed", "skipped"]);

const LIFECYCLE_HEAD_STEPS = [
  "editor_planning",
  "context_compile",
  "chapter_writer",
  "scene_alignment",
] as const;

const LIFECYCLE_TAIL_STEPS = [
  "fact_extraction",
  "style_polish",
  "final_acceptance",
  "write_chapter",
] as const;

function materializeLifecycleSteps(
  steps: WorkflowStep[],
  executionStatus: string,
  candidateReady: boolean,
): WorkflowStep[] {
  const result = [...steps];
  const names = new Set(result.map((step) => step.agent_name));
  const executionComplete = executionStatus === "completed";
  const headComplete = executionComplete || candidateReady;

  LIFECYCLE_HEAD_STEPS.forEach((agentName, index) => {
    if (names.has(agentName)) return;
    const status = headComplete
      ? "completed"
      : index === 0 && executionStatus === "running"
        ? "running"
        : "pending";
    result.push({
      id: `synthetic-head-${agentName}`,
      agent_name: agentName,
      layer: -1,
      status,
      duration_ms: 0,
      error_message: "",
      started_at: null,
      completed_at: headComplete ? new Date(0).toISOString() : null,
      output_snapshot: { synthetic_lifecycle: true },
    });
  });

  LIFECYCLE_TAIL_STEPS.forEach((agentName) => {
    if (names.has(agentName)) return;
    result.push({
      id: `synthetic-tail-${agentName}`,
      agent_name: agentName,
      layer: Number.MAX_SAFE_INTEGER,
      status: executionComplete ? "completed" : "pending",
      duration_ms: 0,
      error_message: "",
      started_at: null,
      completed_at: executionComplete ? new Date(0).toISOString() : null,
      output_snapshot: { synthetic_lifecycle: true },
    });
  });

  return result;
}

function phaseStatus(steps: WorkflowStep[]): PhaseStatus {
  const statuses = steps.map((step) => String(step.status || "pending"));
  if (statuses.some((status) => status === "failed" || status === "interrupted" || status === "cancelled")) return "failed";
  if (statuses.some((status) => status.includes("waiting"))) return "waiting";
  if (statuses.some((status) => status === "pending_validator_retry")) return "recovering";
  if (statuses.some((status) => status === "running")) return "running";
  if (statuses.length > 0 && statuses.every((status) => status === "skipped")) return "skipped";
  if (statuses.length > 0 && statuses.every((status) => STEP_SETTLED_STATUSES.has(status))) return "completed";
  return "pending";
}

function buildWorkflowPhases(steps: WorkflowStep[]): WorkflowPhase[] {
  const buckets = new Map<string, WorkflowStep[]>();
  const unknown: WorkflowStep[] = [];

  for (const step of steps) {
    const definition = WORKFLOW_PHASES.find((phase) => phase.matches(step.agent_name));
    if (!definition) {
      unknown.push(step);
      continue;
    }
    const bucket = buckets.get(definition.id) || [];
    bucket.push(step);
    buckets.set(definition.id, bucket);
  }

  const phases = WORKFLOW_PHASES.flatMap((definition) => {
    const phaseSteps = buckets.get(definition.id) || [];
    if (!phaseSteps.length) return [];
    return [{
      id: definition.id,
      title: definition.title,
      description: definition.description,
      steps: phaseSteps,
      status: phaseStatus(phaseSteps),
      durationMs: phaseSteps.reduce((sum, step) => sum + (step.duration_ms || 0), 0),
      completedCount: phaseSteps.filter((step) => STEP_SETTLED_STATUSES.has(step.status)).length,
    } satisfies WorkflowPhase];
  });

  if (unknown.length) {
    phases.push({
      id: "other",
      title: "其他处理",
      description: "执行工作流扩展节点",
      steps: unknown,
      status: phaseStatus(unknown),
      durationMs: unknown.reduce((sum, step) => sum + (step.duration_ms || 0), 0),
      completedCount: unknown.filter((step) => STEP_SETTLED_STATUSES.has(step.status)).length,
    });
  }
  return phases;
}

const PHASE_STATUS_LABELS: Record<PhaseStatus, string> = {
  pending: "等待中",
  running: "执行中",
  recovering: "审校器恢复中",
  waiting: "需要确认",
  completed: "已完成",
  skipped: "无需执行",
  failed: "执行失败",
};

function PhaseHistoryListItem({ phase }: { phase: WorkflowPhase }) {
  return (
    <div className="workflow-history__item flex items-center justify-between border-b border-pine-200/20 px-2 py-1.5 text-xs text-pine-700/65 last:border-0">
      <div className="flex min-w-0 items-center gap-2">
        {phase.status === "skipped" ? (
          <span className="workflow-history__icon flex h-3.5 w-3.5 items-center justify-center rounded-full border border-slate-300 text-[8px] text-slate-400">–</span>
        ) : (
          <CheckCircle2 className="workflow-history__icon h-3.5 w-3.5 shrink-0 text-emerald-500/80" />
        )}
        <span className="workflow-history__title truncate font-medium">{phase.title}</span>
        <span className="workflow-history__count shrink-0 text-[10px] text-pine-500/55">
          {phase.completedCount}/{phase.steps.length}
        </span>
      </div>
      {phase.durationMs > 0 && <span className="workflow-history__duration shrink-0 pl-2 font-mono text-[10px]">{formatDuration(phase.durationMs)}</span>}
    </div>
  );
}

interface ReviewIssueLocation {
  issueId: string;
  start: number;
  end: number;
  blocksCommit: boolean;
}

interface ReviewLocationSegment {
  text: string;
  issueIds: string[];
  tone: "blocking" | "advisory" | null;
}

function reviewIssueIdentity(issue: WorkflowReviewIssue, index: number): string {
  return issue.issue_id || issue.violation_id || `issue-${index}`;
}

function buildReviewIssueLocationView(
  text: string,
  issues: WorkflowReviewIssue[],
): {
  segments: ReviewLocationSegment[];
  locations: Record<string, ReviewIssueLocation>;
  unmatchedIssueIds: Set<string>;
} {
  const locations: Record<string, ReviewIssueLocation> = {};
  const unmatchedIssueIds = new Set<string>();

  issues.forEach((issue, index) => {
    const issueId = reviewIssueIdentity(issue, index);
    const candidates = [issue.target_span, issue.evidence_span]
      .filter((value): value is string => typeof value === "string")
      .map((value) => value.trim())
      .filter(Boolean);
    const uniqueCandidates = [...new Set(candidates)];
    let matched: ReviewIssueLocation | null = null;

    for (const candidate of uniqueCandidates) {
      const start = text.indexOf(candidate);
      if (start >= 0) {
        matched = {
          issueId,
          start,
          end: start + candidate.length,
          blocksCommit: Boolean(issue.blocks_commit),
        };
        break;
      }
    }

    if (matched) locations[issueId] = matched;
    else unmatchedIssueIds.add(issueId);
  });

  const locationList = Object.values(locations);
  const boundaries = new Set<number>([0, text.length]);
  locationList.forEach((location) => {
    boundaries.add(location.start);
    boundaries.add(location.end);
  });
  const sortedBoundaries = [...boundaries].sort((left, right) => left - right);
  const segments: ReviewLocationSegment[] = [];

  for (let index = 0; index < sortedBoundaries.length - 1; index += 1) {
    const start = sortedBoundaries[index];
    const end = sortedBoundaries[index + 1];
    if (end <= start) continue;
    const activeLocations = locationList.filter(
      (location) => location.start < end && location.end > start,
    );
    segments.push({
      text: text.slice(start, end),
      issueIds: activeLocations.map((location) => location.issueId),
      tone: activeLocations.length === 0
        ? null
        : activeLocations.some((location) => location.blocksCommit)
          ? "blocking"
          : "advisory",
    });
  }

  return { segments, locations, unmatchedIssueIds };
}

function buildSingleIssueLocationSegments(
  text: string,
  location?: ReviewIssueLocation,
): ReviewLocationSegment[] {
  if (!text) return [];
  if (!location) return [{ text, issueIds: [], tone: null }];

  const segments: ReviewLocationSegment[] = [];
  if (location.start > 0) {
    segments.push({ text: text.slice(0, location.start), issueIds: [], tone: null });
  }
  segments.push({
    text: text.slice(location.start, location.end),
    issueIds: [location.issueId],
    tone: location.blocksCommit ? "blocking" : "advisory",
  });
  if (location.end < text.length) {
    segments.push({ text: text.slice(location.end), issueIds: [], tone: null });
  }
  return segments;
}

function WriterTimingPanel({ step }: { step: WorkflowStep }) {
  const output = asRecord(step.output_snapshot);
  const orchestration = asRecord(output.orchestration_timing_ms);
  const promptDebug = asRecord(output.prompt_debug);
  const timing = asRecord(promptDebug.timing_ms);
  const events = Array.isArray(promptDebug.timing_events)
    ? promptDebug.timing_events.map(asRecord)
    : [];
  const repairEvent = events.find((event) => event.phase === "skill_repair_dispatch") || {};
  const attempts = Array.isArray(repairEvent.attempts)
    ? repairEvent.attempts.map(asRecord)
    : [];

  const rows = [
    ["packet", asNumber(orchestration.packet_build_ms)],
    ["persona", asNumber(orchestration.persona_load_ms)],
    ["agent", asNumber(orchestration.agent_execute_ms)],
    ["initial LLM", asNumber(timing.llm_initial_generate)],
    ["marker retry", asNumber(timing.llm_marker_retry_generate)],
    ["skill validate", asNumber(timing.skill_validate_only_total)],
    ["skill repair", asNumber(timing.skill_repair_dispatch)],
    ["total", asNumber(orchestration.step_total_ms) || asNumber(timing.chapter_writer_total) || step.duration_ms],
  ].filter(([, value]) => Number(value) > 0) as [string, number][];

  if (!rows.length && !attempts.length) return null;

  return (
    <div className="workflow-timing mt-4 border-t border-[#D1DFE8]/70 pt-3">
      <div className="workflow-timing__title mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
        <Cpu className="h-3.5 w-3.5" />
        Writer timing
      </div>
      <div className="workflow-timing__grid grid grid-cols-2 gap-x-3 gap-y-1.5 text-[11px] text-slate-600">
        {rows.map(([label, value]) => (
          <div key={label} className="flex items-center justify-between gap-2">
            <span className="truncate">{label}</span>
            <span className="workflow-timing__value font-mono text-slate-800">{formatDuration(value)}</span>
          </div>
        ))}
      </div>
      {attempts.length > 0 && (
        <div className="workflow-timing__events mt-2 space-y-1 border-t border-[#D1DFE8]/50 pt-2 text-[11px] text-slate-600">
          {attempts.slice(0, 4).map((attempt, index) => (
            <div key={`${attempt.hook || "attempt"}-${index}`} className="flex items-center justify-between gap-2">
              <span className="min-w-0 truncate">
                {String(attempt.hook || "repair")} · {String(attempt.status || "-")}
                {compactMetricList(attempt.metrics) ? ` · ${compactMetricList(attempt.metrics)}` : ""}
              </span>
              <span className="workflow-timing__value shrink-0 font-mono text-slate-800">
                {formatDuration(asNumber(attempt.duration_ms))}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function StepPhaseTimingPanel({ step }: { step: WorkflowStep }) {
  const output = asRecord(step.output_snapshot);
  const phaseTiming = asRecord(output.phase_timing_ms);
  const events = Array.isArray(output.phase_timing_events)
    ? output.phase_timing_events.map(asRecord)
    : [];
  const rows = Object.entries(phaseTiming)
    .map(([label, value]) => [label, asNumber(value)] as [string, number])
    .filter(([, value]) => value > 0)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8);

  if (!rows.length && !events.length) return null;

  return (
    <div className="workflow-timing mt-4 border-t border-[#D1DFE8]/70 pt-3">
      <div className="workflow-timing__title mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
        <Cpu className="h-3.5 w-3.5" />
        Step phases
      </div>
      {rows.length > 0 && (
        <div className="workflow-timing__grid grid grid-cols-2 gap-x-3 gap-y-1.5 text-[11px] text-slate-600">
          {rows.map(([label, value]) => (
            <div key={label} className="flex items-center justify-between gap-2">
              <span className="truncate">{label}</span>
              <span className="workflow-timing__value font-mono text-slate-800">{formatDuration(value)}</span>
            </div>
          ))}
        </div>
      )}
      {events.length > 0 && (
        <div className="workflow-timing__events mt-2 space-y-1 border-t border-[#D1DFE8]/50 pt-2 text-[11px] text-slate-600">
          {events.slice(-4).map((event, index) => (
            <div key={`${event.phase || "phase"}-${index}`} className="flex items-center justify-between gap-2">
              <span className="min-w-0 truncate">{String(event.phase || "phase")}</span>
              <span className="workflow-timing__value shrink-0 font-mono text-slate-800">
                {formatDuration(asNumber(event.duration_ms))}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const DeckCard = React.forwardRef<HTMLDivElement, {
  phase: WorkflowPhase;
  isActive: boolean; 
  indexOffset: number; 
  isHovered: boolean;
}>(({ 
  phase,
  isActive, 
  indexOffset, 
  isHovered 
  }, _ref) => {
  const title = phase.title;
  const status = phase.status;
  const isRunning = status === "running" || status === "recovering" || status === "waiting";
  const activeStep = phase.steps.find((step) => (
    step.status === "running"
    || step.status === "pending_validator_retry"
    || step.status.includes("waiting")
  )) || phase.steps.find((step) => step.status === "pending") || phase.steps[phase.steps.length - 1];
  const phaseProgress = phase.steps.length > 0
    ? Math.round((phase.completedCount / phase.steps.length) * 100)
    : 0;

  // Pure vertical stacking with scale to prevent x-overflow issues
  let yOffset = indexOffset * 16;
  let scale = 1 - indexOffset * 0.04;
  
  if (isHovered && !isActive) {
    yOffset = indexOffset * 55;
    scale = 1;
  }

  const cardVariants: Variants = {
    initial: { opacity: 0, y: yOffset + 50, scale: 0.95 },
    animate: { 
      opacity: Math.max(0, 1 - indexOffset * 0.2), 
      y: yOffset, 
      scale: scale,
      zIndex: 50 - indexOffset
    },
    exit: { 
      opacity: 0, 
      x: 150, 
      transition: { duration: 0.4, ease: "easeIn" }
    }
  };

  return (
    <motion.div
      layout
      variants={cardVariants}
      initial="initial"
      animate="animate"
      exit="exit"
      transition={{ type: "spring", stiffness: 300, damping: 25 }}
      data-phase-status={status}
      className={`workflow-deck-card absolute top-0 w-full overflow-hidden rounded-xl border transition-shadow ${
        isActive 
          ? "workflow-deck-card--active border-[#D1DFE8] bg-gradient-to-br from-[#F4F7F9] to-[#E6EDF2] shadow-[0_12px_32px_-8px_rgba(30,41,59,0.15),0_0_0_1px_rgba(255,255,255,0.8)]"
          : "workflow-deck-card--queued border-[#D1DFE8]/60 bg-[#F4F7F9]/90 shadow-[0_8px_16px_-6px_rgba(30,41,59,0.08)]"
      }`}
      style={{ transformOrigin: "top center" }}
    >
      {/* 游离光晕与星轨暗纹 (Ethereal Aura & Astrolabe Accent) */}
      {/* 右上角的冷色星云光晕 */}
      <div className="workflow-aura pointer-events-none absolute -right-16 -top-16 h-48 w-48 rounded-full bg-[#94A3B8] opacity-[0.25] blur-3xl mix-blend-multiply transition-opacity duration-700" />
      {/* 左下角的暖色能量光晕 */}
      <div className="workflow-aura pointer-events-none absolute -bottom-20 -left-10 h-40 w-40 rounded-full bg-[#E2E8F0] opacity-[0.6] blur-2xl transition-opacity duration-700" />
      
      {/* 远景星空与游离小行星 (Drifting & Twinkling Starry Sky) - 使用 pattern 防止拉伸，确保完美正圆 */}
      <div className="workflow-card-cosmos pointer-events-none absolute inset-0 overflow-hidden opacity-80">
        <svg className="w-full h-full" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <pattern id="star-pattern" x="0" y="0" width="250" height="250" patternUnits="userSpaceOnUse">
              {/* 极缓慢的无缝平移 */}
              <animateTransform attributeName="patternTransform" type="translate" from="0 0" to="-250 -125" dur="180s" repeatCount="indefinite" />
              
              {/* 绝对像素级别的星辰，永远保持完美的正圆 */}
              <circle cx="25" cy="50" r="1.2" fill="#94A3B8">
                <animate attributeName="opacity" values="0.1;1;0.1" dur="3s" repeatCount="indefinite" />
              </circle>
              <circle cx="110" cy="30" r="1.5" fill="#64748B">
                <animate attributeName="opacity" values="0.3;0.9;0.3" dur="5s" repeatCount="indefinite" />
              </circle>
              <circle cx="210" cy="35" r="2" fill="#CBD5E1" />
              <circle cx="230" cy="160" r="1" fill="#94A3B8">
                <animate attributeName="opacity" values="0;1;0" dur="4s" repeatCount="indefinite" />
              </circle>
              <circle cx="45" cy="205" r="1.5" fill="#64748B" />
              <circle cx="180" cy="210" r="1.8" fill="#94A3B8">
                <animate attributeName="opacity" values="0.2;1;0.2" dur="6s" repeatCount="indefinite" />
              </circle>
              <circle cx="85" cy="125" r="1.2" fill="#CBD5E1" />
              <circle cx="150" cy="60" r="1.5" fill="#64748B">
                <animate attributeName="opacity" values="0.1;0.8;0.1" dur="2s" repeatCount="indefinite" />
              </circle>
              <circle cx="200" cy="200" r="2" fill="#CBD5E1" />
              
              {/* 背景深处的模糊小行星 */}
              <circle cx="185" cy="75" r="6" fill="#64748B" opacity="0.15" />
              <circle cx="50" cy="150" r="8" fill="#94A3B8" opacity="0.12" />
              <circle cx="135" cy="185" r="4" fill="#CBD5E1" opacity="0.15" />
            </pattern>
          </defs>
          <rect width="100%" height="100%" fill="url(#star-pattern)" />
        </svg>
      </div>

      {/* 3D 小行星轨道 (SVG Orbits) - 完美圆形不拉伸，环绕状态灯 */}
      {isActive && (
        <div className="workflow-card-orbits pointer-events-none absolute -left-12 -top-12 h-48 w-48 opacity-[0.6] text-slate-500">
          <svg viewBox="-100 -100 200 200" className="w-full h-full overflow-visible" fill="none" stroke="currentColor">
            
            {/* 轨道 1 (极大放缓公转) */}
            <g transform="rotate(20)">
              <ellipse cx="0" cy="0" rx="80" ry="25" strokeWidth="0.6" opacity="0.3" strokeDasharray="2 4" />
              <circle r="4" fill="#64748B" stroke="none" style={{ filter: 'drop-shadow(0 0 3px rgba(100,116,139,0.8))' }}>
                <animateMotion dur="45s" repeatCount="indefinite" path="M 80,0 A 80,25 0 1,1 -80,0 A 80,25 0 1,1 80,0" />
              </circle>
            </g>

            {/* 轨道 2 (极大放缓公转) */}
            <g transform="rotate(-40)">
              <ellipse cx="0" cy="0" rx="60" ry="18" strokeWidth="0.4" opacity="0.2" strokeDasharray="4 4" />
              <circle r="3" fill="#94A3B8" stroke="none" style={{ filter: 'drop-shadow(0 0 2px rgba(148,163,184,0.8))' }}>
                <animateMotion dur="35s" repeatCount="indefinite" path="M 60,0 A 60,18 0 1,1 -60,0 A 60,18 0 1,1 60,0" />
              </circle>
            </g>

            {/* 轨道 3 (极大放缓公转) */}
            <g transform="rotate(65)">
              <ellipse cx="0" cy="0" rx="95" ry="30" strokeWidth="0.8" opacity="0.15" />
              <circle r="5" fill="#CBD5E1" stroke="none" style={{ filter: 'drop-shadow(0 0 4px rgba(203,213,225,0.8))' }}>
                <animateMotion dur="60s" repeatCount="indefinite" path="M 95,0 A 95,30 0 1,1 -95,0 A 95,30 0 1,1 95,0" />
              </circle>
            </g>

          </svg>
        </div>
      )}

      {/* 静态星轨曲线 (Subtle Orbital Curve) */}
      <svg 
        className="workflow-card-tracks pointer-events-none absolute right-0 top-0 h-full w-2/3 opacity-[0.15] text-slate-500 transition-opacity duration-500"
        viewBox="0 0 100 100" 
        preserveAspectRatio="none"
      >
         <path d="M 30,0 C 70,30 90,70 100,100" fill="none" stroke="currentColor" strokeWidth="0.8" />
         <path d="M 50,0 C 80,40 100,80 100,100" fill="none" stroke="currentColor" strokeWidth="0.4" />
         <circle cx="80" cy="40" r="2" fill="currentColor" />
         <circle cx="60" cy="70" r="1" fill="currentColor" />
         <circle cx="90" cy="85" r="0.8" fill="currentColor" />
      </svg>

      {/* Hide inner content when not active or hovered to prevent the messy text overlap */}
      <div className={`workflow-card-content relative z-10 p-5 transition-opacity duration-300 ${isActive || isHovered ? "opacity-100" : "opacity-0"}`}>
        <div className="workflow-phase-header flex items-center justify-between">
          <div className="flex items-center gap-3">
            {/* Elegant Status Orb */}
            <div className="workflow-phase-orb relative flex h-8 w-8 items-center justify-center rounded-full bg-white shadow-sm ring-1 ring-slate-900/5">
              <div 
                className={`workflow-phase-orb__dot h-2.5 w-2.5 rounded-full ${status === "recovering" ? "bg-[#75677F] animate-pulse" : isRunning ? "bg-[#AD8B52] animate-pulse" : status === "completed" ? "bg-[#4F7A68]" : status === "failed" ? "bg-[#A45B5B]" : "bg-slate-300"}`}
              />
            </div>
            
            <div className="flex flex-col">
              <span className="workflow-phase-title text-[17px] font-bold tracking-tight text-slate-800">{title}</span>
              {isActive && (
                <span className="workflow-phase-description mt-0.5 text-xs font-medium text-slate-500">
                  {phase.description}
                </span>
              )}
            </div>
          </div>
          
          {/* Status Badge */}
          <div className="flex flex-col items-end gap-0.5">
            <span className="workflow-phase-status text-[11px] font-bold uppercase tracking-widest text-slate-500/70">
              {PHASE_STATUS_LABELS[status]}
            </span>
            {isActive && (
              <span className="workflow-phase-count font-mono text-[10px] text-slate-400">
                {phase.completedCount}/{phase.steps.length} 节点
              </span>
            )}
          </div>
        </div>

        {isActive && (
          <div className="workflow-phase-divider mt-5 border-t border-[#D1DFE8]/65 pt-4">
            <div className="workflow-phase-progress relative mb-4 h-3" aria-label={`阶段进度 ${Math.round(phaseProgress)}%`}>
              <div className="workflow-phase-progress__track absolute left-0 right-0 top-1.5 h-px bg-slate-700/10" />
              <motion.div
                className={`workflow-phase-progress__value absolute left-0 top-1.5 h-px ${status === "failed" ? "bg-[#A45B5B]" : status === "recovering" ? "bg-[#75677F]" : isRunning ? "bg-[#AD8B52]" : "bg-[#4F7A68]"}`}
                initial={{ width: 0 }}
                animate={{ width: `${status === "completed" || status === "skipped" ? 100 : phaseProgress}%` }}
                transition={{ duration: 0.45, ease: "easeOut" }}
              />
              <motion.span
                className={`workflow-phase-progress__marker absolute top-[3px] h-1.5 w-1.5 -translate-x-1/2 rounded-full border border-white/90 ${status === "failed" ? "bg-[#A45B5B]" : status === "recovering" ? "bg-[#75677F]" : isRunning ? "bg-[#AD8B52]" : "bg-[#4F7A68]"}`}
                initial={{ left: 0 }}
                animate={{ left: `${status === "completed" || status === "skipped" ? 100 : phaseProgress}%` }}
                transition={{ duration: 0.45, ease: "easeOut" }}
              />
            </div>
            <div className="workflow-phase-steps grid grid-cols-2 gap-x-3 gap-y-2">
              {phase.steps.slice(0, 8).map((step) => {
                const settled = STEP_SETTLED_STATUSES.has(step.status);
                const stepRunning = step.status === "running" || step.status === "pending_validator_retry" || step.status.includes("waiting");
                return (
                  <div key={step.id} data-step-status={step.status} className="workflow-phase-step flex min-w-0 items-center gap-2 text-[11px]">
                    <span className={`workflow-phase-step__dot h-1.5 w-1.5 shrink-0 rounded-full ${step.status === "failed" ? "bg-[#A45B5B]" : step.status === "pending_validator_retry" ? "bg-[#75677F] animate-pulse" : stepRunning ? "bg-[#AD8B52] animate-pulse" : settled ? "bg-[#4F7A68]" : "bg-slate-300"}`} />
                    <span className={`workflow-phase-step__name min-w-0 flex-1 truncate ${stepRunning ? "font-semibold text-slate-700" : "text-slate-500"}`}>
                      {getLabel(step.agent_name)}
                    </span>
                    {step.duration_ms > 0 && (
                      <span className="workflow-phase-step__duration shrink-0 font-mono text-[9px] text-slate-400">{formatDuration(step.duration_ms)}</span>
                    )}
                  </div>
                );
              })}
            </div>
            {phase.steps.length > 8 && (
              <p className="workflow-phase-more mt-2 text-right text-[10px] text-slate-400">还有 {phase.steps.length - 8} 个节点</p>
            )}
            {activeStep?.agent_name === "chapter_writer" ? (
              <WriterTimingPanel step={activeStep} />
            ) : activeStep ? (
              <StepPhaseTimingPanel step={activeStep} />
            ) : null}
          </div>
        )}
      </div>
      
      {/* Edge label for non-active cards, rendered outside the fading div so it remains visible */}
      {!isActive && (
        <div className={`workflow-card-edge-label absolute bottom-2.5 left-0 right-0 text-center opacity-60 transition-opacity ${isHovered ? "workflow-card-edge-label--hovered" : ""}`}>
           <span className="workflow-queued-indicator hidden" />
           <span className="workflow-queued-title text-[10px] font-bold uppercase tracking-widest text-slate-700">{title}</span>
           <span className="workflow-queued-status hidden">{PHASE_STATUS_LABELS[status]}</span>
        </div>
      )}
    </motion.div>
  );
});

function DeckTimeline({
  steps,
  stepStatusOverrides,
  executionStatus,
}: {
  steps: WorkflowStep[];
  stepStatusOverrides: Record<string, string>;
  executionStatus: string;
}) {
  const [isHovered, setIsHovered] = useState(false);

  const effectiveSteps = useMemo(() => steps.map((s) => ({
    ...s,
    status: stepStatusOverrides[s.agent_name] || s.status,
  })), [steps, stepStatusOverrides]);
  const phases = useMemo(() => buildWorkflowPhases(effectiveSteps), [effectiveSteps]);

  const isExecutionComplete = executionStatus === "completed";
  let activeIndex = isExecutionComplete
    ? phases.length
    : phases.findIndex((phase) => !["completed", "skipped"].includes(phase.status));
  if (activeIndex === -1) activeIndex = phases.length;

  const historyPhases = phases.slice(0, activeIndex);
  const deckPhases = phases.slice(activeIndex);
  
  // 保留原设计的叠放卡组，但每张卡代表真实阶段而非硬编码旧节点。
  const visibleDeckPhases = deckPhases.slice(0, 5);
  const hiddenCount = deckPhases.length - visibleDeckPhases.length;

  return (
    <div className="workflow-timeline flex h-full flex-col gap-5 px-5 py-5">
      {/* Keep the completed path above the live card so the workflow never
          appears to begin halfway through its lifecycle. */}
      {historyPhases.length > 0 && (
        <div className="workflow-history rounded-xl border border-[#D9D6CC]/70 bg-[#F7F5EF]/70 p-3 shadow-[0_5px_18px_-14px_rgba(54,67,62,0.35)]">
          <div className="mb-2 flex items-center justify-between px-1">
            <div className="flex items-center gap-2">
              <div className="workflow-history__marker h-1.5 w-1.5 rounded-full bg-[#668273]" />
              <h4 className="workflow-history__heading text-[10px] font-semibold tracking-[0.18em] text-slate-500">已走过</h4>
            </div>
            <span className="workflow-history__summary font-mono text-[9px] text-slate-400">{historyPhases.length}/{phases.length}</span>
          </div>
          <div className="max-h-32 overflow-y-auto pr-1 custom-scrollbar">
            {historyPhases.map((phase) => <PhaseHistoryListItem key={phase.id} phase={phase} />)}
          </div>
        </div>
      )}

      {/* Deck Area */}
      {visibleDeckPhases.length > 0 && (
        <div 
          className="workflow-deck relative min-h-[340px]"
          onMouseEnter={() => setIsHovered(true)}
          onMouseLeave={() => setIsHovered(false)}
        >
          <AnimatePresence mode="popLayout">
            {visibleDeckPhases.map((phase, idx) => (
              <DeckCard 
                key={phase.id}
                phase={phase}
                isActive={idx === 0} 
                indexOffset={idx} 
                isHovered={isHovered}
              />
            ))}
          </AnimatePresence>
          
          {hiddenCount > 0 && (
            <div 
              className="workflow-hidden-count-wrap absolute left-0 right-0 text-center transition-all duration-300"
              style={{ 
                bottom: isHovered ? -((visibleDeckPhases.length - 1) * 55 + 18) : -((visibleDeckPhases.length - 1) * 16 + 18),
                zIndex: 10
              }}
            >
              <span className="workflow-hidden-count text-[10px] text-slate-500 font-bold bg-[#F4F7F9]/80 backdrop-blur-sm px-3 py-1 rounded-full border border-[#D1DFE8]/60 shadow-sm">
                还有 {hiddenCount} 个待执行阶段
              </span>
            </div>
          )}
        </div>
      )}

      {visibleDeckPhases.length === 0 && phases.length > 0 && (
        <div className="workflow-complete relative overflow-hidden rounded-2xl border border-emerald-500/20 bg-gradient-to-br from-[#F4F7F9] via-[#EEF4F2] to-[#E4EFEB] px-5 py-6 shadow-[0_14px_36px_-18px_rgba(30,77,65,0.28)]">
          <div className="workflow-aura pointer-events-none absolute -right-10 -top-12 h-40 w-40 rounded-full bg-emerald-300/20 blur-3xl" />
          <div className="relative flex items-center gap-4">
            <div className="workflow-complete-icon flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-white/80 shadow-sm ring-1 ring-emerald-500/15">
              <ShieldCheck className="h-5 w-5 text-emerald-600" />
            </div>
            <div className="min-w-0">
              <p className="text-base font-semibold tracking-tight text-slate-800">工作流已完成</p>
              <p className="mt-1 text-xs leading-relaxed text-slate-500">
                {phases.length} 个阶段均已处理，正文与状态提交已收口。
              </p>
            </div>
            <span className="ml-auto font-mono text-sm font-semibold text-emerald-700">100%</span>
          </div>
        </div>
      )}

      {phases.length === 0 && (
        <div className="workflow-empty flex flex-col items-center justify-center py-14 text-center text-pine-700/55">
          <div className="mb-3 h-8 w-8 rounded-full border border-pine-200/70 bg-white/50" />
          <p className="text-sm">正在等待首个执行阶段</p>
        </div>
      )}
    </div>
  );
}

export default function WorkflowMonitor({ projectId, activeExecutionId, onWorkflowComplete }: Props) {
  const humanWorkbenchPreview = import.meta.env.DEV
    && new URLSearchParams(window.location.search).get("human-workbench-preview") === "1";
  const [executions, setExecutions] = useState<WorkflowExecution[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // 自动选中 ref：loadExecutions 轮询时若发现当前无选中 execution，自动选中最新的。
  // 覆盖通过 API/后端直接启动工作流（而非工作台生成按钮回调）的场景，
  // 确保 waiting_review 工作台弹窗能正常触发。用 ref 避免 loadExecutions 依赖变化导致 interval 重建。
  const selectedIdRef = useRef<string | null>(null);
  selectedIdRef.current = selectedId;
  const manualSelectionRef = useRef(false);
  const activeExecutionIdRef = useRef<string | null>(null);
  activeExecutionIdRef.current = activeExecutionId ?? null;
  const [detail, setDetail] = useState<WorkflowExecutionDetail | null>(null);
  const [stepStatusOverrides, setStepStatusOverrides] = useState<Record<string, string>>({});
  // 方案16：DAG 层级事件进度（值待后续 UI 渲染任务消费，当前仅消费 SSE 事件维护状态）
  const [, setDagLayerProgress] = useState<{
    currentLayer: number;
    totalLayers: number;
    layerNodes: string[];
    executedNodes: string[];
    skippedNodes: Array<{ node_id: string; reason: string }>;
    failedNodes: Array<{ node_id: string; error: string; error_type?: string }>;
    layerStatus: "idle" | "running" | "complete" | "failed";
    abortReason?: string;
  }>({
    currentLayer: 0,
    totalLayers: 0,
    layerNodes: [],
    executedNodes: [],
    skippedNodes: [],
    failedNodes: [],
    layerStatus: "idle",
  });
  const [, setContainerWidth] = useState(340);
  const [reviewText, setReviewText] = useState("");
  const [reviewSubmitting, setReviewSubmitting] = useState(false);
  const [reviewError, setReviewError] = useState("");
  const [expandedIssueId, setExpandedIssueId] = useState<string | null>(null);
  const [issueMessages, setIssueMessages] = useState<Record<string, string>>({});
  const [issueSubmitting, setIssueSubmitting] = useState<string | null>(null);
  const [revisionIssueId, setRevisionIssueId] = useState<string | null>(null);
  const [showRevisionMarks, setShowRevisionMarks] = useState(false);
  const [showIssueLocations, setShowIssueLocations] = useState(true);
  const [locatedIssueId, setLocatedIssueId] = useState<string | null>(null);
  const [cancelSubmitting, setCancelSubmitting] = useState(false);
  const [cancelError, setCancelError] = useState("");
  const [showTechnicalError, setShowTechnicalError] = useState(false);
  const [baselineConflict, setBaselineConflict] = useState<string[]>([]);
  const [regenSubmitting, setRegenSubmitting] = useState(false);
  // P1-W2：持久化降级提示。后端 layer_complete 事件携带 persistence_degraded=true 时，
  // 表示 DB 持久化失败次数超过阈值（PERSIST_DEGRADED_THRESHOLD=3），崩溃恢复可能不完整。
  // 用户可手动重新生成或等待自动重试。
  const [persistenceDegraded, setPersistenceDegraded] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const issueMarkRefs = useRef<Record<string, HTMLElement | null>>({});
  const editorRef = useRef<HTMLTextAreaElement>(null);
  const editorMirrorRef = useRef<HTMLDivElement>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const hasNotifiedCompleteRef = useRef<Set<string>>(new Set());
  // 费用优化：SSE 活跃时降低轮询频率（3s→15s），避免三重冗余轮询
  const sseActiveRef = useRef(false);

  const effectiveId = humanWorkbenchPreview
    ? HUMAN_WORKBENCH_PREVIEW_ID
    : selectedId || activeExecutionId;

  const loadExecutions = useCallback(async () => {
    if (humanWorkbenchPreview) {
      setExecutions([]);
      setSelectedId(HUMAN_WORKBENCH_PREVIEW_ID);
      setDetail((current) => current?.id === HUMAN_WORKBENCH_PREVIEW_ID
        ? current
        : createHumanWorkbenchPreviewDetail(projectId));
      return;
    }
    try {
      const data = await listWorkflowExecutions(projectId, 20);
      setExecutions(data);
      if (!manualSelectionRef.current && data.length > 0) {
        // 默认跟随最新的活跃执行，避免已完成的旧 activeExecutionId
        // 把新启动的工作流压到“其他执行”中。
        const liveExecution = data.find((execution) => isLiveWorkflowStatus(execution.status));
        const preferredId = liveExecution?.id || selectedIdRef.current || activeExecutionIdRef.current || data[0].id;
        if (preferredId !== selectedIdRef.current) setSelectedId(preferredId);
      }
    } catch {}
  }, [humanWorkbenchPreview, projectId]);

  useEffect(() => {
    loadExecutions();
    const interval = setInterval(loadExecutions, 10000);
    return () => clearInterval(interval);
  }, [loadExecutions]);

  useEffect(() => {
    if (activeExecutionId) {
      manualSelectionRef.current = false;
      setSelectedId(activeExecutionId);
    }
  }, [activeExecutionId]);

  useEffect(() => {
    manualSelectionRef.current = false;
    if (humanWorkbenchPreview) {
      setSelectedId(HUMAN_WORKBENCH_PREVIEW_ID);
      setDetail(createHumanWorkbenchPreviewDetail(projectId));
    } else {
      setSelectedId(null);
      setDetail(null);
    }
  }, [humanWorkbenchPreview, projectId]);

  useEffect(() => {
    if (humanWorkbenchPreview) return;
    if (!effectiveId) return;
    const abortController = new AbortController();
    const loadDetail = async () => {
      try {
        const data = await getWorkflowExecution(projectId, effectiveId, { signal: abortController.signal });
        if (!abortController.signal.aborted) {
          setDetail(data);
        }
      } catch (e) {
        if (e instanceof DOMException && e.name === "AbortError") return;
      }
    };
    loadDetail();
    // 费用优化：SSE 活跃时降低轮询频率到 15s（降级 fallback），
    // SSE 断线时恢复 3s 高频轮询。消除 SSE + 3s轮询 的双重冗余。
    const getInterval = () => sseActiveRef.current ? 15000 : 3000;
    let interval = setInterval(loadDetail, getInterval());
    // 定期检查 SSE 状态，动态调整轮询间隔
    const adjustInterval = setInterval(() => {
      const newInterval = getInterval();
      clearInterval(interval);
      interval = setInterval(loadDetail, newInterval);
    }, 5000);
    return () => {
      abortController.abort();
      clearInterval(interval);
      clearInterval(adjustInterval);
    };
  }, [effectiveId, humanWorkbenchPreview, projectId]);

  useEffect(() => {
    if (humanWorkbenchPreview) return;
    if (!effectiveId) return;

    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }

    const isLiveExecution = (
      (effectiveId === activeExecutionId || isLiveWorkflowStatus(detail?.status))
      && !hasNotifiedCompleteRef.current.has(effectiveId)
    );

    if (!isLiveExecution) return;

    const sseAbortController = new AbortController();
    const url = workflowSSEUrl(projectId, effectiveId);
    const es = new EventSource(url);
    eventSourceRef.current = es;
    sseActiveRef.current = true;

    const safeFetchDetail = () => {
      if (sseAbortController.signal.aborted) return;
      getWorkflowExecution(projectId, effectiveId, { signal: sseAbortController.signal })
        .then((d) => { if (!sseAbortController.signal.aborted) setDetail(d); })
        .catch((e) => { if (e instanceof DOMException && e.name === "AbortError") return; });
    };

    es.onmessage = (e) => {
      try {
        const event: WorkflowSSEEvent = JSON.parse(e.data);
        if (event.type === "step_update" && event.agent_name) {
          setStepStatusOverrides((prev) => ({
            ...prev,
            [event.agent_name!]: event.status || "running",
          }));
          if (effectiveId) {
            safeFetchDetail();
          }
        }
        if (event.type === "candidate_ready" && effectiveId) {
          safeFetchDetail();
        }
        // 方案16：消费 DAG 层级事件
        if (event.type === "layer_start" && event.layer_index !== undefined) {
          setDagLayerProgress((prev) => ({
            ...prev,
            currentLayer: event.layer_index!,
            totalLayers: event.total_layers ?? prev.totalLayers,
            layerNodes: event.layer_nodes ?? [],
            layerStatus: "running",
          }));
        }
        if (event.type === "layer_complete" && event.layer_index !== undefined) {
          setDagLayerProgress((prev) => ({
            ...prev,
            currentLayer: event.layer_index!,
            executedNodes: event.executed ?? [],
            skippedNodes: event.skipped ?? [],
            layerStatus: "complete",
          }));
          // P1-W2：消费持久化降级标志，提示用户 DB 持久化可能不完整
          if (event.persistence_degraded) {
            setPersistenceDegraded(true);
          }
          safeFetchDetail();
        }
        if (event.type === "layer_failed" && event.layer_index !== undefined) {
          setDagLayerProgress((prev) => ({
            ...prev,
            currentLayer: event.layer_index!,
            failedNodes: event.failed_nodes ?? [],
            layerStatus: "failed",
            abortReason: event.reason,
          }));
        }
        if (event.type === "execution_update") {
          if (event.status === "completed") {
            setStepStatusOverrides({});
            // P1-W2：执行完成后清除降级提示（已完成执行不再展示）
            setPersistenceDegraded(false);
            hasNotifiedCompleteRef.current.add(effectiveId);
            getWorkflowExecution(projectId, effectiveId, { signal: sseAbortController.signal })
              .then((d) => {
                if (sseAbortController.signal.aborted) return;
                setDetail(d);
                if (onWorkflowComplete) onWorkflowComplete(effectiveId);
              }).catch((e) => { if (e instanceof DOMException && e.name === "AbortError") return; });
            loadExecutions();
            if (eventSourceRef.current) {
              eventSourceRef.current.close();
              eventSourceRef.current = null;
            }
            sseActiveRef.current = false;
          } else if (event.status === "failed" || event.status === "cancelled") {
            setStepStatusOverrides({});
            // P1-W2：执行失败/取消后也清除降级提示
            setPersistenceDegraded(false);
            hasNotifiedCompleteRef.current.add(effectiveId);
            safeFetchDetail();
            loadExecutions();
            if (eventSourceRef.current) {
              eventSourceRef.current.close();
              eventSourceRef.current = null;
            }
            sseActiveRef.current = false;
          }
        }
      } catch {}
    };

    return () => {
      sseAbortController.abort();
      es.close();
      if (eventSourceRef.current === es) {
        eventSourceRef.current = null;
      }
      sseActiveRef.current = false;
    };
  }, [effectiveId, humanWorkbenchPreview, projectId, activeExecutionId, detail?.status, onWorkflowComplete, loadExecutions]);

  useEffect(() => {
    if (!containerRef.current) return;
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        setContainerWidth(entry.contentRect.width);
      }
    });
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, []);
  const workflowCandidate = detail?.result_context?.candidate || null;
  const candidateReady = Boolean(detail?.result_context?.candidate_ready && workflowCandidate);
  const displaySteps = useMemo(
    () => detail
      ? materializeLifecycleSteps(detail.steps || [], detail.status, candidateReady)
      : [],
    [detail?.steps, detail?.status, candidateReady],
  );
  const processedSteps = displaySteps.filter((s) => STEP_SETTLED_STATUSES.has(s.status)).length;
  const totalSteps = displaySteps.length;
  const progressPct = detail?.status === "completed"
    ? 100
    : totalSteps > 0
      ? (processedSteps / totalSteps) * 100
      : detail?.total_layers
        ? ((detail.current_layer || 0) / detail.total_layers) * 100
        : 0;
  const elapsedEnd = detail && !isLiveWorkflowStatus(detail.status)
    ? parseWorkflowTimestamp(detail.updated_at)
    : Date.now();
  const elapsedDuration = detail
    ? Math.max(0, elapsedEnd - parseWorkflowTimestamp(detail.created_at))
    : 0;
  const llmUsage = asRecord(detail?.result_context?.llm_usage);
  const llmTokens = asNumber(llmUsage.prompt_tokens) + asNumber(llmUsage.completion_tokens);
  const llmCallCount = asNumber(llmUsage.call_count);

  const isRunning = detail?.status === "running" || detail?.status === "pending_validator_retry";
  const isCancellable = isRunning || isReviewWaitingStatus(detail?.status);
  const isWaitingReview = isReviewWaitingStatus(detail?.status);
  const review = isWaitingReview ? detail?.result_context?.review : undefined;
  const isSystemReview = review?.review_type === "system_review";
  const isChapterReview = String(review?.review_scope || "scene") === "chapter";
  const repairBusy = issueSubmitting !== null;

  useEffect(() => {
    if (detail?.status === "failed" && detail.error_message?.includes("outline_state_baseline_conflict")) {
      const conflictLines = detail.error_message.split(";").filter((s: string) => s.trim());
      setBaselineConflict(conflictLines.length > 0 ? conflictLines : ["大纲与当前故事状态存在冲突"]);
    } else {
      setBaselineConflict([]);
    }
  }, [detail?.status, detail?.error_message]);

  useEffect(() => {
    if (review) {
      setReviewText(review.candidate_text || "");
      setReviewError("");
      const latestRevision = [...review.violations].reverse().find((issue) => issue.revision_diff);
      if (latestRevision) {
        setRevisionIssueId(latestRevision.issue_id || latestRevision.violation_id || null);
      }
    }
  }, [review?.review_version, review?.candidate_text]);

  const submitReview = async (
    action: "accept_edited" | "retry" | "accept_without_recheck",
  ) => {
    if (!detail || !review) return;
    if (humanWorkbenchPreview) {
      setReviewError(
        action === "retry"
          ? "测试模式不会启动新的模型修复。"
          : action === "accept_without_recheck"
            ? "测试模式不会跳过复检或提交真实章节。"
            : "测试模式不会提交正文或恢复真实工作流。",
      );
      return;
    }
    setReviewSubmitting(true);
    setReviewError("");
    try {
      if (isSystemReview) {
        if (action === "retry") {
          const result = await retryValidator(projectId, detail.id);
          if (result.status === "running" || result.gate_passed) {
            setDetail({ ...detail, status: "running" });
            setStepStatusOverrides({});
          } else if (result.status === "waiting_human_system_review") {
            setReviewError(result.message || "审校器连续故障，需要人工确认");
          }
        } else {
          setDetail(null);
        }
      } else {
        const result = await resumeWorkflowReview(projectId, detail.id, {
          action,
          edited_text: action === "retry" ? undefined : reviewText,
          review_version: review.review_version,
        });
        setDetail({ ...detail, status: result.status || "running" });
        setStepStatusOverrides({});
      }
      await loadExecutions();
    } catch (error) {
      setReviewError(error instanceof Error ? error.message : "恢复工作流失败");
    } finally {
      setReviewSubmitting(false);
    }
  };

  const updateReview = (nextReview: WorkflowReview) => {
    setReviewText(nextReview.candidate_text || "");
    setDetail((current) => current
      ? {
          ...current,
          result_context: { ...(current.result_context || {}), review: nextReview },
        }
      : current
    );
  };

  const ignoreIssue = async (issue: WorkflowReviewIssue) => {
    if (!detail || !review || !issue.issue_id || repairBusy) return;
    if (humanWorkbenchPreview) {
      updateReview({
        ...review,
        review_version: review.review_version + 1,
        violations: review.violations.map((item) => item.issue_id === issue.issue_id
          ? { ...item, review_status: "ignored" as const }
          : item),
      });
      setExpandedIssueId(null);
      return;
    }
    setIssueSubmitting(issue.issue_id);
    setReviewError("");
    try {
      const result = await ignoreWorkflowReviewIssue(projectId, detail.id, issue.issue_id, {
        review_version: review.review_version,
        edited_text: reviewText,
      });
      updateReview(result.review);
    } catch (error) {
      setReviewError(error instanceof Error ? error.message : "忽略问题失败");
    } finally {
      setIssueSubmitting(null);
    }
  };

  const repairIssue = async (
    issue: WorkflowReviewIssue,
    useDefaultInstruction = false,
    repairStrategy?: "append_ending" | "allow_scene_rewrite" | "scene_restructure"
  ) => {
    if (!detail || !review || !issue.issue_id || repairBusy) return;
    const customMessage = (issueMessages[issue.issue_id] || "").trim();
    let message = customMessage;
    if (!message && useDefaultInstruction) {
      message = "请根据修订目标进行自然的局部修改，保留其余正文。";
    }
    if (!message && repairStrategy === "append_ending") {
      message = "请只在正文末尾或最后一个自然段补写结尾落点，让场景结束状态自然达成，保留其余正文。";
    }
    if (!message && repairStrategy === "allow_scene_rewrite") {
      message = "请交给 FBI 场景重构专员处理。允许重排最后若干段或整场结构，但必须保留既有事实、人物和事件顺序。";
    }
    if (humanWorkbenchPreview) {
      const original = String(issue.target_span || "");
      const replacement = issue.type === "clue_provenance_error"
        ? "她摸到空荡的袖口，才想起玄火令昨日已经交给三师兄保管。"
        : issue.type === "ending_state_not_reached"
          ? "废墟里重新燃起第一盏灯。二师兄把最后一根断梁抬到空地，三师兄蹲在灯下，用炭条画出了第一面新墙的位置。"
          : original;
      const start = original ? reviewText.indexOf(original) : -1;
      const nextText = start >= 0
        ? `${reviewText.slice(0, start)}${replacement}${reviewText.slice(start + original.length)}`
        : reviewText;
      const diffSegments = start >= 0
        ? [
            { operation: "equal" as const, text: reviewText.slice(0, start) },
            { operation: "delete" as const, text: original },
            { operation: "add" as const, text: replacement },
            { operation: "equal" as const, text: reviewText.slice(start + original.length) },
          ].filter((segment) => segment.text)
        : [{ operation: "equal" as const, text: reviewText }];
      updateReview({
        ...review,
        candidate_text: nextText,
        review_version: review.review_version + 1,
        violations: review.violations.map((item) => item.issue_id === issue.issue_id
          ? {
              ...item,
              review_status: "resolved" as const,
              repair_engine: "fbi",
              fbi_status: "resolved",
              revision_diff: {
                segments: diffSegments,
                added_chars: replacement.length,
                deleted_chars: original.length,
              },
            }
          : item),
      });
      setRevisionIssueId(issue.issue_id);
      setShowRevisionMarks(true);
      setShowIssueLocations(false);
      setExpandedIssueId(issue.issue_id);
      setIssueMessages((current) => ({ ...current, [issue.issue_id!]: "" }));
      return;
    }
    setIssueSubmitting(issue.issue_id);
    setReviewError("");
    try {
      const result = await chatWorkflowReviewIssue(projectId, detail.id, issue.issue_id, {
        review_version: review.review_version,
        edited_text: reviewText,
        message,
        repair_strategy: repairStrategy,
      });
      updateReview(result.review);
      setRevisionIssueId(issue.issue_id);
      setShowRevisionMarks(true);
      setShowIssueLocations(false);
      setExpandedIssueId(issue.issue_id);
      setIssueMessages((current) => ({ ...current, [issue.issue_id!]: "" }));
      // V2: 智能修订后自动复检刷新
      const newText = result.review.candidate_text || reviewText;
      if (!isChapterReview) {
        void autoRecheck(newText, result.review);
      }
    } catch (error) {
      setReviewError(error instanceof Error ? error.message : "智能修订失败");
    } finally {
      setIssueSubmitting(null);
    }
  };

  const revisionIssue = review?.violations.find(
    (issue) => (issue.issue_id || issue.violation_id) === revisionIssueId && issue.revision_diff
  );
  const rawPendingIssueCount = review
    ? review.violations.filter((issue) => {
        const status = issue.review_status || "open";
        const fbiStatus = String(issue.fbi_status || "");
        // 审校版本一致性：stale issue 不计入待处理
        if (issue.is_stale) return false;
        return Boolean(issue.blocks_commit) && issue.user_visible !== false && status !== "ignored" && status !== "resolved" && fbiStatus !== "resolved";
      }).length
    : 0;
  // F5: 优先使用后端按窗口聚合后的 window_count（5 个 issue → 2 个窗口），
  // 回退到 issue 计数。通用方案，不针对特定 metric。
  const reviewWindowCount = (review as unknown as { window_count?: number } | null)?.window_count;
  const pendingReviewIssueCount = rawPendingIssueCount === 0
    ? 0
    : (typeof reviewWindowCount === "number" && reviewWindowCount > 0
      ? Math.min(reviewWindowCount, rawPendingIssueCount)
      : rawPendingIssueCount);

  // V2 诊断增强：将 issues 分为当前问题 / 已解决 / 系统问题 / 过期问题
  const currentTextHash = useMemo(() => {
    if (!review) return "";
    // 优先使用 review 顶层的 current_text_hash（后端复检写入）
    const topHash = ("current_text_hash" in review ? (review as unknown as { current_text_hash?: string }).current_text_hash : undefined);
    if (topHash) return topHash;
    // 兜底：从 violations 中取
    for (const v of [...(review.violations || [])].reverse()) {
      if (v.text_hash) return v.text_hash;
    }
    return "";
  }, [review]);

  const classifiedIssues = useMemo(() => {
    if (!review) return { current: [], resolved: [], system: [], stale: [] };
    const current: WorkflowReviewIssue[] = [];
    const resolved: WorkflowReviewIssue[] = [];
    const system: WorkflowReviewIssue[] = [];
    const stale: WorkflowReviewIssue[] = [];

    for (const issue of review.violations) {
      if (issue.user_visible === false) continue;

      const status = issue.review_status || "open";
      const fbiStatus = String(issue.fbi_status || "");

      // 过期问题（text_hash 不匹配当前正文）
      if (issue.is_stale || (issue.text_hash && currentTextHash && issue.text_hash !== currentTextHash)) {
        stale.push(issue);
        continue;
      }

      // 已解决。必须先于系统分类，避免旧系统故障重新显示成当前问题。
      if (status === "resolved" || fbiStatus === "resolved") {
        resolved.push(issue);
        continue;
      }

      // 系统问题
      if (issue.is_system_issue || issue.repair_scope === "system") {
        system.push(issue);
        continue;
      }

      // 当前问题
      if (issue.blocks_commit || status === "open") {
        current.push(issue);
      }
    }
    return { current, resolved, system, stale };
  }, [review, currentTextHash]);

  const issueLocationView = useMemo(
    () => buildReviewIssueLocationView(reviewText, classifiedIssues.current),
    [classifiedIssues.current, reviewText],
  );
  const activeIssueLocation = locatedIssueId
    ? issueLocationView.locations[locatedIssueId]
    : undefined;
  const editorLocationSegments = useMemo(
    () => buildSingleIssueLocationSegments(reviewText, activeIssueLocation),
    [activeIssueLocation, reviewText],
  );

  useEffect(() => {
    if (showRevisionMarks || !locatedIssueId) return;
    let frame = 0;
    const positionLocatedIssue = () => {
      const mark = issueMarkRefs.current[locatedIssueId];
      if (!mark) return;
      const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      if (showIssueLocations) {
        mark.scrollIntoView({
          behavior: reducedMotion ? "auto" : "smooth",
          block: "center",
        });
        return;
      }

      const editor = editorRef.current;
      const mirror = editorMirrorRef.current;
      if (!editor || !mirror) return;
      const targetScrollTop = Math.max(
        0,
        mark.offsetTop - (editor.clientHeight / 2) + (mark.clientHeight / 2),
      );
      editor.scrollTop = targetScrollTop;
      mirror.scrollTop = targetScrollTop;
      editor.focus({ preventScroll: true });
    };
    const schedulePosition = () => {
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(positionLocatedIssue);
    };

    schedulePosition();
    window.addEventListener("resize", schedulePosition);
    const resizeObserver = typeof ResizeObserver !== "undefined" && editorRef.current
      ? new ResizeObserver(schedulePosition)
      : null;
    if (resizeObserver && editorRef.current) resizeObserver.observe(editorRef.current);

    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", schedulePosition);
      resizeObserver?.disconnect();
    };
  }, [editorLocationSegments, locatedIssueId, showIssueLocations, showRevisionMarks]);

  const [effectivenessProof, setEffectivenessProof] = useState<EffectivenessProof | null>(null);
  const [proofExpanded, setProofExpanded] = useState(false);
  const [rechecking, setRechecking] = useState(false);

  const refreshEffectivenessProof = useCallback(async () => {
    if (humanWorkbenchPreview || !projectId || !detail?.id) {
      setEffectivenessProof(null);
      return;
    }
    try {
      const proof = await editorTraceApi.getEffectivenessProof(projectId, detail.id);
      setEffectivenessProof(proof);
    } catch {
      setEffectivenessProof(null);
    }
  }, [humanWorkbenchPreview, projectId, detail?.id]);

  // 自动复检：智能修订后自动请求后端重新复检当前正文
  // 事务式：旧 issues 先标记 stale → 复检 → 只展示新 violations
  const autoRecheck = useCallback(async (text: string, baseReview?: WorkflowReview) => {
    if (humanWorkbenchPreview) return;
    if (!detail || !projectId || !text.trim()) return;
    const reviewForRecheck = baseReview || review;
    setRechecking(true);
    try {
      // 1. 先将所有旧 issues 标记 stale
      if (reviewForRecheck) {
        const staleMarked: WorkflowReviewIssue[] = reviewForRecheck.violations.map((v) => ({
          ...v,
          is_stale: true,
        }));
        const staleReview: WorkflowReview = {
          ...reviewForRecheck,
          candidate_text: text,
          violations: staleMarked,
        } as WorkflowReview;
        updateReview(staleReview);
      }

      // 2. 调用复检
      const result = await reReviewApi.recheck(projectId, detail.id, {
        candidate_text: text,
      });

      if (
        result.auto_resumed
        || result.status === "running"
        || result.status === "pending_validator_retry"
      ) {
        const resumedStatus = result.status === "pending_validator_retry"
          ? "pending_validator_retry"
          : "running";
        setDetail((current) => current
          ? {
              ...current,
              status: resumedStatus,
              error_message: resumedStatus === "running"
                ? ""
                : "Validator temporarily unavailable. Automatic re-verify is running.",
              result_context: result.review
                ? { ...(current.result_context || {}), review: result.review }
                : current.result_context,
            }
          : current
        );
        setStepStatusOverrides({});
        await loadExecutions();
        return;
      }

      // 3. 用复检结果更新 review
      if (reviewForRecheck) {
        const staleIssues = (result.stale_violations || []) as WorkflowReviewIssue[];
        const currentIssues = result.violations as WorkflowReviewIssue[];
        const nextReview: WorkflowReview = {
          ...reviewForRecheck,
          ...(result.review || {}),
          candidate_text: text,
          violations: [...currentIssues, ...staleIssues],
          review_version: result.review_version,
          current_text_hash: result.text_hash,
          passed: result.passed,
        } as WorkflowReview;
        updateReview(nextReview);
      }
      void refreshEffectivenessProof();
    } catch (e) {
      // 复检失败：保留正文修订，旧 issues 进入旧审校结果
      console.warn("自动复检失败:", e);
      setReviewError(e instanceof Error ? e.message : "自动复检失败，请在工作台中重试");
      if (reviewForRecheck) {
        const allStale: WorkflowReviewIssue[] = reviewForRecheck.violations.map((v) => ({
          ...v,
          is_stale: true,
        }));
        const staleReview: WorkflowReview = {
          ...reviewForRecheck,
          candidate_text: text,
          violations: allStale,
          passed: false,
        } as WorkflowReview;
        updateReview(staleReview);
      }
    } finally {
      setRechecking(false);
    }
  }, [detail, humanWorkbenchPreview, projectId, refreshEffectivenessProof, review, loadExecutions]);

  // 工作台打开时自动复检：如果 issues 不是基于当前正文
  useEffect(() => {
    if (humanWorkbenchPreview) return;
    if (!review || !detail || !projectId) return;
    if (!isWaitingReview) return;
    const candidateText = review.candidate_text || "";
    if (review.passed && candidateText.trim() && !rechecking) {
      void autoRecheck(candidateText);
      return;
    }
    if (isChapterReview) return;
    // 只在首次加载时检查，避免循环
    const proofIssuesCurrent = effectivenessProof?.issues_based_on_current_text;
    if (proofIssuesCurrent === false && !rechecking) {
      if (candidateText.trim()) {
        void autoRecheck(candidateText);
      }
    }
  }, [humanWorkbenchPreview, isWaitingReview, isChapterReview, effectivenessProof?.issues_based_on_current_text]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    void refreshEffectivenessProof();
  }, [
    refreshEffectivenessProof,
    detail?.status,
    review?.review_version,
    review?.candidate_text,
    currentTextHash,
  ]);

  const proofCompileStatus = String(effectivenessProof?.v2_compile_status || "");
  const writerInputSnapshots = effectivenessProof?.writer_input_snapshots || [];
  const proofActiveCapabilities = effectivenessProof?.active_capabilities || [];
  const proofSourceTrace = effectivenessProof?.writer_input_source_trace || {};

  useEffect(() => {
    if (!detail?.id) return;
    const liveStatus = detail.status === "running"
      || detail.status === "pending_validator_retry"
      || isReviewWaitingStatus(detail.status);
    if (!liveStatus && proofCompileStatus !== "skipped") return;

    // 费用优化：SSE 活跃时降低 effectiveness 轮询频率到 15s
    const intervalMs = sseActiveRef.current ? 15000 : 3000;
    const timer = window.setInterval(() => {
      void refreshEffectivenessProof();
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [detail?.id, detail?.status, proofCompileStatus, refreshEffectivenessProof]);
  const workflowError = detail?.error_message || "";
  const workflowErrorSummary = summarizeWorkflowError(workflowError);
  const hasTechnicalErrorDetails = workflowErrorSummary !== workflowError;

  useEffect(() => {
    setShowTechnicalError(false);
  }, [detail?.id, detail?.error_message]);

  const autoRecheckTransition = Boolean(
    review
    && !isSystemReview
    && review.candidate_text?.trim()
    && (
      rechecking
      || review.passed
      || (!isChapterReview && effectivenessProof?.issues_based_on_current_text === false)
    )
  );

  return (
    <div className="workflow-monitor flex h-full flex-col" data-workflow-status={detail?.status || "idle"}>
      {detail && (
        <div className="workflow-monitor__header shrink-0 border-b border-pine-200/40 px-4 py-3">
          <div className="flex items-center justify-between">
            <div className="min-w-0 flex-1">
              <h3 className="workflow-monitor__title truncate text-sm font-semibold text-pine-700">
                {workflowTitle(detail.trigger_type)}
              </h3>
              <div className="workflow-monitor__meta mt-1 flex items-center gap-2">
                <span
                  className={`workflow-monitor__status-dot inline-block h-2 w-2 rounded-full ${
                    detail.status === "running"
                      ? "bg-[#8F7650] animate-pulse"
                      : detail.status === "completed"
                      ? "bg-emerald-400"
                      : isContentReviewWaitingStatus(detail.status)
                      ? "bg-[#75677F]"
                      : detail.status === "waiting_human_system_review"
                      ? "bg-red-400"
                      : detail.status === "pending_validator_retry"
                      ? "bg-[#75677F] animate-pulse"
                      : detail.status === "failed"
                      ? "bg-red-400"
                      : detail.status === "cancelled"
                      ? "bg-gray-400"
                      : "bg-gray-500"
                  }`}
                />
                <span className="workflow-monitor__status-copy text-xs text-pine-700">
                  {detail.status === "running"
                    ? `${processedSteps}/${totalSteps} 节点 · 已运行 ${formatElapsedDuration(elapsedDuration)}`
                    : detail.status === "completed"
                    ? `完成 · 历时 ${formatElapsedDuration(elapsedDuration)}`
                    : isContentReviewWaitingStatus(detail.status)
                    ? `${autoRecheckTransition ? "自动复检中" : "等待人工审核"} · 已运行 ${formatElapsedDuration(elapsedDuration)}`
                    : detail.status === "waiting_human_system_review"
                    ? "系统故障"
                    : detail.status === "pending_validator_retry"
                    ? `审校器恢复中 · 已运行 ${formatElapsedDuration(elapsedDuration)}`
                    : detail.status === "failed"
                    ? "失败"
                    : detail.status === "cancelled"
                    ? "已终止"
                    : detail.status}
                </span>
              </div>
              {llmTokens > 0 && (
                <div className="workflow-monitor__metrics mt-2 flex flex-wrap gap-1.5 text-[10px] text-pine-600/70">
                  {llmTokens > 0 && (
                    <span className="rounded-full border border-pine-200/60 bg-white/45 px-2 py-0.5">
                      Token {Math.round(llmTokens).toLocaleString("zh-CN")}
                    </span>
                  )}
                  {llmCallCount > 0 && (
                    <span className="rounded-full border border-pine-200/60 bg-white/45 px-2 py-0.5">
                      {llmCallCount} 次模型调用
                    </span>
                  )}
                </div>
              )}
              {persistenceDegraded && isRunning && (
                <div className="mt-2 rounded-lg border border-amber-500/30 bg-amber-500/5 px-2.5 py-1.5 text-[11px] leading-relaxed text-amber-600">
                  <p>保存状态出现异常：本次结果仍会返回，但中断后的自动恢复记录可能不完整。生成完成后请确认章节内容。</p>
                </div>
              )}
              {(detail.status === "failed" || detail.status === "cancelled" || isReviewWaitingStatus(detail.status)) && detail.error_message && (
                <div
                  className={
                    isReviewWaitingStatus(detail.status)
                      ? "mt-2 rounded-lg border border-violet-500/20 bg-violet-500/5 px-2.5 py-1.5 text-[11px] leading-relaxed text-violet-400"
                      : "mt-2 rounded-lg border border-red-500/20 bg-red-500/5 px-2.5 py-1.5 text-[11px] leading-relaxed text-red-400"
                  }
                >
                  <p>{workflowErrorSummary}</p>
                  {import.meta.env.DEV && hasTechnicalErrorDetails && (
                    <button
                      type="button"
                      onClick={() => setShowTechnicalError((current) => !current)}
                      className={
                        isReviewWaitingStatus(detail.status)
                          ? "mt-1 flex items-center gap-1 text-[10px] text-violet-300/80 hover:text-violet-200"
                          : "mt-1 flex items-center gap-1 text-[10px] text-red-300/80 hover:text-red-200"
                      }
                    >
                      {showTechnicalError ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                      {showTechnicalError ? "收起技术详情" : "展开技术详情"}
                    </button>
                  )}
                  {import.meta.env.DEV && showTechnicalError && hasTechnicalErrorDetails && (
                    <pre
                      className={
                        isReviewWaitingStatus(detail.status)
                          ? "mt-1.5 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded border border-violet-500/15 bg-black/20 px-2 py-1.5 font-mono text-[10px] leading-4 text-violet-300/80"
                          : "mt-1.5 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded border border-red-500/15 bg-black/20 px-2 py-1.5 font-mono text-[10px] leading-4 text-red-300/80"
                      }
                    >
                      {workflowError}
                    </pre>
                  )}
                </div>
              )}
              {(detail.status === "failed" || detail.status === "cancelled") && !baselineConflict.length && (
                <div className="mt-2 flex gap-2">
                  <button
                    type="button"
                    disabled={regenSubmitting}
                    onClick={async () => {
                      if (!detail) return;
                      setRegenSubmitting(true);
                      try {
                        const chapterNum = Number(detail.result_context?.chapter_number) || 1;
                        await regenerateFromOutline(projectId, chapterNum);
                        await loadExecutions();
                      } catch (e) {
                        console.error("重建章节失败", e);
                      } finally {
                        setRegenSubmitting(false);
                      }
                    }}
                    className="rounded border border-orange-500/30 bg-orange-500/10 px-2.5 py-1 text-[11px] text-orange-300 hover:bg-orange-500/20 disabled:opacity-50"
                  >
                    {regenSubmitting ? "重建中..." : "按新大纲重建本章"}
                  </button>
                </div>
              )}
            </div>
            {totalSteps > 0 && (
              <div className="ml-2 text-right">
                <span className="workflow-monitor__percentage font-mono text-xs font-semibold text-pine-700">{Math.round(progressPct)}%</span>
              </div>
            )}
          </div>
          {isRunning && totalSteps > 0 && (
            <div className="workflow-monitor__progress relative mt-2 h-3" aria-label={`工作流进度 ${Math.round(progressPct)}%`}>
              <div className="workflow-monitor__progress-track absolute left-0 right-0 top-1.5 h-px bg-pine-900/10" />
              <div
                className="workflow-monitor__progress-value absolute left-0 top-1.5 h-px bg-[#8F7650]/60 transition-all duration-500"
                style={{ width: `${progressPct}%` }}
              />
              <span
                className="workflow-monitor__progress-marker absolute top-[3px] h-1.5 w-1.5 -translate-x-1/2 rounded-full border border-[#F7F4EB] bg-[#8F7650] transition-all duration-500"
                style={{ left: `${progressPct}%` }}
              />
            </div>
          )}
          {isCancellable && (
            <div className="workflow-monitor__cancel-wrap mt-2 flex justify-end">
              <button
                disabled={cancelSubmitting}
                onClick={async () => {
                  if (!detail) return;
                  if (humanWorkbenchPreview) {
                    setCancelError("测试模式不会终止或修改真实工作流。");
                    return;
                  }
                  setCancelSubmitting(true);
                  setCancelError("");
                  try {
                    await cancelWorkflow(projectId, detail.id);
                    setDetail({ ...detail, status: "cancelled", error_message: "用户手动终止工作流" });
                    await loadExecutions();
                  } catch (e) {
                    console.error("终止工作流失败", e);
                    setCancelError(e instanceof Error ? e.message : "终止工作流失败");
                  } finally {
                    setCancelSubmitting(false);
                  }
                }}
                className="workflow-monitor__cancel rounded-md border border-red-500/30 bg-red-500/10 px-3 py-1 text-xs text-red-400 transition-colors hover:border-red-500/50 hover:bg-red-500/20 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {cancelSubmitting ? "正在终止..." : "终止工作流"}
              </button>
            </div>
          )}
          {cancelError && <p className="mt-2 text-right text-xs text-red-400">{cancelError}</p>}
          {candidateReady && workflowCandidate && (
            <div className="mt-3 rounded-lg border border-emerald-500/25 bg-emerald-500/5 px-3 py-2">
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 text-xs font-medium text-emerald-300">
                    <Sparkles className="h-3.5 w-3.5" />
                    整章候选稿已生成
                  </div>
                  <p className="mt-1 text-[11px] leading-relaxed text-pine-700">
                    后台仍会继续完成场景审校、事实抽取和章节提交；普通质量问题将进入报告，不再阻断候选稿可见。
                  </p>
                </div>
                <div className="flex shrink-0 flex-wrap gap-1.5 text-[10px] text-pine-700 sm:justify-end">
                  <span className="rounded border border-emerald-500/20 bg-emerald-500/10 px-2 py-1">
                    {workflowCandidate.word_count ?? 0} 字
                  </span>
                  <span className="rounded border border-pine-200/70 bg-white/50 px-2 py-1">
                    {workflowCandidate.scene_count ?? 0} 场
                  </span>
                  {workflowCandidate.alignment_method && (
                    <span className="rounded border border-pine-200/70 bg-white/50 px-2 py-1">
                      {workflowCandidate.alignment_method}
                    </span>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      <div className="workflow-monitor__body flex-1 overflow-auto" ref={containerRef}>
        {!detail ? (
          <div className="p-4">
            {executions.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-center">
                <div className="mb-3 text-3xl opacity-30">⬡</div>
                <p className="text-sm text-pine-700">暂无工作流记录</p>
                <p className="mt-1 text-xs text-pine-700">
                  在右侧“本章蓝图”顶部点击「生成本章」开始
                </p>
              </div>
            ) : (
              <div className="space-y-1.5">
                <h4 className="mb-2 text-xs font-medium text-pine-700">历史执行</h4>
                {executions.map((exec) => (
                  <div
                    key={exec.id}
                    onClick={() => {
                      manualSelectionRef.current = true;
                      setSelectedId(exec.id);
                    }}
                    className={`cursor-pointer rounded-lg border px-3 py-2 transition-colors ${
                      selectedId === exec.id
                        ? "border-magic-500/30 bg-magic-500/5"
                        : "border-pine-200/40 hover:border-pine-200 hover:bg-white/50"
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-xs text-pine-700">
                        {exec.trigger_type || "生成任务"}
                      </span>
                      <span
                        className={`h-2 w-2 rounded-full ${
                          exec.status === "completed"
                            ? "bg-emerald-400"
                            : exec.status === "failed"
                            ? "bg-red-400"
                            : exec.status === "running"
                            ? "bg-[#8F7650] animate-pulse"
                            : isContentReviewWaitingStatus(exec.status)
                            ? "bg-[#75677F]"
                            : exec.status === "waiting_human_system_review"
                            ? "bg-red-400"
                            : exec.status === "pending_validator_retry"
                            ? "bg-[#75677F] animate-pulse"
                            : exec.status === "cancelled"
                            ? "bg-gray-400"
                            : "bg-gray-500"
                        }`}
                      />
                    </div>
                    <div className="mt-0.5 text-[10px] text-pine-700">
                      {formatTime(exec.created_at)}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        ) : (
          <div className="p-2">
            <DeckTimeline
              steps={displaySteps}
              stepStatusOverrides={stepStatusOverrides}
              executionStatus={detail.status}
            />

          </div>
        )}
      </div>

      {review && !isSystemReview && !autoRecheckTransition && createPortal(
        <div className="review-workbench-overlay fixed inset-0 z-[100] flex items-center justify-center p-4">
          <div
            className="review-workbench flex h-[90vh] w-full max-w-6xl flex-col overflow-hidden"
            role="dialog"
            aria-modal="true"
            aria-labelledby="review-workbench-title"
          >
            <div className="review-workbench__header flex items-center justify-between px-6 py-4">
              <div>
                <div className="flex items-center gap-2">
                  <AlertTriangle className="h-5 w-5 text-magic-500" />
                  <h3 id="review-workbench-title" className="text-base font-bold text-pine-900">
                    {isChapterReview ? "章节修订工作台" : "场景修订工作台"}
                  </h3>
                </div>
                <p className="mt-1 text-xs text-pine-600">{review.message}</p>
                {repairBusy && (
                  <p className="mt-1 text-[11px] text-sky-300">FBI 正在处理当前问题，其他修订入口已暂时锁定。</p>
                )}
              </div>
              <div className="text-right text-xs text-pine-700">
                <p>
                  {pendingReviewIssueCount} 项待处理
                </p>
                <p className="mt-1">已自动修复 {review.guidance?.auto_repair_attempt_count ?? 0} 轮</p>
              </div>
            </div>
            <div className="review-workbench__main grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[minmax(0,1.4fr)_minmax(340px,0.6fr)]">
              {/* 左侧：正文与修订 */}
              <section className="review-workbench__copy-panel flex min-h-0 flex-col border-b lg:border-b-0 lg:border-r">
                <div className="review-workbench__copy-toolbar flex items-center justify-between gap-3 px-4 py-2">
                  <div className="flex items-center gap-2 text-xs font-semibold text-pine-800">
                    <FileText className="h-4 w-4 text-magic-500" />
                    {showRevisionMarks && revisionIssue
                      ? "修订痕迹对照"
                      : showIssueLocations
                      ? "问题展示"
                      : isChapterReview
                      ? "候选章节（可编辑）"
                      : "候选正文（可编辑）"}
                  </div>
                  <div className="review-workbench__view-controls flex min-w-0 items-center gap-2">
                    <div className="review-workbench__view-tabs" role="tablist" aria-label="正文视图">
                      <button
                        type="button"
                        role="tab"
                        aria-selected={showIssueLocations && !showRevisionMarks}
                        data-active={showIssueLocations && !showRevisionMarks}
                        onClick={() => {
                          setShowIssueLocations(true);
                          setShowRevisionMarks(false);
                        }}
                        className="review-workbench__view-tab"
                      >
                        问题展示
                      </button>
                      <button
                        type="button"
                        role="tab"
                        aria-selected={!showIssueLocations && !showRevisionMarks}
                        data-active={!showIssueLocations && !showRevisionMarks}
                        onClick={() => {
                          setShowIssueLocations(false);
                          setShowRevisionMarks(false);
                        }}
                        className="review-workbench__view-tab"
                      >
                        编辑正文
                      </button>
                    {revisionIssue?.revision_diff && (
                      <button
                        type="button"
                        role="tab"
                        aria-selected={showRevisionMarks}
                        data-active={showRevisionMarks}
                        onClick={() => {
                          setShowIssueLocations(false);
                          setShowRevisionMarks(true);
                        }}
                        className="review-workbench__view-tab"
                      >
                        修订痕迹
                      </button>
                    )}
                    </div>
                    <span className="text-[11px] text-pine-700">{reviewText.length} 字</span>
                  </div>
                </div>
                {showRevisionMarks && revisionIssue?.revision_diff ? (
                  <div className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap bg-transparent px-6 py-5 text-sm leading-8 text-pine-800">
                    <div className="mb-4 flex items-center gap-3 border-b border-pine-200/70 pb-3 text-[11px]">
                      <span className="flex items-center gap-1.5 text-emerald-300">
                        <span className="h-2 w-2 rounded-sm bg-emerald-400/70" />
                        新增 {revisionIssue.revision_diff.added_chars} 字
                      </span>
                      <span className="flex items-center gap-1.5 text-red-300">
                        <span className="h-2 w-2 rounded-sm bg-red-400/70" />
                        删除 {revisionIssue.revision_diff.deleted_chars} 字
                      </span>
                    </div>
                    {revisionIssue.revision_diff.segments.map((segment, segmentIndex) => (
                      <span
                        key={segmentIndex}
                        className={
                          segment.operation === "add"
                            ? "rounded-sm bg-emerald-500/20 text-emerald-100"
                            : segment.operation === "delete"
                            ? "rounded-sm bg-red-500/20 text-red-200 line-through decoration-red-400"
                            : ""
                        }
                      >
                        {segment.text}
                      </span>
                    ))}
                  </div>
                ) : showIssueLocations ? (
                  <div
                    className="review-workbench__location-copy min-h-0 flex-1 overflow-auto px-6 py-5 text-sm leading-8"
                    role="tabpanel"
                    aria-label="问题展示正文"
                  >
                    {issueLocationView.segments.map((segment, segmentIndex) => (
                      segment.issueIds.length > 0 ? (
                        <mark
                          key={segmentIndex}
                          ref={(element) => {
                            segment.issueIds.forEach((issueId) => {
                              issueMarkRefs.current[issueId] = element;
                            });
                          }}
                          className="review-workbench__issue-mark"
                          data-tone={segment.tone || "advisory"}
                          data-active={segment.issueIds.includes(locatedIssueId || "")}
                        >
                          {segment.text}
                        </mark>
                      ) : (
                        <span key={segmentIndex}>{segment.text}</span>
                      )
                    ))}
                  </div>
                ) : (
                  <div className="review-workbench__editor-shell min-h-0 flex-1">
                    <div
                      ref={editorMirrorRef}
                      className="review-workbench__editor-mirror"
                      aria-hidden="true"
                    >
                      {editorLocationSegments.map((segment, segmentIndex) => (
                        segment.issueIds.length > 0 ? (
                          <mark
                            key={segmentIndex}
                            ref={(element) => {
                              segment.issueIds.forEach((issueId) => {
                                issueMarkRefs.current[issueId] = element;
                              });
                            }}
                            className="review-workbench__editor-mark"
                            data-tone={segment.tone || "advisory"}
                          >
                            {segment.text}
                          </mark>
                        ) : (
                          <span key={segmentIndex}>{segment.text}</span>
                        )
                      ))}
                    </div>
                    <textarea
                      ref={editorRef}
                      value={reviewText}
                      onChange={(event) => setReviewText(event.target.value)}
                      onScroll={(event) => {
                        const mirror = editorMirrorRef.current;
                        if (!mirror) return;
                        mirror.scrollTop = event.currentTarget.scrollTop;
                        mirror.scrollLeft = event.currentTarget.scrollLeft;
                      }}
                      aria-label="编辑候选正文"
                      className="review-workbench__editor"
                    />
                  </div>
                )}
              </section>
              {/* 右侧：分析面板 */}
              <div className="review-workbench__analysis-panel flex min-h-0 flex-col overflow-y-auto">
                {effectivenessProof && (
                  <section className="flex flex-col border-b border-pine-200/50 shrink-0">
                  <button
                    type="button"
                    className="flex items-center gap-1.5 px-4 py-2.5 text-xs font-medium text-pine-700 hover:text-pine-700"
                    onClick={() => setProofExpanded(!proofExpanded)}
                  >
                    {proofExpanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                    <ShieldCheck className="h-3.5 w-3.5" />
                    生效证明
                  </button>
                  {proofExpanded && (
                    <div className="space-y-1.5 px-4 pb-3 text-[11px]">
                      <div className="flex justify-between text-pine-700">
                        <span>V2 输入</span>
                        <span className={effectivenessProof.v2_input_used ? "text-emerald-300" : "text-red-300"}>
                          {effectivenessProof.v2_input_used ? "已使用" : "未使用"}
                        </span>
                      </div>
                      <div className="flex justify-between text-pine-700">
                        <span>must_include 数量</span>
                        <span className="text-pine-700">{String(effectivenessProof.must_include_count ?? 0)}</span>
                      </div>
                      <div className="flex justify-between text-pine-700">
                        <span>forbidden 数量</span>
                        <span className="text-pine-700">{String(effectivenessProof.forbidden_count ?? 0)}</span>
                      </div>
                      <div className="flex justify-between text-pine-700">
                        <span>字数硬上限 / 实际字数</span>
                        <span className="text-pine-700">
                          {String(effectivenessProof.hard_max_chars ?? "-")} / {String(effectivenessProof.actual_chars ?? "-")}
                        </span>
                      </div>
                      <div className="flex justify-between text-pine-700">
                        <span>FBI V2 介入</span>
                        <span className={effectivenessProof.fbi_v2_engaged ? "text-emerald-300" : "text-pine-700"}>
                          {effectivenessProof.fbi_v2_engaged ? "是" : "否"}
                        </span>
                      </div>
                      <div className="flex justify-between text-pine-700">
                        <span>修复工单</span>
                        <span className="text-pine-700">
                          {String(effectivenessProof.repair_orders_succeeded ?? 0)} 成功 / {String(effectivenessProof.repair_orders_failed ?? 0)} 失败
                        </span>
                      </div>
                      {Array.isArray(effectivenessProof.failure_reasons) && (effectivenessProof.failure_reasons as string[]).length > 0 && (
                        <div className="text-pine-700">
                          失败原因：{(effectivenessProof.failure_reasons as string[]).join(", ")}
                        </div>
                      )}
                      <div className="flex justify-between text-pine-700">
                        <span>降级旧链路</span>
                        <span className={effectivenessProof.legacy_fallback ? "text-magic-300" : "text-pine-700"}>
                          {effectivenessProof.legacy_fallback ? "是" : "否"}
                        </span>
                      </div>
                      <div className="flex justify-between text-pine-700">
                        <span>issue 基于最新正文</span>
                        <span className={effectivenessProof.issues_based_on_current_text ? "text-emerald-300" : "text-red-300"}>
                          {effectivenessProof.issues_based_on_current_text ? "是" : "否"}
                        </span>
                      </div>
                      <div className="flex justify-between text-pine-700">
                        <span>V2 编译状态</span>
                        <span className={
                          effectivenessProof.v2_compile_status === "ok" ? "text-emerald-300" :
                          effectivenessProof.v2_compile_status === "failed" ? "text-red-300" : "text-magic-300"
                        }>
                          {String(effectivenessProof.v2_compile_status ?? "unknown")}
                        </span>
                      </div>
                      {effectivenessProof.trace_id && (
                        <div className="flex justify-between gap-2 text-pine-700">
                          <span>trace</span>
                          <span className="truncate text-pine-700">{effectivenessProof.trace_id}</span>
                        </div>
                      )}
                      {proofActiveCapabilities.length > 0 && (
                        <div className="space-y-1 rounded-md border border-emerald-500/20 bg-emerald-500/5 p-2">
                          <div className="flex items-center justify-between gap-2 text-emerald-200">
                            <span>Writer 输入增强</span>
                            <span>{proofActiveCapabilities.length} 项</span>
                          </div>
                          <div className="flex flex-wrap gap-1">
                            {proofActiveCapabilities.map((capability) => (
                              <span
                                key={capability}
                                className="rounded border border-emerald-500/25 px-1.5 py-0.5 text-[10px] text-emerald-100"
                              >
                                {capability}
                              </span>
                            ))}
                          </div>
                          {Object.keys(proofSourceTrace).length > 0 && (
                            <div className="space-y-0.5 text-[10px] text-pine-700">
                              {Object.entries(proofSourceTrace).slice(0, 4).map(([capability, sources]) => (
                                <div key={capability} className="line-clamp-1">
                                  {capability}: {Array.isArray(sources) ? sources.slice(0, 3).join(" / ") : ""}
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      )}
                      {writerInputSnapshots.length > 0 && (
                        <div className="space-y-1 rounded-md border border-pine-200/70 bg-white/50 p-2">
                          <div className="flex items-center justify-between text-pine-700">
                            <span>Writer 输入快照</span>
                            <span>{writerInputSnapshots.length} 场</span>
                          </div>
                          {writerInputSnapshots.slice(0, 3).map((snapshot) => (
                            <div key={snapshot.scene_index} className="space-y-0.5 border-t border-pine-200/70 pt-1">
                              <div className="flex justify-between text-pine-700">
                                <span>场景 {snapshot.scene_index + 1}</span>
                                <span>
                                  must {snapshot.must_include?.length ?? 0} / avoid {snapshot.must_avoid?.length ?? 0}
                                </span>
                              </div>
                              {snapshot.active_capabilities?.length > 0 && (
                                <div className="line-clamp-1 text-[10px] text-emerald-300">
                                  {snapshot.active_capabilities.join(" / ")}
                                </div>
                              )}
                              {snapshot.ending_state && (
                                <div className="line-clamp-1 text-[10px] text-pine-700">
                                  结尾：{snapshot.ending_state}
                                </div>
                              )}
                            </div>
                          ))}
                          {writerInputSnapshots.length > 3 && (
                            <div className="text-[10px] text-pine-700">
                              另有 {writerInputSnapshots.length - 3} 场已记录
                            </div>
                          )}
                        </div>
                      )}
                      {/* 立即复检按钮 */}
                      {!isChapterReview && !effectivenessProof.issues_based_on_current_text && !rechecking && review?.candidate_text && (
                        <button
                          type="button"
                          className="mt-2 w-full rounded-md bg-white/50 px-3 py-1.5 text-xs font-medium text-pine-700 hover:bg-white/50 disabled:opacity-50"
                          onClick={() => void autoRecheck(review.candidate_text || "")}
                        >
                          立即复检当前正文
                        </button>
                      )}
                      {rechecking && (
                        <div className="mt-2 text-center text-xs text-magic-300">
                          正在复检当前正文...
                        </div>
                      )}
                    </div>
                  )}
                </section>
              )}
              <section className="flex min-h-0 flex-col">
                <div className="border-b border-pine-200/70 px-4 py-3">
                  <p className="text-xs font-medium text-pine-700">审校问题</p>
                  <p className="mt-1 text-[11px] text-pine-700">
                    {isChapterReview
                      ? "展开问题可辅助修订；提交后会按场景重新切分并继续完整审查。"
                      : "展开问题可让智能体局部修订，也可以明确忽略。"}
                  </p>
                </div>
                <div className="flex-1 space-y-2 p-4">
                  {/* 当前问题 */}
                  {classifiedIssues.current.length > 0 && (
                    <div className="mb-4">
                      <div className="mb-3 flex items-center gap-1.5 text-xs font-bold text-crimson-600">
                        <AlertTriangle className="h-4 w-4" />
                        当前问题 ({classifiedIssues.current.length})
                      </div>
                      <div className="space-y-3">
                  {classifiedIssues.current.map((issue, index) => {
                    const issueId = reviewIssueIdentity(issue, index);
                    const expanded = expandedIssueId === issueId;
                    const status = issue.review_status || "open";
                    const busy = issueSubmitting === issueId;
                    const lockedByOtherRepair = repairBusy && !busy;
                    const repairedByFbi = issue.repair_engine === "fbi" || Boolean(issue.fbi_case_id);
                    const issueLocated = Boolean(issueLocationView.locations[issueId]);
                    const issueLocationMissing = issueLocationView.unmatchedIssueIds.has(issueId);
                    const unsafeLocalization = issue.type === "missing_must_show" && (
                      issue.localization_status !== "localized"
                      || !issue.target_span
                      || (typeof issue.location_confidence === "number" && issue.location_confidence < 0.6)
                    );
                    return (
                      <article
                        key={issueId}
                        className={`review-workbench__issue overflow-hidden rounded-xl border ${lockedByOtherRepair ? "opacity-60 grayscale" : ""} ${
                          status === "ignored"
                            ? "review-workbench__issue--muted border-pine-200 bg-pine-50/50 shadow-sm"
                            : status === "resolved"
                            ? "review-workbench__issue--resolved border-emerald-200 bg-emerald-50/50 shadow-sm"
                            : "review-workbench__issue--open border-crimson-300 bg-crimson-50/80"
                        }`}
                        data-active={locatedIssueId === issueId}
                      >
                        <button
                          type="button"
                          onClick={() => {
                            setExpandedIssueId(expanded ? null : issueId);
                            setLocatedIssueId(issueId);
                            setShowIssueLocations(false);
                            setShowRevisionMarks(false);
                          }}
                          className="review-workbench__issue-trigger flex w-full items-start gap-2 px-4 py-3.5 text-left"
                        >
                          {expanded ? <ChevronDown className="mt-0.5 h-4 w-4 shrink-0 text-pine-500" /> : <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-pine-500" />}
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center justify-between gap-2">
                              <p className="text-xs font-bold text-pine-900">{issueTypeLabel(issue.type)}</p>
                              <span className={`shrink-0 text-[10px] font-medium px-2 py-0.5 rounded-full ${status === "open" ? "bg-crimson-50 text-crimson-600 border border-crimson-100" : status === "resolved" ? "bg-emerald-50 text-emerald-600 border border-emerald-100" : "bg-pine-100 text-pine-600 border border-pine-200"}`}>
                                {status === "open" ? "待处理" : status === "resolved" ? "已解决" : status === "pending_recheck" ? "等待复检" : "已忽略"}
                              </span>
                            </div>
                            {Boolean(issue.classification) && (
                              <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                                <span className="rounded border border-pine-200 bg-white/60 px-1.5 py-0.5 text-[10px] text-pine-700">
                                  {protocolClassificationLabel(issue.classification)}
                                </span>
                                {Boolean(issue.recommended_route) && (
                                  <span className="font-mono text-[10px] text-pine-500">{String(issue.recommended_route)}</span>
                                )}
                              </div>
                            )}
                            {repairedByFbi && (
                              <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                                <span className="rounded border border-sky-200 bg-sky-50 px-1.5 py-0.5 text-[10px] text-sky-700">
                                  {fbiStatusLabel(issue.fbi_status)}
                                </span>
                                {issue.fbi_case_id && (
                                  <span className="font-mono text-[10px] text-pine-500">{issue.fbi_case_id}</span>
                                )}
                              </div>
                            )}
                            <p className="mt-2 line-clamp-2 text-xs leading-relaxed text-pine-800">{String(issue.detail || "")}</p>
                            <p className={`mt-1.5 text-[10px] ${issueLocated ? "text-pine-600" : "text-amber-700"}`}>
                              {issueLocated
                                ? locatedIssueId === issueId && !showIssueLocations && !showRevisionMarks
                                  ? "已在编辑正文中定位"
                                  : "点击后在编辑正文中定位"
                                : "正文中未找到可精确定位的片段"}
                            </p>
                          </div>
                        </button>
                        {expanded && (
                          <div className="space-y-3 border-t border-pine-200/70 px-3 py-3">
                            {issueLocationMissing && (
                              <p className="review-workbench__location-missing rounded border px-2 py-1.5 text-[11px] leading-5">
                                正文中未找到该片段。请根据问题说明人工查找；系统不会用近似文本制造错误高亮。
                              </p>
                            )}
                            {Boolean(issue.target_span) && (
                              <div>
                                <p className="text-[10px] font-medium text-pine-700">冲突片段</p>
                                <p className="mt-1 rounded bg-white/50 px-2 py-1.5 text-[11px] leading-5 text-pine-700">{String(issue.target_span)}</p>
                              </div>
                            )}
                            {Boolean(issue.expected_behavior) && (
                              <div>
                                <p className="text-[10px] font-medium text-pine-700">修订目标</p>
                                <p className="mt-1 text-[11px] leading-5 text-emerald-300/90">{String(issue.expected_behavior)}</p>
                              </div>
                            )}
                            {unsafeLocalization && (
                              <p className="rounded border border-amber-200 bg-amber-50 px-2 py-1.5 text-[11px] leading-5 text-amber-700">
                                尚未找到可信的正文插入位置，智能修订已暂停，避免修改无关段落。
                              </p>
                            )}
                            {(issue.chat || []).map((entry, chatIndex) => (
                              <div key={chatIndex} className={`rounded px-2 py-1.5 text-[11px] leading-5 ${entry.role === "user" ? "bg-magic-500/10 text-magic-100" : "bg-white/50 text-pine-700"}`}>
                                {entry.content}
                              </div>
                            ))}
                            {issue.revision_diff && (
                              <button
                                type="button"
                                onClick={() => {
                                  setRevisionIssueId(issueId);
                                  setShowRevisionMarks(true);
                                  setShowIssueLocations(false);
                                }}
                                className="flex w-full items-center justify-between rounded border border-pine-200/70 bg-white/50 px-2 py-2 text-[11px] text-pine-700 transition-colors hover:border-magic-500/40 hover:bg-white/50"
                              >
                                <span className="flex items-center gap-1.5">
                                  <Diff className="h-3.5 w-3.5 text-magic-300" />
                                  查看修订痕迹
                                </span>
                                <span>
                                  <span className="text-emerald-300">+{issue.revision_diff.added_chars}</span>
                                  <span className="mx-1 text-pine-700">/</span>
                                  <span className="text-red-300">-{issue.revision_diff.deleted_chars}</span>
                                </span>
                              </button>
                            )}
                            {status !== "ignored" && (
                              <div className="flex gap-2">
                                <input
                                  value={issueMessages[issueId] || ""}
                                  onChange={(event) => setIssueMessages((current) => ({ ...current, [issueId]: event.target.value }))}
                                  placeholder="补充你的修订要求"
                                  className="min-w-0 flex-1 rounded border border-pine-200 bg-white/50 px-2 py-1.5 text-[11px] text-pine-700 outline-none focus:border-magic-500"
                                />
                                <button
                                  type="button"
                                  title="发送给修订智能体"
                                  disabled={repairBusy || !issue.issue_id || unsafeLocalization}
                                  onClick={() => repairIssue(issue)}
                                  className="rounded border border-magic-500/35 bg-magic-500/10 p-1.5 text-magic-300 hover:bg-magic-500/20 disabled:opacity-40"
                                >
                                  <Send className="h-3.5 w-3.5" />
                                </button>
                              </div>
                            )}
                            <div className="flex flex-wrap gap-2">
                              {issue.scope === 'advisory' ? (
                                <button
                                  type="button"
                                  onClick={() => ignoreIssue(issue)}
                                  className="flex items-center gap-1 rounded border border-pine-200 px-2 py-1 text-[11px] text-pine-700 hover:bg-white/50 disabled:opacity-40"
                                >
                                  <EyeOff className="h-3 w-3" />
                                  知道了
                                </button>
                              ) : issue.scope === 'scene_contract' ? (
                                <button
                                  type="button"
                                  disabled={repairBusy || status === "ignored" || !issue.issue_id || unsafeLocalization}
                                  onClick={() => repairIssue(issue, true)}
                                  className="flex items-center gap-1 rounded border border-magic-500/30 bg-magic-500/10 px-2 py-1 text-[11px] text-magic-300 hover:bg-magic-500/20 disabled:opacity-40"
                                >
                                  <Sparkles className="h-3 w-3" />
                                  {busy ? "调整中" : "调整场景合同"}
                                </button>
                              ) : issue.scope === 'outline_plan' ? (
                                <button
                                  type="button"
                                  disabled={repairBusy || status === "ignored" || !issue.issue_id || unsafeLocalization}
                                  onClick={() => repairIssue(issue, true)}
                                  className="flex items-center gap-1 rounded border border-sky-500/30 bg-sky-500/10 px-2 py-1 text-[11px] text-sky-300 hover:bg-sky-500/20 disabled:opacity-40"
                                >
                                  <Sparkles className="h-3 w-3" />
                                  {busy ? "查看中" : "查看大纲"}
                                </button>
                              ) : (
                                <button
                                  type="button"
                                  disabled={repairBusy || status === "ignored" || !issue.issue_id || unsafeLocalization}
                                  onClick={() => repairIssue(issue, true)}
                                  className="flex items-center gap-1 rounded border border-emerald-500/30 bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-300 hover:bg-emerald-500/20 disabled:opacity-40"
                                >
                                  <Sparkles className="h-3 w-3" />
                                  {busy ? "修订中" : "智能修订"}
                                </button>
                              )}
                              {issue.type === "ending_state_not_reached" && issue.scope !== 'advisory' && issue.scope !== 'scene_contract' && (
                                <>
                                  <button
                                    type="button"
                                    disabled={repairBusy || status === "ignored" || !issue.issue_id}
                                    onClick={() => repairIssue(issue, false, "append_ending")}
                                    className="flex items-center gap-1 rounded border border-sky-600 bg-sky-50 px-2 py-1 text-[11px] text-sky-700 hover:bg-sky-100 disabled:opacity-40"
                                  >
                                    <Sparkles className="h-3 w-3" />
                                    {busy ? "补写中" : "补写结尾"}
                                  </button>
                                  <button
                                    type="button"
                                    disabled={repairBusy || status === "ignored" || !issue.issue_id}
                                    onClick={() => repairIssue(issue, false, "allow_scene_rewrite")}
                                    className="flex items-center gap-1 rounded border border-purple-600 bg-purple-50 px-2 py-1 text-[11px] text-purple-700 hover:bg-purple-100 disabled:opacity-40"
                                  >
                                    <Sparkles className="h-3 w-3" />
                                    {busy ? "重构中" : "FBI 场景重构"}
                                  </button>
                                </>
                              )}
                              {issue.scope !== 'advisory' && (
                                <button
                                  type="button"
                                    disabled={repairBusy || status === "ignored" || !issue.issue_id}
                                  onClick={() => ignoreIssue(issue)}
                                  className="flex items-center gap-1 rounded border border-pine-200 px-2 py-1 text-[11px] text-pine-700 hover:bg-white/50 disabled:opacity-40"
                                >
                                  <EyeOff className="h-3 w-3" />
                                  忽略此问题
                                </button>
                              )}
                            </div>
                          </div>
                        )}
                      </article>
                    );
                  })}
                      </div>
                    </div>
                  )}

                  {/* 系统问题 */}
                  {classifiedIssues.system.length > 0 && (
                    <div className="mb-3">
                      <div className="mb-2 flex items-center gap-1.5 text-xs font-medium text-sky-300">
                        <Cpu className="h-3.5 w-3.5" />
                        系统问题 ({classifiedIssues.system.length})
                      </div>
                      <div className="space-y-2">
                        {classifiedIssues.system.map((issue, index) => (
                          <div key={`sys-${index}`} className="rounded-md border border-sky-500/25 bg-sky-500/5 px-3 py-2">
                            <p className="text-xs font-medium text-sky-200">{issueTypeLabel(issue.type)}</p>
                            <p className="mt-1 text-[11px] text-pine-700">{String(issue.detail || "系统错误，非正文问题")}</p>
                            <p className="mt-1 text-[10px] text-sky-700">
                              系统会自动复验；若同时存在正文问题，将在正文复检后继续，无需为此修改正文。
                            </p>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* 已解决问题（折叠） */}
                  {classifiedIssues.resolved.length > 0 && (
                    <details className="mb-3">
                      <summary className="mb-2 flex cursor-pointer items-center gap-1.5 text-xs font-medium text-emerald-300">
                        <ShieldCheck className="h-3.5 w-3.5" />
                        已解决 ({classifiedIssues.resolved.length})
                      </summary>
                      <div className="space-y-2">
                        {classifiedIssues.resolved.map((issue, index) => (
                          <div key={`resolved-${index}`} className="rounded-md border border-emerald-500/15 bg-emerald-500/5 px-3 py-2 opacity-70">
                            <p className="text-xs font-medium text-pine-700">{issueTypeLabel(issue.type)}</p>
                            <p className="mt-1 text-[11px] text-pine-700">{String(issue.detail || "")}</p>
                          </div>
                        ))}
                      </div>
                    </details>
                  )}

                  {/* 过期问题（折叠） */}
                  {classifiedIssues.stale.length > 0 && (
                    <details className="mb-4">
                      <summary className="mb-3 flex cursor-pointer items-center gap-1.5 text-xs font-bold text-pine-600">
                        <History className="h-4 w-4" />
                        旧审校结果 ({classifiedIssues.stale.length})
                      </summary>
                      <div className="space-y-3">
                        {classifiedIssues.stale.map((issue, index) => (
                          <div key={`stale-${index}`} className="rounded-xl border border-pine-200/60 bg-pine-50/50 px-4 py-3 opacity-60">
                            <p className="text-xs font-bold text-pine-900">{issueTypeLabel(issue.type)}</p>
                            <p className="mt-1 text-xs text-pine-700">{String(issue.detail || "基于旧版正文")}</p>
                          </div>
                        ))}
                      </div>
                    </details>
                  )}

                  {/* 无问题 */}
                  {classifiedIssues.current.length === 0 && classifiedIssues.system.length === 0 && (
                    <div className="rounded-lg border border-emerald-500/20 bg-emerald-500/5 p-4 text-center text-sm text-emerald-300">
                      <CheckCircle2 className="mx-auto mb-2 h-5 w-5" />
                      当前正文无待处理问题
                    </div>
                  )}
                </div>
              </section>
            </div>
            </div>
            {reviewError && (
              <p className="review-workbench__error px-6 py-3 text-xs" role="alert">
                {reviewError}
              </p>
            )}
            <div className="review-workbench__footer flex flex-col gap-4 px-6 py-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0 text-xs text-pine-700">
                <p className="flex items-center gap-2">
                  <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500" />
                  提交并复检会从当前候选稿继续，不会重新规划或整章生成。
                </p>
                <p id="review-workbench-override-help" className="mt-1 pl-6 text-[11px] text-pine-600">
                  直接采用仅跳过内容质量复检；事实/状态抽取与提交完整性检查仍会执行。
                </p>
              </div>
              <div className="review-workbench__actions flex flex-wrap justify-end gap-2">
                <button
                  type="button"
                  disabled={reviewSubmitting || repairBusy}
                  aria-busy={reviewSubmitting}
                  onClick={() => submitReview("retry")}
                  className="review-workbench__action review-workbench__action--secondary"
                >
                  基于当前稿重新修订
                </button>
                <button
                  type="button"
                  disabled={reviewSubmitting || repairBusy || !reviewText.trim()}
                  aria-busy={reviewSubmitting}
                  aria-describedby="review-workbench-override-help"
                  onClick={() => submitReview("accept_without_recheck")}
                  className="review-workbench__action review-workbench__action--override"
                >
                  直接采用，不再复检
                </button>
                <button
                  type="button"
                  disabled={reviewSubmitting || repairBusy || !reviewText.trim()}
                  aria-busy={reviewSubmitting}
                  onClick={() => submitReview("accept_edited")}
                  className="review-workbench__action review-workbench__action--primary"
                >
                  提交并复检
                </button>
              </div>
            </div>
          </div>
        </div>
      , document.body)}

      {review && isSystemReview && createPortal(
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 p-4">
          <div className="flex max-h-[88vh] w-full max-w-lg flex-col rounded-lg border border-red-500/30 bg-white/50 shadow-2xl">
            <div className="border-b border-pine-200 px-4 py-3">
              <h3 className="text-sm font-semibold text-red-300">自动审校暂时未完成</h3>
              <p className="mt-1 text-xs leading-5 text-pine-700">
                正文尚未自动提交。你可以立即重新复验，或提交当前稿进入下一轮检查。
              </p>
            </div>
            <div className="space-y-3 overflow-auto p-4">
              <div className="rounded border border-pine-200/50 bg-white/50 px-3 py-2 text-xs text-pine-700">
                系统已经尝试复验 {review.retry_count ?? review.attempts.length} 次，仍未获得稳定结果。
              </div>
              {reviewError && <p className="text-xs text-red-400">{reviewError}</p>}
            </div>
            <div className="flex justify-end gap-2 border-t border-pine-200 px-4 py-3">
              <button
                type="button"
                disabled={reviewSubmitting}
                onClick={() => submitReview("retry")}
                className="rounded border border-red-500/40 px-3 py-1.5 text-xs text-red-300 hover:bg-red-500/10 disabled:opacity-50"
              >
                立即重新复验
              </button>
              <button
                type="button"
                disabled={reviewSubmitting}
                onClick={() => submitReview("accept_edited")}
                className="rounded bg-white/50 px-3 py-1.5 text-xs font-medium text-pine-700 hover:bg-white/50 disabled:opacity-50"
              >
                提交当前稿并复检
              </button>
            </div>
          </div>
        </div>
      , document.body)}

      {baselineConflict.length > 0 && createPortal(
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 p-4">
          <div className="flex w-full max-w-lg flex-col rounded-lg border border-orange-500/30 bg-white/50 shadow-2xl">
            <div className="border-b border-pine-200 px-4 py-3">
              <h3 className="text-sm font-semibold text-orange-300">大纲与故事状态冲突</h3>
              <p className="mt-1 text-xs text-pine-700">当前故事状态与新大纲不一致，需要重置后重新生成</p>
            </div>
            <div className="space-y-2 overflow-auto p-4">
              {baselineConflict.map((conflict, index) => (
                <div key={index} className="rounded border border-orange-500/20 bg-orange-500/5 px-3 py-2 text-xs text-orange-200">
                  {conflict}
                </div>
              ))}
            </div>
            <div className="flex justify-end gap-2 border-t border-pine-200 px-4 py-3">
              <button
                type="button"
                onClick={() => setBaselineConflict([])}
                className="rounded border border-pine-200 px-3 py-1.5 text-xs text-pine-700 hover:bg-white/50"
              >
                取消
              </button>
              <button
                type="button"
                disabled={regenSubmitting}
                onClick={async () => {
                  setRegenSubmitting(true);
                  try {
                    const chapterNum = Number(detail?.result_context?.chapter_number) || 1;
                    await regenerateFromOutline(projectId, chapterNum);
                    setBaselineConflict([]);
                    await loadExecutions();
                  } catch (err: any) {
                    setBaselineConflict([`重置失败: ${err?.message || "未知错误"}`]);
                  } finally {
                    setRegenSubmitting(false);
                  }
                }}
                className="rounded bg-orange-500 px-3 py-1.5 text-xs font-medium text-black hover:bg-orange-400 disabled:opacity-50"
              >
                {regenSubmitting ? "重置中..." : "按新大纲重置并重新生成"}
              </button>
            </div>
          </div>
        </div>
      , document.body)}
    </div>
  );
}
