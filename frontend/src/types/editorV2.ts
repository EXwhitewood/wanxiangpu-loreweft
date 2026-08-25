/**
 * 主编系统 V2 类型定义
 * Phase 0-13 重构后的统一类型
 */

// ---- Phase 0: Editor Trace ----

export interface PreGenerationMetrics {
  decision_context_token_estimate: number;
  chapter_plan_contract_item_count: number;
  scene_contract_item_counts: number[];
  per_scene_must_show_counts: number[];
  per_scene_forbidden_counts: number[];
  per_scene_writer_input_char_counts: number[];
  per_scene_writer_visible_ledger_items: number[];
}
export interface PostGenerationMetrics {
  text_char_count: number;
  explanation_density: number;
  dash_count: number;
  new_term_count: number;
  definition_sentence_count: number;
  quality_finding_count: number;
  fbi_auto_repair_rounds: number;
  fbi_patches_per_round: number[];
  fbi_empty_response_count: number;
  fbi_no_op_count: number;
  final_human_workbench: boolean;
}

export interface PhaseTraceEntry {
  phase: 'start' | 'handler_registered' | 'executed' | 'skipped' | 'timeout' | 'failed' | string;
  time: number;
  ts?: string;
  reason?: string;
  error?: string;
  error_type?: string;
  resume?: boolean;
}

export interface LayerTrace {
  layer: string;
  status: 'ok' | 'degraded' | 'failed' | 'skipped';
  duration_ms: number;
  detail: string;
  error: string;
  // 方案16：DAG 可观测性字段
  node_traces?: Record<string, PhaseTraceEntry[]>;
  phase_trace?: PhaseTraceEntry[];
  skip_reason?: string;
}

export interface SceneWriterInputSnapshot {
  scene_index: number;
  v2_used: boolean;
  must_include: string[];
  must_avoid: string[];
  active_capabilities: string[];
  activation_reasons: Record<string, string>;
  source_trace: Record<string, string[]>;
  writer_input_enrichment: Record<string, unknown>;
  hard_max_chars: number;
  target_chars: number;
  ending_state: string;
  soft_hints: string[];
  visible_facts: string[];
  degraded_items: string[];
  budget_check: Record<string, unknown>;
}

export interface SceneRepairOutcome {
  scene_index: number;
  v2_status: 'v2_resolved' | 'v2_degraded' | 'v2_failed' | 'legacy_fallback' | 'skipped';
  total_orders: number;
  succeeded_orders: number;
  failed_orders: number;
  needs_human: string[];
  legacy_fallback: boolean;
  failure_reasons: string[];
  text_hash_before: string;
  text_hash_after: string;
}

export interface EditorTrace {
  trace_id: string;
  project_id: string;
  chapter_number: number;
  execution_id: string;
  created_at: string;
  pre_metrics: PreGenerationMetrics;
  post_metrics: PostGenerationMetrics;
  layers: LayerTrace[];
  final_status: 'completed' | 'degraded' | 'failed' | 'cancelled';
  final_error: string;
  // V2 诊断增强
  v2_compile_status: 'ok' | 'degraded' | 'failed' | 'skipped';
  v2_compile_error: string;
  writer_input_snapshots: SceneWriterInputSnapshot[];
  repair_outcomes: SceneRepairOutcome[];
  scene_text_hashes: string[];
}

// ---- 生效证明面板数据 ----

export interface EffectivenessProof {
  trace_id?: string;
  final_status?: string;
  workflow_status?: string;
  review_version?: number;
  v2_compile_status?: 'ok' | 'degraded' | 'failed' | 'skipped' | string;
  v2_compile_error?: string;
  v2_input_used: boolean;
  must_include_count: number;
  forbidden_count: number;
  active_capabilities?: string[];
  writer_input_source_trace?: Record<string, string[]>;
  writer_input_snapshots?: SceneWriterInputSnapshot[];
  hard_max_chars: number;
  actual_chars: number;
  fbi_v2_engaged: boolean;
  repair_orders_total: number;
  repair_orders_succeeded: number;
  repair_orders_failed: number;
  failure_reasons: string[];
  legacy_fallback: boolean;
  issues_based_on_current_text: boolean;
  current_text_hash: string;
}

// ---- Phase 2: ContractItem ----

export interface ContractItem {
  id: string;
  text: string;
  source: string;
  source_ref: string | null;
  layer: string;
  obligation_type: string;
  priority: 'critical' | 'high' | 'medium' | 'low';
  certainty: string;
  visibility: string;
  scene_scope: string[];
  due_scene_id: string | null;
  word_cost: number;
  explanation_risk: number;
  conflict_group: string | null;
  status: string;
  evidence: string[];
}

export interface InformationBudget {
  target_chars: number;
  hard_max_chars: number;
  max_current_scene_must: number;
  max_soft_hints: number;
  max_new_named_entities: number;
  max_new_terms: number;
  max_definition_sentences_per_1000_chars: number;
  max_explanation_markers_per_1000_chars: number;
  max_foreshadowing_ops: number;
  max_reader_questions_opened: number;
}

export interface CompiledSceneContract {
  scene_id: string;
  chapter_id: string;
  pov_character: string | null;
  scene_function: string;
  target_emotion: string;
  target_chars: number;
  hard_facts: ContractItem[];
  current_scene_must: ContractItem[];
  soft_hints: ContractItem[];
  forbidden: ContractItem[];
  ending_state: string;
  style_policy: Record<string, unknown>;
  pacing_policy: Record<string, unknown>;
  information_budget: InformationBudget;
  hidden_carry_forward: ContractItem[];
  quality_check_only: ContractItem[];
}

export interface WriterInputPacket {
  system_rules: string[];
  scene_task: string;
  visible_facts: string[];
  must_include: string[];
  soft_suggestions: string[];
  must_avoid: string[];
  ending_state: string;
  style_instruction: string;
  pacing_instruction: string;
  output_constraints: Record<string, unknown>;
  active_capabilities: string[];
  activation_reasons: Record<string, string>;
  source_trace: Record<string, string[]>;
  writer_input_enrichment: Record<string, unknown>;
}

// ---- Phase 7: ReviewFindingV2 ----

export interface ReviewFindingV2 {
  id: string;
  source: string;
  type: string;
  title: string;
  description: string;
  severity: 'S1' | 'S2' | 'S3' | 'S4';
  blocks_commit: boolean;
  user_visible: boolean;
  repair_scope: string;
  repair_lane: string;
  evidence_spans: Array<Record<string, unknown>>;
  validator: string;
  retryable: boolean;
  max_attempts: number;
}

// ---- Phase 8: RepairOrder ----

export interface RepairOrder {
  id: string;
  issue_ids: string[];
  lane: string;
  target_scope: string;
  target_region: Record<string, unknown> | null;
  operation: string;
  instruction: string;
  allowed_delta_chars: number;
  protected_spans: Array<Record<string, unknown>>;
  validator: string;
  max_attempts: number;
  status: 'pending' | 'running' | 'succeeded' | 'failed' | 'skipped' | 'needs_human';
  attempts: number;
  result: string;
  patches: Array<Record<string, unknown>>;
}
