export interface Project {
  id: string;
  name: string;
  description: string;
  genre: string;
  word_count_target: number;
  created_at: string;
  updated_at: string;
  current_chapter: number;
  total_words: number;
  outline_data?: Record<string, unknown>;
  core_data?: Record<string, unknown>;
}

export type GenreProfileId =
  | "general"
  | "xianxia"
  | "mystery"
  | "romance"
  | "scifi"
  | "historical";

export interface ProjectCreate {
  name: string;
  description?: string;
  genre?: string;
  genre_profile_id?: GenreProfileId;
  word_count_target?: number;
}

export interface Character {
  id: string;
  project_id: string;
  name: string;
  aliases: string[];
  description: string;
  appearance: string;
  personality: string;
  desire: string;
  deep_need: string;
  arc: string;
  relationships: Record<string, string>;
  role: string;
  faction: string;
  status: string;
  lifecycle_status: "alive" | "deceased" | "missing" | "transformed" | "unknown";
  narrative_activity: "core_active" | "scene_active" | "dormant" | "archived";
  role_importance: "protagonist" | "main" | "supporting" | "episodic" | "background";
  first_seen_chapter?: number | null;
  last_seen_chapter?: number | null;
  last_mentioned_chapter?: number | null;
  next_planned_chapter?: number | null;
  active_arc_ids: string[];
  voice_profile?: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface SceneBeat {
  goal: string;
  conflict: string;
  outcome: string;
  info_release: string;
  hook: string;
}

export interface ChapterOutline {
  chapter_number: number;
  title: string;
  main_conflict: string;
  value_shift: string;
  pov_character?: string;
  thread_ops?: Array<{ op: string; thread_id: string; mode?: string }>;
  scenes: SceneBeat[];
  status: "draft" | "planned" | "needs_review" | "frozen";
  recovery_generated?: boolean;
}

export interface ChapterListItem {
  chapter_number: number;
  title: string;
  status: string;
  word_count: number;
}

export interface Chapter {
  chapter_number: number;
  title: string;
  content: string;
  status: string;
  created_at?: string;
  updated_at?: string;
}

export interface ChapterSettlementStatus {
  job_id: string;
  chapter_number: number;
  chapter_revision_hash: string;
  status: "pending" | "running" | "completed" | "retryable_failed" | "degraded" | "superseded";
  phase: string;
  stages: Record<string, { status: string; [key: string]: unknown }>;
  attempts: number;
  error: string;
  created_at?: string;
  updated_at?: string;
  completed_at?: string | null;
}

export interface ChapterSaveResponse extends Chapter {
  settlement?: ChapterSettlementStatus | null;
}

export type WritingAssistanceDimension =
  | "fact"
  | "character"
  | "timeline"
  | "world_rule"
  | "pov_knowledge"
  | "foreshadowing"
  | "outline_deviation"
  | "pacing"
  | "clarity"
  | "character_expression"
  | "dialogue"
  | "hook"
  | "prose_style";

export type ChapterAdvisoryTrigger =
  | "manual"
  | "save"
  | "ai_edit_before"
  | "ai_edit_after";

export type AIEditPermissionMode =
  | "proposal_only"
  | "ask_every_time"
  | "chapter_session";

export interface WritingAssistanceSettings {
  consistency_reminders: {
    enabled: boolean;
    check_on_save: boolean;
    dimensions: WritingAssistanceDimension[];
  };
  ai_edit_permission: {
    mode: AIEditPermissionMode;
  };
}

export interface ChapterAdvisoryFinding {
  finding_id: string;
  fingerprint: string;
  revision_hash: string;
  category: WritingAssistanceDimension;
  severity: "high" | "medium" | "low";
  title: string;
  detail: string;
  evidence_quote: string;
  source: {
    source_type: string;
    label: string;
    excerpt: string;
  };
  confidence: number;
  suggested_actions: Array<
    | "ignore"
    | "intentional_exception"
    | "update_source"
    | "ask_ai"
    | "authorize_edit"
  >;
  blocks_commit: false;
  repair_scope: "advisory";
  repair_lane: "none";
  diagnostic_id?: string | null;
  status?: string;
  available_actions?: string[];
  affected_domains?: string[];
}

export interface ChapterDiagnosticRecord {
  diagnostic_id: string;
  project_id: string;
  chapter_number: number;
  chapter_revision_hash: string;
  fingerprint: string;
  category: WritingAssistanceDimension | string;
  severity: "high" | "medium" | "low" | string;
  title: string;
  detail: string;
  evidence_quote: string;
  source: {
    source_type?: string;
    label?: string;
    excerpt?: string;
    [key: string]: unknown;
  };
  confidence: number;
  reference_available?: boolean;
  affected_domains: string[];
  available_actions: string[];
  status: string;
  decision_by?: string;
  decision_reason?: string;
  decision_payload?: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
  decided_at?: string | null;
}

export interface ChapterAdvisoryCheckResponse {
  status: "complete" | "degraded" | "disabled";
  trigger: ChapterAdvisoryTrigger;
  revision_hash: string;
  checked_dimensions: WritingAssistanceDimension[];
  findings: ChapterAdvisoryFinding[];
  diagnostics: Array<{
    code: string;
    message: string;
    level: "info" | "warning";
  }>;
  blocks_commit: false;
}

export interface EntityState {
  location: string | null;
  emotional_state: string | null;
  physical_state: string | null;
  inventory: string[];
  alive: boolean;
  extra: Record<string, unknown>;
}

export interface LocationState {
  atmosphere: string | null;
  light_source: string | null;
  objects_present: string[];
  condition: string | null;
}

export interface SubjectiveView {
  believed_state: Record<string, unknown>;
  last_known: Record<string, unknown>;
}

export interface StoryState {
  timeline_id: string;
  narrative_time: string | null;
  active_chapter: number;
  active_scene: number;
  pov_character: string | null;
  objective_state: Record<string, EntityState | LocationState>;
  subjective_views: Record<string, SubjectiveView>;
}

export type APIFormat = "openai_compatible" | "anthropic_compatible";

export interface APIConfig {
  api_format: APIFormat;
  api_key: string;
  base_url: string;
  model: string;
}

export interface GlobalSettings {
  openai_compatible: APIConfig | null;
  anthropic_compatible: APIConfig | null;
}

export interface AgentOverride {
  api_format: APIFormat | null;
  api_key: string | null;
  base_url: string | null;
  model: string | null;
  skills: string[] | null;
  agent_skills: string[] | null;
  persona: string | null;
}

export interface SkillInfo {
  name: string;
  display_name: string;
  description: string;
  detail?: string;
  category: "utility" | "agent" | "governance" | string;
  agent?: string;
  agents?: string[];
  kind?: SkillKind;
  domain?: string;
  source?: SkillSource;
  format_version?: string;
  source_format?: string;
  enabled_by_default?: boolean;
  validators?: string[];
  has_validators?: boolean;
  has_constraints?: boolean;
  has_repair_hooks?: boolean;
  has_repair_strategies?: boolean;
}

export interface AgentDetail {
  name: string;
  display_name: string;
  description: string;
  icon: string;
  default_skills: string[];
  enabled_skills: string[];
  agent_skills: string[];
  enabled_agent_skills: string[];
  persona: string;
  active_persona: string;
  config: APIConfig;
  has_override: boolean;
}

export interface AppSettings {
  global: GlobalSettings;
  agent_overrides: Record<string, AgentOverride>;
}

export interface SystemInfo {
  app_version: string;
  app_mode: "standard" | string;
  configured_app_mode: "standard" | string;
  is_standard_mode: boolean;
  core_backend: string;
  shell_backend: string;
  workflow_backend: string;
  configured_core_backend: string;
  configured_shell_backend: string;
  configured_workflow_backend: string;
  database_url: string;
  configured_database_url: string;
  restart_required: boolean;
  is_tauri: boolean;
  message?: string;
}

export interface AgentConfigItem {
  name: string;
  config: AgentOverride;
}

// ====================================================================
// 娱乐设置（等待娱乐功能：跳转外部平台 + 经典小游戏）
// ====================================================================
// 与后端 backend/app/models/entertainment.py 一一对应
// 通过 /api/settings/entertainment 端点读写
// --------------------------------------------------------------------

/** 链接分类：视频/音乐/小说/自定义 */
export type LinkCategory = "video" | "music" | "novel" | "custom";

/** 单条娱乐跳转链接 */
export interface EntertainmentLink {
  name: string;                   // 显示名称，如"B站"
  url: string;                    // 完整 URL，如"https://www.bilibili.com"
  category: LinkCategory;         // 分类，用于分组显示
  icon?: string | null;           // 可选 emoji 图标
  enabled: boolean;               // 是否启用
}

/** 娱乐功能完整配置 */
export interface EntertainmentSettings {
  enabled: boolean;                       // 总开关
  show_trigger_button: boolean;           // 是否在工作流启动时显示娱乐邀请
  auto_open_on_workflow_wait: boolean;    // 工作流进入等待态时自动弹出
  notify_on_workflow_complete: boolean;   // 工作流完成时弱提醒
  links: EntertainmentLink[];             // 跳转链接列表
  enabled_games: string[];                // 启用的小游戏 ID 列表（如 ["snake", "2048"]）
}

export interface GenerationRequest {
  chapter_number: number;
  scene_number?: number | null;
  pov_character?: string | null;
  custom_instructions?: string | null;
}

export interface GenerationResponse {
  chapter_number: number;
  scene_number: number;
  content: string;
  state_patch: Record<string, unknown> | null;
  consistency_report: Record<string, unknown> | null;
  trace_id?: string | null;
  advisory_summary?: Record<string, unknown> | null;
}

export interface GenerationTrace {
  id: string;
  project_id: string;
  chapter_number: number;
  scene_index: number;
  trace_version: number;
  mode: string;
  status: string;
  context_manifest: Record<string, unknown>;
  prompt_preview: Record<string, unknown>;
  quality_summary: Record<string, unknown>;
  recovery_summary: Record<string, unknown>;
  created_at?: string | null;
  expires_at?: string | null;
}

export interface EntityProgression {
  id: string;
  project_id: string;
  entity_type: string;
  entity_id: string;
  effective_chapter: number;
  effective_scene: number;
  change_type: string;
  before_value: Record<string, unknown>;
  after_value: Record<string, unknown>;
  evidence_text: string;
  source: string;
  status: string;
  schema_version: number;
  fingerprint?: string | null;
  created_at?: string | null;
}

export interface ProjectHealth {
  schema_version: number;
  project_id: string;
  status: string;
  summary: Record<string, number>;
  quality_memory?: {
    recent_quality_patterns?: Array<Record<string, unknown>>;
    mode_fit_history?: Array<Record<string, unknown>>;
  };
  signals: Array<{ name: string; value: number; level: string }>;
}

export type FeatureMode = "off" | "shadow" | "report" | "assist" | "enforce";

export interface GenerationFeaturePolicy {
  schema_version: number;
  ai_flavor_mode: FeatureMode;
  reader_experience_mode: FeatureMode;
  concept_budget_mode: "off" | "shadow" | "report" | "enforce";
  character_voice_mode: FeatureMode;
  prompt_trace_mode: "off" | "metadata" | "full" | "full_debug_when_failed";
  mention_detector_mode: "off" | "shadow" | "candidate";
  progression_mode: "off" | "read" | "candidate" | "active";
  generation_mode: "legacy" | "quick" | "standard" | "deep";
  proposition_extraction_mode: "off" | "shadow" | "report" | "enforce";
  proposition_audit_mode: "off" | "shadow" | "report" | "enforce";
  hard_correctness_mode: "off" | "shadow" | "report" | "enforce";
  narrative_contract_mode: "off" | "shadow" | "report" | "enforce";
  style_quality_mode: "off" | "shadow" | "report" | "assist";
  writing_mode_profile_id: string;
  writing_mode_profile_mode: FeatureMode;
  narrative_experience_mode: FeatureMode;
  literary_quality_mode: FeatureMode;
  commercial_pacing_mode: FeatureMode;
  commercial_pacing_profile_id: string;
  commercial_pacing_revision_mode: "off" | "local_patch" | "scene_rewrite";
  reader_corpus_mode: FeatureMode;
  reader_corpus_profile_id: string;
  scene_credibility_mode: FeatureMode;
  mode_fit_mode: FeatureMode;
  style_experience_conflict_mode: FeatureMode;
  experience_revision_mode: "off" | "local_patch" | "scene_rewrite" | "full";
  enabled_by?: string;
  notes?: string[];
}

export interface WritingModeProfile {
  schema_version: number;
  id: string;
  label: string;
  description: string;
  target_reader: string;
  metric_weights: Record<string, number>;
  experience_defaults: Record<string, unknown>;
  literary_quality_defaults: Record<string, unknown>;
  style_interaction: Record<string, unknown>;
  revision_policy: Record<string, unknown>;
  prompt_guidance: string[];
}

export interface WritingModeListResponse {
  profiles: WritingModeProfile[];
  active_profile_id: string;
}

export interface ReaderCorpusSummary {
  source_count: number;
  chapter_count: number;
  pattern_count: number;
  genres: Record<string, number>;
  pattern_types: Record<string, number>;
}

export interface ReaderCorpusSource {
  id: string;
  title: string;
  author: string;
  genre: string;
  platform: string;
  quality_tier: string;
  status: string;
  word_count: number;
  chapter_count: number;
  tags: string[];
  allowed_uses: string[];
  notes: string;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ReaderExperiencePattern {
  id: string;
  source_id: string;
  chapter_id: string;
  genre: string;
  chapter_position: string;
  scene_position: string;
  pattern_type: string;
  pattern_key: string;
  score: number;
  evidence_count: number;
  metrics: Record<string, unknown>;
  guidance: Record<string, unknown>;
  tags: string[];
  created_at?: string | null;
}

export interface ReaderExperienceGuidance {
  schema_version: number;
  mode: string;
  genre: string;
  chapter_position: string;
  scene_position: string;
  guidance: string[];
  target_metrics: Record<string, number>;
  avoid_patterns: string[];
  pattern_ids: string[];
  source_count: number;
  token_budget_chars: number;
}

export interface ExperienceQualityResponse {
  chapter_number: number;
  reports: Array<Record<string, unknown>>;
  quality_memory: Record<string, unknown>;
}

export interface RevisionHint {
  target_span: string;
  problem: string;
  strategy: string;
  preserve: string[];
  auto_revise_allowed: boolean;
  severity_level: string;
  source_mode: string;
  needs_user_confirm: boolean;
}

export interface AIQualityCoordinatorReport {
  risk_summary: string;
  coordinator_mode: "off" | "shadow" | "report" | "assist" | "enforce";
  quality_guidance_patch?: Record<string, unknown>;
  revision_hints?: RevisionHint[];
}

export interface ChapterGenerationResponse {
  chapter_number: number;
  content: string;
  word_count?: number;
  total_scenes: number;
  detail_seeds_count: number;
  consistency_reports: Record<string, unknown>[];
}

export interface TestConnectionResult {
  success: boolean;
  message: string;
  detail?: string;
}

export interface OutlineChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface OutlineChatResponse {
  response: string;
}

export interface OutlineSaveResponse {
  message: string;
  outline_data: Record<string, unknown>;
}

export interface OutlineParseResponse {
  message: string;
  outline_data: Record<string, unknown>;
  change_records?: number;
}

export interface WorldRule {
  id: string;
  category: string;
  name: string;
  description: string;
  constraints: string[];
  priority: "critical" | "high" | "normal" | "low";
  locked: boolean;
  created_at: string;
  updated_at: string;
}

export interface StyleSourceBook {
  id: string;
  filename: string;
  word_count: number;
  uploaded_at: string;
}

export interface StyleFeatures {
  vocabulary: string;
  sentence_structure: string;
  tone: string;
  pacing: string;
  description_style: string;
  dialogue_style: string;
  narrative_voice: string;
  signature_phrases: string[];
  avoid_patterns: string[];
}

export interface StyleSamplePassage {
  id: string;
  text: string;
  category: string;
  source_book: string;
}

export interface StyleEmbedding {
  emotionality: number;
  sentence_complexity: number;
  narrative_distance: number;
  info_density: number;
  dialogue_ratio: number;
  description_density: number;
  rhythm_steepness: number;
  narrator_intrusion: number;
}

export interface StyleStatistics {
  global: {
    total_chars: number;
    paragraph_count: number;
    chapter_count: number;
    word_freq_top: Array<{ word: string; count: number }>;
    sentence_length: { mean: number; median: number; stdev: number };
    paragraph_length: { mean: number; median: number; stdev: number };
    punctuation_profile: Record<string, number>;
    [key: string]: unknown;
  };
  chapter_curves: Record<string, Record<string, unknown>>;
  scene_distribution: Record<string, number>;
  evolution: StyleEvolutionReport;
}

export interface StyleEvolutionReport {
  change_points: number[];
  style_clusters: Array<Record<string, unknown>>;
  is_multi_style: boolean;
  sub_profiles: Array<Record<string, unknown>>;
  explanation: string;
  recommended_usage: string;
}

export interface PersonaCard {
  identity: string;
  decision_pattern: string;
  expression_style: string;
  interpersonal_behavior: string;
  hard_rules: string[];
}

export interface StyleLearningProgress {
  current_step_index: number;
  current_step_key: string;
  message: string;
  total_steps: number;
}

export interface StyleProfile {
  id: string;
  name: string;
  source_books: StyleSourceBook[];
  style_features: StyleFeatures;
  style_embedding: StyleEmbedding;
  persona_card: PersonaCard;
  sample_passages: StyleSamplePassage[];
  style_prompt: string;
  style_statistics: StyleStatistics;
  evolution_report: StyleEvolutionReport;
  confidence: number;
  version: number;
  active: boolean;
  status: "learning" | "ready" | "failed";
  domains: string[];
  parent_profile_ids: string[];
  frozen: boolean;
  editor_influence_enabled?: boolean;
  learning_progress: StyleLearningProgress | null;
  created_at: string;
  updated_at: string;
}

export interface StyleConflict {
  dimension: string;
  dimension_label: string;
  value_a: number;
  value_b: number;
  delta: number;
  level: "L1" | "L2" | "L3" | "L4";
  type: "numeric" | "rule";
  rule_conflict?: { rule_a: string; rule_b: string };
}

export interface StyleConflictReport {
  profile_a_id: string;
  profile_a_name: string;
  profile_b_id: string;
  profile_b_name: string;
  conflicts: StyleConflict[];
  compatibility_score: number;
}

export interface StyleFusionRequest {
  source_profile_ids: string[];
  weights: number[];
  mode: "isolation" | "negotiation" | "arbitration" | "supervision";
  dominant_profile_id?: string;
  negotiation_targets?: Partial<StyleEmbedding>;
  domains?: Record<string, string>;
}

export interface StyleFusionResult {
  profile: StyleProfile;
  sample_text: string;
  similarity_description: string;
}

export interface WorldbuildingOverview {
  total_rules: number;
  total_characters: number;
  total_locations: number;
  total_foreshadowing: number;
  total_promotions: number;
  total_styles: number;
  active_style: string | null;
  promotion_status: Record<string, number>;
  foreshadowing_status: Record<string, number>;
  rule_categories: Record<string, number>;
  seed_stats: Record<string, number>;
  rules: WorldRule[];
  characters: Character[];
  locations: Location[];
  foreshadowing: ForeshadowingLine[];
  observations: WorldviewObservationSummary;
}

export interface ForeshadowingLine {
  id: string;
  project_id: string;
  name: string;
  version: number;
  status: "planned" | "active" | "dormant" | "revealing" | "resolved" | "revised" | "aborted";
  priority: "minor" | "moderate" | "major" | "structural";
  secret_canonical_statement: string;
  secret_truth_type: string;
  secret_impact_level: string;
  secret_spoiler_scope: Record<string, string[]>;
  bury_window_start: number | null;
  bury_window_end: number | null;
  reveal_window_start: number | null;
  reveal_window_end: number | null;
  total_clues_planned: number;
  clues_placed: number;
  bury_rhythm: string;
  complexity_score: number;
  reader_fairness_level: string;
  salience: string;
  reveal_readiness_score: number;
  owner_agent: string;
  revision_count: number;
  created_at: string;
  updated_at: string;
}

export interface ForeshadowingClue {
  id: string;
  project_id: string;
  foreshadowing_line_id: string;
  clue_type: "supportive" | "distractive" | "contradictory" | "missing";
  chapter_number: number;
  scene_index: number;
  clue_text: string;
  pov_character: string;
  salience_at_time: string;
  is_revealed_clue: boolean;
  revealed_in_chapter: number | null;
  created_at: string;
}

export interface CharacterCognitiveState {
  id: string;
  foreshadowing_line_id: string;
  character_name: string;
  cognitive_level: string;
  cognitive_status: string;
  is_intentionally_misled: boolean;
  last_knowledge_update_chapter: number | null;
  last_knowledge_update_event: string;
}

export interface CausalEdge {
  id: string;
  source_id: string;
  target_id: string;
  edge_type: "causes" | "reveals" | "obscures" | "depends_on" | "conflicts_with" | "synergizes_with";
  weight: number;
  narrative_justification: string;
}

export interface ForeshadowingDashboard {
  network: { nodes: ForeshadowingLine[]; edges: CausalEdge[] };
  cognitive_distribution: Record<string, number>;
  recycle_progress: Record<string, number>;
  conflicts: Array<Record<string, unknown>>;
  reveal_readiness_ranking: Array<{
    id: string;
    name: string;
    readiness: number;
    ready: boolean;
  }>;
}

export interface ForeshadowingViolation {
  rule_id: string;
  severity: "Critical" | "High" | "Medium" | "Low";
  description: string;
  matched_text: string;
  suggestion: string;
  chapter_number: number;
}

export interface RevealReadiness {
  readiness: number;
  components: {
    evidence_coverage: number;
    causal_precondition: number;
    knowledge_alignment: number;
    reader_fairness: number;
    contradiction_absence: number;
  };
  ready: boolean;
  recommendation: string;
}

export interface ForeshadowingBudget {
  active_count: number;
  chapter_clue_count: number;
  clue_density: number;
  within_budget: boolean;
  warnings: string[];
}

export interface Location {
  id: string;
  name: string;
  description: string;
  atmosphere: string;
  parent_location?: string;
  location_type?: string;
  function?: string;
  rules?: string[];
  first_seen_chapter?: number;
  source?: string;
  spatial_relations?: Record<string, string>;
}

export interface OutlineStatus {
  frozen: boolean;
  frozen_at: string | null;
  version: number;
  has_outline: boolean;
  has_draft: boolean;
  generated_at?: string | null;
  chapter_continuity?: {
    valid: boolean;
    source: string;
    chapter_count: number;
    max_chapter: number;
    numbers: number[];
    missing: number[];
    duplicates: number[];
    invalid_entries: Array<{ index: number; value: unknown }>;
  };
}

export interface OutlineExpansionStatus {
  has_job: boolean;
  job_id?: string;
  status:
    | "idle"
    | "started"
    | "already_running"
    | "queued"
    | "running"
    | "completing"
    | "paused"
    | "failed"
    | "cancelled"
    | "completed"
    | "already_complete"
    | "resumed"
    | string;
  target_chapters?: number;
  batch_size?: number;
  completed_chapters?: number;
  next_chapter?: number;
  actual_chapters: number;
  progress?: number;
  error?: string | null;
  message?: string | null;
  started_at?: string | null;
  updated_at?: string | null;
  live?: boolean;
  draft_saved?: boolean;
}

export interface TimelineItem {
  id: string;
  name: string;
  parent: string | null;
  branch_chapter: number | null;
  is_active?: boolean;
}

export interface GenerationHistoryEntry {
  type: string;
  chapter: number;
  timestamp: string;
  word_count: number;
  report_summary: {
    pass: boolean;
    auto_repairs: number;
    repair_applied: boolean;
  };
}

export interface PromotionProposal {
  id: string;
  seed_id: string;
  seed_content: string;
  reference_count: number;
  target_type: "character" | "world_rule" | "location";
  target_data: Record<string, unknown>;
  status: "pending" | "approved" | "rejected" | "conflict";
  conflict_info: string;
  created_at: string;
  updated_at: string;
}

export interface WorldviewObservation {
  id: string;
  entity_type: "character" | "location" | "world_rule" | "foreshadowing" | "fact";
  entity_name: string;
  entity_name_normalized: string;
  operation: "new" | "supplement" | "mention" | "reveal" | "conflict";
  payload?: Record<string, unknown>;
  confidence: number;
  evidence_text: string;
  chapter_number: number;
  scene_index: number | null;
  status: "active" | "retracted" | "merged" | "rejected" | "promoted";
  auto_promoted: boolean;
  core_entity_id: string | null;
  confirmed_by: string | null;
  confirmed_at: string | null;
  orphan_warning: boolean;
  generation_revision: number;
  extraction_version: number;
  extraction_source: string;
  created_at: string;
}

export type WorldviewObservationReviewState = "all" | "pending" | "auto" | "manual" | "confirmed";

export interface WorldviewObservationListParams {
  entityType?: string;
  status?: string;
  reviewState?: WorldviewObservationReviewState;
  page?: number;
  pageSize?: number;
}

export interface WorldviewObservationReviewCounts {
  all: number;
  pending: number;
  auto: number;
  manual: number;
  confirmed: number;
}

export interface WorldviewObservationList {
  items: WorldviewObservation[];
  total: number;
  by_type: Record<string, number>;
  by_status: Record<string, number>;
  by_review_state: WorldviewObservationReviewCounts;
  page: number;
  page_size: number | null;
  total_pages: number;
  has_more: boolean;
}

export interface WorldviewObservationSummary {
  total: number;
  by_type: Record<string, number>;
  by_status: Record<string, number>;
  pending_review: number;
}

export interface ChatSessionItem {
  session_id: string;
  agent_type: string;
  title: string;
  chapter_number: number | null;
  is_pinned: boolean;
  summary: string;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface ChatMessageItem {
  id?: string;
  role: "user" | "assistant" | "system";
  content: string;
  created_at?: string;
}

export interface CoreConflict {
  desire: string;
  obstacle: string;
  action: string;
  turn: string;
}

export interface ValueShift {
  axis: string;
  from: string;
  to: string;
}

export interface ThreadOp {
  thread_id: string;
  op: "plant" | "escalate" | "remind" | "reveal" | "abandon";
  mode: "subtle" | "moderate" | "obvious";
}

export interface ChapterSpineItem {
  chapter_id: string;
  chapter_number: number;
  title: string;
  node_id: string;
  function: "setup" | "escalation" | "reversal" | "payoff" | "recovery" | "transition";
  external_event: string;
  core_conflict: CoreConflict | null;
  conflict_text: string;
  value_shift: ValueShift | string | null;
  pov_character: string;
  hook: string;
  thread_ops: ThreadOp[];
  state_effects_expected: Array<{
    entity?: string;
    entity_id?: string;
    field: string;
    delta?: string;
    before?: string;
    after?: string;
    scene_id?: string;
  }>;
  depends_on: string[];
  must_not: string[];
  scenes: Array<Record<string, unknown>>;
  status: "draft" | "planned" | "needs_review" | "frozen";
  recovery_generated?: boolean;
}

export interface ProtagonistCore {
  desire: string;
  need: string;
  flaw: string;
  lie: string;
}

export interface StoryConstitution {
  logline: string;
  reader_promise: string[];
  controlling_idea: string;
  ending_direction: string;
  protagonist_core: ProtagonistCore | null;
  non_negotiables: Array<{ type: string; id: string }>;
}

export interface VolumeNode {
  node_id: string;
  name: string;
  beat_role: string;
  chapter_range: number[];
  turning_point: string;
}

export interface Volume {
  volume_id: string;
  name: string;
  promise: string;
  chapter_range: number[];
  value_from: string;
  value_to: string;
  nodes: VolumeNode[];
}

export interface MacroPlan {
  structure_model: "three_act" | "four_act" | "save_the_cat" | "story_grid" | "custom";
  volumes: Volume[];
}

export interface ArcStage {
  stage_name: string;
  chapter_range: number[];
  choice_pressure: string;
  relationship_change: string;
}

export interface CharacterArc {
  character_id: string;
  character_name: string;
  arc_type: "positive" | "negative" | "flat";
  stages: ArcStage[];
}

export interface ArcPlan {
  arcs: CharacterArc[];
}

export interface ThreadItem {
  thread_id: string;
  name: string;
  thread_type: "main" | "subplot" | "hidden" | "foreshadowing" | "suspense";
  status: "planned" | "active" | "resolved" | "abandoned";
  description: string;
  plant_chapters: number[];
  escalation_chapters: number[];
  reveal_chapters: number[];
  depends_on_threads: string[];
}

export interface ThreadPlan {
  threads: ThreadItem[];
}

export interface SceneBriefItem {
  scene_id: string;
  type: "dialogue" | "action" | "discovery" | "reflection" | "transition" | "development";
  goal: string;
  conflict: string;
  outcome: string;
  info_release: string;
  hook: string;
  required_context_refs: string[];
  state_effects?: Array<{
    entity?: string;
    entity_id?: string;
    field: string;
    before?: string;
    after?: string;
  }>;
  beat_role?: "setup" | "pressure" | "decision" | "turning_point" | "hook";
}

export interface SceneBrief {
  chapter_id: string;
  version: number;
  source: "generated" | "legacy" | "user_edited" | "auto_completed" | "expert_generated";
  expires_when: string;
  expires_scope: "current" | "adjacent" | "related_threads";
  minimum_scene_count?: number;
  recommended_scene_count?: number;
  scene_budget_reasons?: string[];
  scene_budget_signature?: string;
  scene_count_user_overridden?: boolean;
  scenes: SceneBriefItem[];
}

export interface StoryPlanMeta {
  version: number;
  frozen_layers: string[];
  draft_active: boolean;
  amendment_count: number;
  index_version: number;
}

export interface StoryPlan {
  story_constitution: StoryConstitution | null;
  macro_plan: MacroPlan | null;
  arc_plan: ArcPlan | null;
  thread_plan: ThreadPlan | null;
  chapter_spine: ChapterSpineItem[];
  scene_briefs: Record<string, SceneBrief>;
  generation_corridors: Record<string, unknown>;
  meta: StoryPlanMeta;
}

export interface ChapterBlueprintDiagnostic {
  code: string;
  severity: "blocking" | "warning" | string;
  field_path: string;
  message: string;
  blocks_generation: boolean;
}

export interface ChapterBlueprint {
  chapter_number: number;
  chapter: Partial<ChapterSpineItem>;
  scene_brief: Partial<SceneBrief>;
  scenes: SceneBriefItem[];
  revision: number;
  plan_version: number;
  scene_brief_version: number;
  persisted: boolean;
  frozen: boolean;
  frozen_layers: string[];
  diagnostics: ChapterBlueprintDiagnostic[];
}

// ---- Chapter Extractor ----
export type ExtractMode = 'own_project' | 'benchmark' | 'corpus';
export type EventType = 'action' | 'revelation' | 'decision' | 'conflict' | 'resolution' | 'twist';
export type ImpactLevel = 'major' | 'moderate' | 'minor';
export type ForeshadowingOperation = 'plant' | 'advance' | 'payoff' | 'abandon';
export type HookPosition = 'opening' | 'mid' | 'closing';
export type HookType = 'question' | 'mystery' | 'conflict' | 'reversal' | 'promise';
export type ExtractionQuality = 'high' | 'medium' | 'low';

export interface ChapterExtractionInput {
  chapter_text: string;
  chapter_number: number;
  known_entities: string[];
  known_terms: string[];
  extract_mode: ExtractMode;
}

export interface PlotEvent {
  description: string;
  event_type: EventType;
  characters_involved: string[];
  impact_level: ImpactLevel;
}

export interface CharacterMention {
  name: string;
  action: string;
  state_change: string;
  emotion: string;
}

export interface ForeshadowingOp {
  operation: ForeshadowingOperation;
  clue_id: string;
  description: string;
}

export interface TimelineEvent {
  time_marker: string;
  event: string;
  location: string;
}

export interface HookPoint {
  position: HookPosition;
  hook_type: HookType;
  content: string;
}

export interface ChapterExtractionResult {
  chapter_number: number;
  chapter_summary: string;
  plot_events: PlotEvent[];
  character_mentions: CharacterMention[];
  state_changes: string[];
  foreshadowing_ops: ForeshadowingOp[];
  timeline_events: TimelineEvent[];
  setting_mentions: string[];
  emotion_curve: string[];
  hook_points: HookPoint[];
  information_release_points: string[];
  extraction_quality: ExtractionQuality;
  extraction_notes: string;
}

// ---- Methodology ----
export type MethodologyCategory =
  | 'opening_design' | 'chapter_hook' | 'paragraph_hook'
  | 'suspense' | 'reversal' | 'emotion_arc' | 'dialogue'
  | 'relationship' | 'anti_ai' | 'quality_rubric' | 'market_scan';

export interface MethodologySource {
  id: string;
  name: string;
  source_url: string;
  license: string;
  version: string;
  imported_at: string;
  enabled: boolean;
}

export interface MethodologyCard {
  id: string;
  source_id: string;
  category: MethodologyCategory;
  title: string;
  abstract_rule: string;
  applies_to: string[];
  conflicts_with: string[];
  token_cost_estimate: number;
  safety_level: 'safe' | 'caution' | 'experimental';
  source_file: string;
}

export interface MethodologyRule {
  id: string;
  card_id: string;
  rule_type: 'guidance' | 'avoid' | 'example_policy';
  trigger_condition: string;
  guidance: string;
  avoid: string;
  examples_policy: 'no_examples' | 'abstract_only' | 'with_examples';
}

export interface MethodologySourceResponse {
  source: MethodologySource;
  card_count: number;
  rule_count: number;
}

// ---- Deslop Gate ----
export type DeslopGate = 'A' | 'B' | 'C' | 'D' | 'E' | 'F' | 'G' | 'H' | 'I' | 'J' | 'K' | 'L';
export type RepairScope = 'word' | 'phrase' | 'sentence' | 'paragraph';
export type PatchStrategy = 'replace' | 'rephrase' | 'delete' | 'restructure';

export interface DeslopGateReport {
  gate: DeslopGate;
  severity: 'critical' | 'high' | 'medium' | 'low';
  evidence_spans: string[];
  repair_scope: RepairScope;
  deletion_limit: number;
  protected_spans: string[];
  whitelist_hits: string[];
}

export interface DeslopRepairPlan {
  gates_to_apply: DeslopGate[];
  max_delete_ratio: number;
  preserve_plot_functions: boolean;
  patch_strategy: PatchStrategy;
}

export interface DeslopResult {
  gate_reports: DeslopGateReport[];
  repair_plan: DeslopRepairPlan | null;
  original_text: string;
  repaired_text: string;
  total_issues: number;
  fixed_issues: number;
  deletion_ratio: number;
}

// ---- Review Finding ----
export type SLevel = 'S1' | 'S2' | 'S3' | 'S4';
export type FindingCategory = 'structure' | 'character' | 'prose' | 'consistency' | 'platform' | 'factual' | 'format' | 'pacing' | 'style' | 'advisory';

export interface ReviewFinding {
  id: string;
  severity: SLevel;
  category: FindingCategory;
  source_checker: string;
  source_layer: string;
  location: string;
  evidence: string;
  issue: string;
  fix_direction: string;
  scope: string;
  repairable_by_text: boolean;
  repairable_by_contract: boolean;
  suggested_strategy: string;
  confidence: number;
}

// ---- Chapter Plan Contract ----
export type ChapterFunction = 'opening_hook' | 'setup' | 'escalation' | 'reversal' | 'payoff' | 'transition' | 'climax' | 'resolution' | 'breather';
export type PayoffOrSetup = 'payoff' | 'setup' | 'both' | 'neither';

export interface ChapterPlanContract {
  chapter_number: number;
  chapter_function: ChapterFunction;
  target_emotion: string;
  reader_question_to_open: string;
  reader_question_to_close: string;
  main_conflict: string;
  must_progress: string[];
  must_not_resolve: string[];
  payoff_or_setup: PayoffOrSetup;
  character_delta: string[];
  relationship_delta: string[];
  foreshadowing_ops: string[];
  scene_budget: number;
  word_budget: number;
}

export interface ChapterPlanSequence {
  project_id: string;
  chapters: ChapterPlanContract[];
  total_chapters: number;
  created_at: string;
  source: string;
}

// ---- Benchmark ----
export type BenchmarkCategory = 'opening_design' | 'chapter_hook' | 'suspense' | 'reversal' | 'emotion_arc' | 'dialogue' | 'relationship' | 'pacing' | 'worldbuilding';
export type DeconstructionFocus = 'golden_three' | 'full' | 'style_only';
export type DeconstructionStatus = 'pending' | 'in_progress' | 'completed' | 'failed';

export interface BenchmarkBook {
  id: string; title: string; author: string; platform: string; genre: string;
  total_chapters: number; imported_at: string; project_bindings: string[];
}

export interface BenchmarkChapter {
  id: string; book_id: string; chapter_number: number; chapter_title: string;
  summary: string; plot_events: string[]; character_mentions: string[];
  hook_type: string; emotion_arc: string;
}

export interface BenchmarkStrategyCard {
  id: string; book_id: string; category: BenchmarkCategory; title: string;
  strategy: string; evidence: string; applies_to: string[];
}

export interface BenchmarkDeconstructionRequest {
  book_id: string; focus: DeconstructionFocus; max_chapters: number;
}

// ---- Market Intelligence ----
export type DataQuality = 'high' | 'medium' | 'low';
export type TopicStatus = 'draft' | 'confirmed' | 'active' | 'archived';

export interface MarketSnapshotItem {
  rank: number; title: string; author: string; platform: string; genre: string; score: number; read_count: string;
}

export interface MarketSnapshot {
  id: string; platform: string; rank_type: string; captured_at: string;
  data_quality: DataQuality; items: MarketSnapshotItem[];
}

export interface MarketTrend {
  id: string; platform: string; genre: string; repeated_patterns: string[];
  title_patterns: string[]; opening_selling_points: string[];
  reader_signal: string; confidence: number;
}

export interface TopicDecision {
  id: string; project_id: string; target_platform: string; genre: string;
  core_emotion: string; candidate_hooks: string[]; benchmark_candidates: string[];
  risk_notes: string[]; status: TopicStatus; created_at: string;
}

export interface TopicDecisionContract {
  target_platform: string; reader_expectation: string; core_emotion: string;
  opening_pressure: string; benchmark_strategy_ids: string[]; avoid_competition_risks: string[];
}

// ---- Editor Decision Context ----
export type ConstraintType = 'fact' | 'style' | 'pacing' | 'contract' | 'methodology';

export interface StylePolicy {
  allowed_styles: string[];
  forbidden_patterns: string[];
  frozen_style_name: string;
}

export interface TopicContractSummary {
  target_platform: string;
  core_emotion: string;
  reader_expectation: string;
  opening_pressure: string;
}

export interface MethodologyGuidanceSummary {
  active_cards: string[];
  key_rules: string[];
}

export interface BenchmarkGuidanceSummary {
  active_benchmarks: string[];
  strategy_highlights: string[];
}

export interface StoryStateSummary {
  active_characters: string[];
  open_foreshadowing: string[];
  open_questions: string[];
  recent_relationships: string[];
}

export interface ActiveConstraint {
  constraint_type: ConstraintType;
  description: string;
  source: string;
}

export interface RiskWarning {
  risk_type: string;
  description: string;
  source: string;
}

export interface EditorDecisionContext {
  project_id: string;
  chapter_number: number;
  writing_mode: string;
  style_policy: StylePolicy;
  topic_contract: TopicContractSummary;
  methodology_guidance: MethodologyGuidanceSummary;
  benchmark_guidance: BenchmarkGuidanceSummary;
  story_state_summary: StoryStateSummary;
  active_constraints: ActiveConstraint[];
  risk_warnings: RiskWarning[];
  token_budget_used: number;
  enabled_modules: string[];
}

export interface EditorDecisionContextRequest {
  project_id: string;
  chapter_number: number;
  max_token_budget: number;
  enabled_modules: string[] | null;
}

export interface ChapterDecisionBrief {
  chapter_goal: string;
  character_states: string;
  facts_to_continue: string[];
  recommended_pacing: string;
  info_release_limit: string;
  forbidden_items: string[];
  style_protection: string;
  commercial_hooks: string[];
  risk_notes: string[];
}

// --- Agent Skill Runtime Types ---

export type SkillKind = 'prompt' | 'service' | 'tool' | 'workflow' | 'validator';
export type SkillSource = 'builtin' | 'custom' | 'project_file' | 'worldbuilder_sub_agent';

export interface SkillTrigger {
  agents: string[];
  domains: string[];
  scene_roles: string[];
  issue_types: string[];
  writing_modes: string[];
  tabs: string[];
  feature_flags: string[];
}

export interface SkillRuntimeBinding {
  kind: SkillKind;
  service_class: string | null;
  service_method: string | null;
  tool_names: string[];
  min_permission: string;
  execution_phase: string;
  timeout_seconds: number;
  output_char_budget: number;
  cache_enabled: boolean;
}

export interface AgentSkillManifest {
  id: string;
  name: string;
  display_name?: string;
  description: string;
  version: string;
  format_version: string;
  source_format: string;
  source: SkillSource;
  kind: SkillKind;
  domain: string;
  category: string;
  agents: string[];
  priority: number;
  enabled_by_default: boolean;
  triggers: SkillTrigger;
  runtime: SkillRuntimeBinding;
  constraints: Record<string, Record<string, unknown>>;
  validators: string[];
  repair_strategies: string[];
  repair_hooks: string[];
  path: string;
}

export interface AgentSkill {
  manifest: AgentSkillManifest;
  body: string;
  prompt_sections: Record<string, string>;
  examples: string[];
  references: string[];
}

export interface CompiledSkillPacket {
  agent_name: string;
  active_skills: string[];
  activation_reasons: Record<string, string>;
  system_sections: string[];
  user_sections: string[];
  execution_plan: Record<string, unknown>[];
  tool_permissions: Record<string, string>;
  validators: string[];
  validation_contracts: Record<string, Record<string, unknown>>;
  repair_hooks: Record<string, string[]>;
  must_avoid: string[];
  trace: Record<string, unknown>;
}

export interface SkillExecutionTrace {
  agent_name: string;
  active_skills: string[];
  skipped_skills: Record<string, string>;
  activation_reasons: Record<string, string>;
  loaded_skill_count: number;
  injected_system_chars: number;
  injected_user_chars: number;
  service_outputs: Record<string, Record<string, unknown>>;
  validators_requested: string[];
  permission_decisions: Record<string, Record<string, unknown>>;
}

export interface SkillDetailResponse {
  manifest: AgentSkillManifest;
  body: string;
  prompt_sections: Record<string, string>;
  package_files: Array<{ path: string; size: number }>;
  format_version: string;
  source_format: string;
  source: SkillSource;
}

export interface SkillPreviewRequest {
  context: Record<string, unknown>;
}

export interface SkillPreviewResponse {
  active_skills: string[];
  activation_reasons: Record<string, string>;
  rendered_system_preview: string;
  rendered_user_preview: string;
  execution_plan: Record<string, unknown>[];
  tool_permissions: Record<string, string>;
  validators: string[];
  validation_contracts: Record<string, Record<string, unknown>>;
  repair_hooks: Record<string, string[]>;
  must_avoid: string[];
  trace: Record<string, unknown>;
}
