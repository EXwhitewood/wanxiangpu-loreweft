import type {
  Project,
  ProjectCreate,
  ChapterListItem,
  Chapter,
  WritingAssistanceSettings,
  WritingAssistanceDimension,
  ChapterAdvisoryTrigger,
  ChapterAdvisoryCheckResponse,
  ChapterDiagnosticRecord,
  StoryState,
  AppSettings,
  AgentDetail,
  AgentConfigItem,
  AgentOverride,
  SkillInfo,
  TestConnectionResult,
  OutlineChatMessage,
  OutlineChatResponse,
  OutlineParseResponse,
  OutlineStatus,
  OutlineExpansionStatus,
  WorldRule,
  WorldbuildingOverview,
  Character,
  Location,
  ForeshadowingLine,
  ForeshadowingDashboard,
  RevealReadiness,
  PromotionProposal,
  WorldviewObservationList,
  StyleProfile,
  StyleConflictReport,
  StyleFusionRequest,
  StyleFusionResult,
  StyleLearningProgress,
  StoryPlan,
  ChapterBlueprint,
  ThreadPlan,
  SceneBrief,
  SystemInfo,
  GenerationTrace,
  EntityProgression,
  ProjectHealth,
  EntertainmentSettings,
  GenerationFeaturePolicy,
  ReaderCorpusSummary,
  ReaderCorpusSource,
  ReaderExperiencePattern,
  ReaderExperienceGuidance,
  ChapterSaveResponse,
  ChapterSettlementStatus,
  WorldviewObservationListParams,
} from "@/types";
import type { EffectivenessProof } from "@/types/editorV2";

let desktopBackendPort = 18000;

/** Set by DesktopBootstrap after the Rust host selects the actual port. */
export function setDesktopBackendPort(port: number): void {
  if (Number.isInteger(port) && port >= 1024 && port <= 65535) {
    desktopBackendPort = port;
  }
}

function getBackendUrl(): string {
  if ((window as any).__TAURI_INTERNALS__) {
    return `http://127.0.0.1:${desktopBackendPort}/api`;
  }
  return "/api";
}

const REQUEST_TIMEOUT = 15000;
const GUIDED_STEP_TIMEOUT = 60000;
const GUIDED_GENERATE_TIMEOUT = 360000;
// 完整大纲确认会触发带工具调用的长推理，后端任务允许约 300 秒；
// 客户端额外留出网络和落盘余量，避免 15 秒默认超时主动 abort。
const OUTLINE_CHAT_TIMEOUT = 360000;
const REVIEW_REPAIR_TIMEOUT = 180000;
const ADVISORY_CHECK_TIMEOUT = 300000;

async function requestInternal<T>(url: string, options?: RequestInit, timeout?: number): Promise<T> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeout || REQUEST_TIMEOUT);

  // If caller provides a signal, link it so external abort also cancels this request.
  const externalSignal = options?.signal;
  if (externalSignal) {
    if (externalSignal.aborted) {
      controller.abort();
    } else {
      externalSignal.addEventListener("abort", () => controller.abort(), { once: true });
    }
  }

  const { headers: customHeaders, signal: _ignored, ...restOptions } = options || {};
  const mergedHeaders = {
    "Content-Type": "application/json",
    ...customHeaders,
  };

  try {
    const res = await fetch(`${getBackendUrl()}${url}`, {
      ...restOptions,
      headers: mergedHeaders,
      signal: controller.signal,
    });
    if (!res.ok) {
      const error = await res.json().catch(() => ({ detail: res.statusText }));
      const detail = typeof error.detail === "string"
        ? error.detail
        : error.detail?.message;
      throw new Error(detail || `请求失败: ${res.status}`);
    }
    return res.json();
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") {
      // Re-throw as AbortError so callers can distinguish external abort from timeout
      throw new DOMException("Request aborted", "AbortError");
    }
    throw e;
  } finally {
    clearTimeout(timeoutId);
  }
}

async function request<T>(url: string, options?: RequestInit, timeout?: number): Promise<T> {
  return requestInternal<T>(url, options, timeout);
}

export async function fetchProjects(): Promise<Project[]> {
  return request<Project[]>("/projects");
}

export async function createProject(data: ProjectCreate): Promise<Project> {
  return request<Project>("/projects", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function getProject(id: string): Promise<Project> {
  return request<Project>(`/projects/${id}`);
}

export type DailyInspirationResponse = {
  date: string;
  content: string;
  source: "llm" | "fallback";
};

export async function getDailyInspiration(
  projectId: string,
  dateKey?: string,
): Promise<DailyInspirationResponse> {
  const query = dateKey ? `?date=${encodeURIComponent(dateKey)}` : "";
  return request<DailyInspirationResponse>(
    `/projects/${projectId}/daily-inspiration${query}`,
    undefined,
    30000,
  );
}

export async function deleteProject(id: string): Promise<void> {
  await request(`/projects/${id}`, { method: "DELETE" });
}

export async function getChapters(projectId: string): Promise<ChapterListItem[]> {
  return request<ChapterListItem[]>(`/projects/${projectId}/chapters`);
}

export async function getChapter(
  projectId: string,
  chapterNumber: number
): Promise<Chapter> {
  return request<Chapter>(
    `/projects/${projectId}/chapters/${chapterNumber}`
  );
}

export async function updateChapter(
  projectId: string,
  chapterNumber: number,
  data: Partial<Chapter>
): Promise<ChapterSaveResponse> {
  return request<ChapterSaveResponse>(
    `/projects/${projectId}/chapters/${chapterNumber}`,
    {
      method: "PUT",
      body: JSON.stringify(data),
    }
  );
}

export async function getWritingAssistanceSettings(
  projectId: string
): Promise<WritingAssistanceSettings> {
  return request<WritingAssistanceSettings>(
    `/projects/${projectId}/writing-assistance/settings`
  );
}

export async function updateWritingAssistanceSettings(
  projectId: string,
  data: WritingAssistanceSettings
): Promise<WritingAssistanceSettings> {
  return request<WritingAssistanceSettings>(
    `/projects/${projectId}/writing-assistance/settings`,
    {
      method: "PUT",
      body: JSON.stringify(data),
    }
  );
}

export async function checkChapterAdvisories(
  projectId: string,
  chapterNumber: number,
  data: {
    content: string;
    trigger: ChapterAdvisoryTrigger;
    dimensions?: WritingAssistanceDimension[];
  }
): Promise<ChapterAdvisoryCheckResponse> {
  return request<ChapterAdvisoryCheckResponse>(
    `/projects/${projectId}/writing-assistance/chapters/${chapterNumber}/check`,
    {
      method: "POST",
      body: JSON.stringify(data),
    },
    ADVISORY_CHECK_TIMEOUT
  );
}

export async function deleteChapter(
  projectId: string,
  chapterNumber: number
): Promise<{ message: string }> {
  return request<{ message: string }>(
    `/projects/${projectId}/chapters/${chapterNumber}`,
    { method: "DELETE" }
  );
}

export async function getGenerationTraces(
  projectId: string,
  limit: number = 50
): Promise<{ items: GenerationTrace[]; total: number }> {
  return request(`/projects/${projectId}/generation-traces?limit=${limit}`);
}

export async function getProjectHealth(projectId: string): Promise<ProjectHealth> {
  return request(`/projects/${projectId}/health`);
}

export async function getProgressions(
  projectId: string,
  limit: number = 100
): Promise<{ items: EntityProgression[]; total: number }> {
  return request(`/projects/${projectId}/progressions?limit=${limit}`);
}

export async function getGenerationFeatures(
  projectId: string
): Promise<{ effective: GenerationFeaturePolicy; project: Partial<GenerationFeaturePolicy> }> {
  return request(`/projects/${projectId}/generation-features`);
}

export async function getReaderCorpusSummary(): Promise<ReaderCorpusSummary> {
  return request("/admin/reader-corpus/summary");
}

export async function listReaderCorpusSources(
  limit: number = 100,
  offset: number = 0
): Promise<{ items: ReaderCorpusSource[]; total: number }> {
  return request(`/admin/reader-corpus/sources?limit=${limit}&offset=${offset}`);
}

export async function listReaderExperiencePatterns(
  params: { genre?: string; pattern_type?: string; limit?: number } = {}
): Promise<{ items: ReaderExperiencePattern[]; total: number }> {
  const qs = new URLSearchParams();
  if (params.genre) qs.set("genre", params.genre);
  if (params.pattern_type) qs.set("pattern_type", params.pattern_type);
  if (params.limit) qs.set("limit", String(params.limit));
  return request(`/admin/reader-corpus/patterns${qs.toString() ? `?${qs}` : ""}`);
}

export async function uploadReaderCorpusFiles(data: {
  files: File[];
  genre?: string;
  platform?: string;
  quality_tier?: string;
  author?: string;
  tags?: string;
  allowed_uses?: string;
  notes?: string;
}): Promise<{ imported: number; failed: number; sources: ReaderCorpusSource[]; errors: string[] }> {
  const form = new FormData();
  data.files.forEach((file) => form.append("files", file));
  form.append("genre", data.genre || "");
  form.append("platform", data.platform || "");
  form.append("quality_tier", data.quality_tier || "A");
  form.append("author", data.author || "");
  form.append("tags", data.tags || "");
  form.append("allowed_uses", data.allowed_uses || "");
  form.append("notes", data.notes || "");

  const res = await fetch(`${getBackendUrl()}/admin/reader-corpus/upload`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(error.detail || `上传失败: ${res.status}`);
  }
  return res.json();
}

export async function importReaderCorpusFolder(data: {
  folder_path: string;
  genre?: string;
  platform?: string;
  quality_tier?: string;
  tags?: string[];
}): Promise<Record<string, unknown>> {
  return request("/admin/reader-corpus/import-folder", {
    method: "POST",
    body: JSON.stringify(data),
  }, 120000);
}

export async function rebuildReaderCorpusPatterns(): Promise<Record<string, unknown>> {
  return request("/admin/reader-corpus/rebuild-patterns", { method: "POST" }, 120000);
}

export async function previewReaderCorpusGuidance(data: {
  genre?: string;
  chapter_number?: number;
  scene_index?: number;
  scene_count?: number | null;
  scene_contract?: Record<string, unknown>;
  limit?: number;
}): Promise<ReaderExperienceGuidance> {
  return request("/admin/reader-corpus/guidance/preview", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function deleteReaderCorpusSource(sourceId: string): Promise<{ deleted: boolean }> {
  return request(`/admin/reader-corpus/sources/${sourceId}`, { method: "DELETE" });
}

export async function getState(projectId: string): Promise<StoryState> {
  return request<StoryState>(`/projects/${projectId}/state`);
}

export async function updateState(
  projectId: string,
  stateData: StoryState | Record<string, unknown>
): Promise<StoryState> {
  return request<StoryState>(`/projects/${projectId}/state`, {
    method: "PUT",
    body: JSON.stringify(stateData),
  });
}

export async function getSettings(): Promise<AppSettings> {
  return request<AppSettings>("/settings");
}

export async function updateSettings(
  settingsData: AppSettings
): Promise<AppSettings> {
  return request<AppSettings>("/settings", {
    method: "PUT",
    body: JSON.stringify(settingsData),
  });
}

export async function getSystemInfo(): Promise<SystemInfo> {
  return request<SystemInfo>("/settings/system-info");
}

export async function getAgentConfigs(): Promise<AgentConfigItem[]> {
  return request<AgentConfigItem[]>("/settings/agents");
}

export async function getChapterDiagnostics(
  projectId: string,
  chapterNumber: number,
  includeResolved = false,
): Promise<{ chapter_number: number; items: ChapterDiagnosticRecord[] }> {
  const query = includeResolved ? "?include_resolved=true" : "";
  return request<{ chapter_number: number; items: ChapterDiagnosticRecord[] }>(
    `/projects/${projectId}/writing-assistance/chapters/${chapterNumber}/diagnostics${query}`,
  );
}

export async function getChapterSettlement(
  projectId: string,
  chapterNumber: number,
): Promise<ChapterSettlementStatus | null> {
  return request<ChapterSettlementStatus | null>(
    `/projects/${projectId}/chapters/${chapterNumber}/settlement`,
  );
}

export async function retryChapterSettlement(
  projectId: string,
  chapterNumber: number,
): Promise<ChapterSettlementStatus> {
  return request<ChapterSettlementStatus>(
    `/projects/${projectId}/chapters/${chapterNumber}/settlement/retry`,
    { method: "POST" },
  );
}

export async function decideChapterDiagnostic(
  projectId: string,
  chapterNumber: number,
  diagnosticId: string,
  data: {
    action: "mark_fixed" | "ignore" | "ignore_and_amend_outline";
    reason?: string;
    outline_changes?: Record<string, unknown>;
    decided_by?: string;
  },
): Promise<ChapterDiagnosticRecord> {
  return request<ChapterDiagnosticRecord>(
    `/projects/${projectId}/writing-assistance/chapters/${chapterNumber}/diagnostics/${diagnosticId}/decision`,
    {
      method: "POST",
      body: JSON.stringify(data),
    },
  );
}

export async function getAgents(): Promise<AgentDetail[]> {
  return request<AgentDetail[]>("/agents");
}

export async function getSkills(): Promise<SkillInfo[]> {
  return request<SkillInfo[]>("/agents/skills");
}

export async function importSkills(
  files: File[],
  category: "utility" | "agent",
  agentName?: string,
): Promise<Array<{ name?: string; filename?: string; error?: string; import_warnings?: string[] }>> {
  const formData = new FormData();
  files.forEach((file) => formData.append("files", file));
  const params = new URLSearchParams({ category });
  if (agentName) params.set("agent", agentName);

  const response = await fetch(`${getBackendUrl()}/agents/skills/import?${params.toString()}`, {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    throw new Error(`Skill 导入失败（HTTP ${response.status}）`);
  }
  return response.json();
}

export async function updateAgentConfig(
  agentName: string,
  overrideData: AgentOverride
): Promise<{ agent_name: string; config: AgentOverride }> {
  return request(`/settings/agents/${agentName}`, {
    method: "PUT",
    body: JSON.stringify(overrideData),
  });
}

export async function testConnection(
  configData: {
    api_format: string;
    api_key: string;
    base_url: string;
    model: string;
  }
): Promise<TestConnectionResult> {
  return request<TestConnectionResult>("/settings/test-connection", {
    method: "POST",
    body: JSON.stringify(configData),
  }, 30000);
}

// ====================================================================
// 娱乐设置 API（等待娱乐功能：跳转外部平台 + 经典小游戏）
// ====================================================================
// 端点挂载在 /api/settings 前缀下，独立存储于 data/entertainment_settings.json
// --------------------------------------------------------------------

export async function getEntertainmentSettings(): Promise<EntertainmentSettings> {
  return request<EntertainmentSettings>("/settings/entertainment");
}

export async function updateEntertainmentSettings(
  settingsData: EntertainmentSettings
): Promise<EntertainmentSettings> {
  return request<EntertainmentSettings>("/settings/entertainment", {
    method: "PUT",
    body: JSON.stringify(settingsData),
  });
}

export async function resetEntertainmentSettings(): Promise<EntertainmentSettings> {
  return request<EntertainmentSettings>("/settings/entertainment/reset", {
    method: "POST",
  });
}

export async function outlineChat(
  projectId: string,
  messages: OutlineChatMessage[],
  contextMode: "master" | "chapter" = "master",
  selectedChapterNumber?: number,
  signal?: AbortSignal,
): Promise<OutlineChatResponse> {
  return request<OutlineChatResponse>(`/outline/${projectId}/chat`, {
    method: "POST",
    signal,
    body: JSON.stringify({
      messages,
      context_mode: contextMode,
      selected_chapter_number: selectedChapterNumber,
    }),
  }, OUTLINE_CHAT_TIMEOUT);
}

export function outlineChatStreamUrl(projectId: string): string {
  return `${getBackendUrl()}/outline/${projectId}/chat/stream`;
}

export async function outlineChatStream(
  projectId: string,
  messages: OutlineChatMessage[],
  contextMode: "master" | "chapter",
  selectedChapterNumber: number | undefined,
  onToolCall: (data: { tool: string; args: Record<string, unknown>; display: string }) => void,
  onToolResult: (data: { tool: string; summary: string; changed?: boolean }) => void,
  onTextDelta: (text: string) => void,
  onDone: (response?: string) => void,
  onError: (error: string) => void,
  onAborted?: () => void,
  signal?: AbortSignal,
): Promise<void> {
  const url = outlineChatStreamUrl(projectId);
  try {
    var res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        messages,
        context_mode: contextMode,
        selected_chapter_number: selectedChapterNumber,
      }),
      signal,
    });
  } catch (e) {
    if (signal?.aborted) {
      onAborted?.();
      return;
    }
    onError((e as Error).message || "连接失败");
    return;
  }
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    onError(error.detail || `请求失败: ${res.status}`);
    return;
  }

  if (!res.body) {
    onError("服务器没有返回可读取的流");
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let currentEvent = "";
  let receivedDone = false;

  while (true) {
    let chunk: ReadableStreamReadResult<Uint8Array>;
    try {
      chunk = await reader.read();
    } catch (e) {
      if (signal?.aborted || (e instanceof DOMException && e.name === "AbortError")) {
        onAborted?.();
      } else {
        onError((e as Error).message || "流连接读取失败，请重试");
      }
      await reader.cancel().catch(() => {});
      return;
    }
    const { done, value } = chunk;
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      if (line.startsWith("event: ")) {
        currentEvent = line.slice(7).trim();
      } else if (line.startsWith("data: ")) {
        const dataStr = line.slice(6);
        if (dataStr.trim() === "[DONE]") {
          receivedDone = true;
          onDone();
          return;
        }
        try {
          const data = JSON.parse(dataStr);
          if (currentEvent === "tool_call") {
            onToolCall(data as { tool: string; args: Record<string, unknown>; display: string });
          } else if (currentEvent === "tool_result") {
            onToolResult(data as { tool: string; summary: string; changed?: boolean });
          } else if (currentEvent === "text_delta") {
            onTextDelta(data.content || "");
          } else if (currentEvent === "done") {
            receivedDone = true;
            onDone(data.response);
          } else if (currentEvent === "error") {
            onError(data.message || "未知错误");
            await reader.cancel().catch(() => {});
            return;
          }
        } catch {
          // skip malformed data
        }
        currentEvent = "";
      }
    }
  }

  if (signal?.aborted) {
    onAborted?.();
    return;
  }
  if (!receivedDone) {
    onError("连接意外断开，请重试");
  }
}

export async function guidedStep(
  projectId: string,
  step: string,
  answers: Record<string, string> = {},
  supplement: string = ""
): Promise<{
  step_type: string;
  content?: string;
  core_themes?: string[];
  question?: string;
  options?: Array<{ label: string; value: string; recommended: boolean; reasoning: string }>;
  followup_questions?: Array<{ question: string; placeholder?: string }>;
  outline_text?: string;
  outline_json?: Record<string, unknown> | null;
  saved?: boolean;
  draft_saved?: boolean;
  recovered?: boolean;
  recovery_note?: string;
  staged?: boolean;
  requested_chapters?: number;
  generated_chapters?: number;
  validation?: Record<string, unknown>;
}> {
  const timeout = step === "generate" ? GUIDED_GENERATE_TIMEOUT : GUIDED_STEP_TIMEOUT;
  return request(`/outline/${projectId}/guided/step`, {
    method: "POST",
    body: JSON.stringify({ step, answers, supplement }),
  }, timeout);
}

export async function parseOutline(
  projectId: string,
  outlineText: string
): Promise<OutlineParseResponse> {
  return request<OutlineParseResponse>(`/outline/${projectId}/parse`, {
    method: "POST",
    body: JSON.stringify({ outline_text: outlineText }),
  });
}

export async function getOutlineStatus(projectId: string): Promise<OutlineStatus> {
  return request<OutlineStatus>(`/outline/${projectId}/status`);
}

export async function startOutlineExpansion(
  projectId: string,
  data: {
    target_chapters: number;
    batch_size?: number;
    replace_existing?: boolean;
    force_restart?: boolean;
    seed_context?: Record<string, unknown>;
  },
): Promise<OutlineExpansionStatus> {
  return request<OutlineExpansionStatus>(`/outline/${projectId}/expansion/start`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function getOutlineExpansionStatus(
  projectId: string,
): Promise<OutlineExpansionStatus> {
  return request<OutlineExpansionStatus>(`/outline/${projectId}/expansion/status`);
}

export async function resumeOutlineExpansion(
  projectId: string,
): Promise<OutlineExpansionStatus> {
  return request<OutlineExpansionStatus>(`/outline/${projectId}/expansion/resume`, {
    method: "POST",
  });
}

export async function cancelOutlineExpansion(
  projectId: string,
): Promise<OutlineExpansionStatus> {
  return request<OutlineExpansionStatus>(`/outline/${projectId}/expansion/cancel`, {
    method: "POST",
  });
}

export async function deleteOutline(
  projectId: string
): Promise<{ message: string; deleted: boolean }> {
  return request(`/outline/${projectId}`, {
    method: "DELETE",
  });
}

export async function freezeOutline(
  projectId: string
): Promise<{ message: string; frozen: boolean; frozen_at: string; version: number }> {
  return request(`/outline/${projectId}/freeze`, {
    method: "POST",
  });
}

export async function unfreezeOutline(
  projectId: string
): Promise<{ message: string; frozen: boolean; version: number }> {
  return request(`/outline/${projectId}/unfreeze`, {
    method: "POST",
  });
}

export async function saveDraftOutline(
  projectId: string,
  outlineData: Record<string, unknown>
): Promise<{ message: string; draft_saved: boolean }> {
  return request(`/outline/${projectId}/draft`, {
    method: "POST",
    body: JSON.stringify(outlineData),
  });
}

export async function parseDraftOutline(
  projectId: string,
  outlineText: string
): Promise<{
  message: string;
  draft_saved: boolean;
  outline_data: Record<string, unknown>;
  recovered?: boolean;
  recovery_note?: string;
}> {
  return request(`/outline/${projectId}/draft/parse`, {
    method: "POST",
    body: JSON.stringify({ outline_text: outlineText }),
  }, GUIDED_GENERATE_TIMEOUT);
}

export async function recoverOutlineContinuityDraft(
  projectId: string
): Promise<{
  message: string;
  draft_saved: boolean;
  recovery: {
    anchor_chapters: number[];
    anchor_count: number;
    added_chapters: number[];
    added_count: number;
    target_chapter_count: number;
    chapter_continuity: OutlineStatus["chapter_continuity"];
    scene_completion: Record<string, unknown>;
  };
}> {
  return request(`/outline/${projectId}/draft/recover-continuity`, {
    method: "POST",
  });
}

export async function readDraftOutline(
  projectId: string
): Promise<{
  has_draft: boolean;
  draft: Record<string, unknown> | null;
  diff_summary: {
    total_changes: number;
    change_types: string[];
    changes: Array<{
      change_type: string;
      target: Record<string, unknown>;
      before: unknown;
      after: unknown;
      reason: string;
      impact: Record<string, unknown>;
    }>;
  } | null;
}> {
  return request(`/outline/${projectId}/draft`);
}

export async function confirmDraftOutline(
  projectId: string
): Promise<{
  message: string;
  outline_data: Record<string, unknown>;
  validation: Record<string, unknown>;
  change_records: number;
}> {
  return request(`/outline/${projectId}/draft/confirm`, {
    method: "POST",
  });
}

export async function discardDraftOutline(
  projectId: string
): Promise<{ message: string }> {
  return request(`/outline/${projectId}/draft/discard`, {
    method: "POST",
  });
}

export async function createAmendment(
  projectId: string,
  data: {
    target_chapters: number[];
    modification: string;
    reason: string;
  }
): Promise<{
  amendment_id: string;
  impact_report: Record<string, unknown>;
  status: string;
}> {
  return request(`/outline/${projectId}/amendment`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function applyAmendment(
  projectId: string,
  amendmentId: string
): Promise<{
  message: string;
  outline_data: Record<string, unknown>;
  validation: Record<string, unknown>;
}> {
  return request(`/outline/${projectId}/amendment/${amendmentId}/apply`, {
    method: "POST",
  });
}

export function editorChatStreamUrl(projectId: string): string {
  return `${getBackendUrl()}/editor/${projectId}/chat/stream`;
}

async function streamChapterChat(
  url: string,
  messages: Array<{ role: string; content: string }>,
  chapterNumber: number,
  onToolCall: (data: { tool: string; args: Record<string, unknown>; display: string }) => void,
  onToolResult: (data: { tool: string; summary: string }) => void,
  onTextDelta: (text: string) => void,
  onDone: (response?: string) => void,
  onError: (error: string) => void,
  signal?: AbortSignal | null,
): Promise<void> {
  try {
    var res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages, chapter_number: chapterNumber }),
      signal: signal || undefined,
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") {
      return;
    }
    onError((e as Error).message || "连接失败");
    return;
  }
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    onError(error.detail || `请求失败: ${res.status}`);
    return;
  }

  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let receivedDone = false;

  while (true) {
    const { done, value } = await reader.read().catch((e) => {
      if (e instanceof DOMException && e.name === "AbortError") {
        return { done: true, value: undefined };
      }
      throw e;
    });
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      if (line.startsWith("data: ")) {
        const dataStr = line.slice(6);
        if (dataStr.trim() === "[DONE]") {
          receivedDone = true;
          onDone();
          return;
        }
        try {
          const payload = JSON.parse(dataStr);
          const eventType = payload.type;
          const eventData = payload.data || {};
          if (eventType === "tool_call") {
            onToolCall(eventData as { tool: string; args: Record<string, unknown>; display: string });
          } else if (eventType === "tool_result") {
            onToolResult(eventData as { tool: string; summary: string });
          } else if (eventType === "text_delta") {
            onTextDelta(eventData.content || "");
          } else if (eventType === "done") {
            receivedDone = true;
            onDone(eventData.response);
          } else if (eventType === "error") {
            onError(eventData.message || "未知错误");
          }
        } catch {
          // skip malformed data
        }
      }
    }
  }

  if (!receivedDone) {
    onError("连接意外断开，请重试");
  }
}

export async function writingCompanionChatStream(
  projectId: string,
  messages: Array<{ role: string; content: string }>,
  chapterNumber: number,
  onTextDelta: (text: string) => void,
  onDone: (response?: string) => void,
  onError: (error: string) => void,
  signal?: AbortSignal | null,
): Promise<void> {
  return streamChapterChat(
    `${getBackendUrl()}/writing-companion/${projectId}/chat/stream`,
    messages,
    chapterNumber,
    () => {},
    () => {},
    onTextDelta,
    onDone,
    onError,
    signal,
  );
}

export async function editorGenerate(
  projectId: string,
  chapterNumber: number,
  povCharacter?: string | null,
  customInstructions?: string | null,
  expectedBlueprintRevision?: number | null,
): Promise<{ execution_id: string; status: string; chapter_number: number }> {
  return request(`/editor/${projectId}/generate`, {
    method: "POST",
    body: JSON.stringify({
      chapter_number: chapterNumber,
      pov_character: povCharacter || null,
      custom_instructions: customInstructions || null,
      expected_blueprint_revision: expectedBlueprintRevision ?? null,
    }),
  }, 300000);
}

export interface ContextEnvelopeBlock {
  block_id: string;
  block_type?: string;
  priority?: string;
  compressible?: boolean;
  compacted?: boolean;
  estimated_tokens?: number;
  tokens?: number;
  source_refs?: string[];
  content_preview?: string;
}

export interface ContextEnvelopeResponse {
  chapter_number: number;
  target_agent: string;
  token_report: Record<string, unknown>;
  blocks: ContextEnvelopeBlock[];
  ledger?: Record<string, unknown>;
  validation?: Record<string, unknown>;
  compaction?: Record<string, unknown>;
}

export async function previewContextEnvelope(
  projectId: string,
  data: { chapter_number: number; manual_focus?: string | null; profile_override?: Record<string, unknown> | null }
): Promise<ContextEnvelopeResponse> {
  return request(`/editor/${projectId}/context/envelope/preview`, {
    method: "POST",
    body: JSON.stringify({
      target_agent: "chapter_writer",
      chapter_number: data.chapter_number,
      manual_focus: data.manual_focus || null,
      profile_override: data.profile_override || null,
    }),
  }, 120000);
}

export async function compactContextEnvelope(
  projectId: string,
  data: { chapter_number: number; manual_focus?: string | null; dry_run?: boolean }
): Promise<ContextEnvelopeResponse> {
  return request(`/editor/${projectId}/context/compact`, {
    method: "POST",
    body: JSON.stringify({
      target_agent: "chapter_writer",
      chapter_number: data.chapter_number,
      manual_focus: data.manual_focus || null,
      dry_run: !!data.dry_run,
    }),
  }, 180000);
}

export async function getLatestContextLedger(
  projectId: string,
  chapterNumber: number,
  agent: string = "chapter_writer"
): Promise<Record<string, unknown>> {
  const params = new URLSearchParams({
    chapter_number: String(chapterNumber),
    agent,
  });
  return request(`/editor/${projectId}/context/ledger/latest?${params.toString()}`);
}

export async function getWorldbuildingOverview(projectId: string): Promise<WorldbuildingOverview> {
  return request<WorldbuildingOverview>(`/worldbuilding/${projectId}/overview`);
}

export async function createWorldRule(
  projectId: string,
  data: Partial<WorldRule>
): Promise<WorldRule> {
  return request<WorldRule>(`/worldbuilding/${projectId}/rules`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function updateWorldRule(
  projectId: string,
  ruleId: string,
  data: Partial<WorldRule>
): Promise<WorldRule> {
  return request<WorldRule>(`/worldbuilding/${projectId}/rules/${ruleId}`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

export async function deleteWorldRule(
  projectId: string,
  ruleId: string
): Promise<{ message: string }> {
  return request<{ message: string }>(
    `/worldbuilding/${projectId}/rules/${ruleId}`,
    { method: "DELETE" }
  );
}

export async function listCharacters(projectId: string): Promise<Character[]> {
  return request<Character[]>(`/worldbuilding/${projectId}/characters`);
}

export async function createCharacter(
  projectId: string,
  data: Partial<Character>
): Promise<Character> {
  return request<Character>(`/worldbuilding/${projectId}/characters`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function updateCharacter(
  projectId: string,
  characterId: string,
  data: Partial<Character>
): Promise<Character> {
  return request<Character>(
    `/worldbuilding/${projectId}/characters/${characterId}`,
    {
      method: "PUT",
      body: JSON.stringify(data),
    }
  );
}

export async function deleteCharacter(
  projectId: string,
  characterId: string
): Promise<{ message: string }> {
  return request<{ message: string }>(
    `/worldbuilding/${projectId}/characters/${characterId}`,
    { method: "DELETE" }
  );
}

export async function createLocation(
  projectId: string,
  data: Partial<Location>
): Promise<Location> {
  return request<Location>(`/worldbuilding/${projectId}/locations`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function updateLocation(
  projectId: string,
  locationId: string,
  data: Partial<Location>
): Promise<Location> {
  return request<Location>(
    `/worldbuilding/${projectId}/locations/${locationId}`,
    {
      method: "PUT",
      body: JSON.stringify(data),
    }
  );
}

export async function deleteLocation(
  projectId: string,
  locationId: string
): Promise<{ message: string }> {
  return request<{ message: string }>(
    `/worldbuilding/${projectId}/locations/${locationId}`,
    { method: "DELETE" }
  );
}

export async function worldbuildingChat(
  projectId: string,
  messages: Array<{ role: string; content: string }>,
  activeTab: string
): Promise<{ response: string; suggestions: Record<string, unknown>[] | Record<string, unknown> | null; mentions?: { type: string; name: string }[]; navigates?: { tab: string; reason: string }[] }> {
  return request<{ response: string; suggestions: Record<string, unknown>[] | Record<string, unknown> | null; mentions?: { type: string; name: string }[]; navigates?: { tab: string; reason: string }[] }>(
    `/worldbuilding/${projectId}/chat`,
    {
      method: "POST",
      body: JSON.stringify({ messages, active_tab: activeTab }),
    },
    120000
  );
}

export async function listForeshadowing(projectId: string): Promise<ForeshadowingLine[]> {
  return request<ForeshadowingLine[]>(`/worldbuilding/${projectId}/foreshadowing`);
}

export async function createForeshadowing(
  projectId: string,
  data: Partial<ForeshadowingLine>
): Promise<ForeshadowingLine> {
  return request<ForeshadowingLine>(`/worldbuilding/${projectId}/foreshadowing`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function updateForeshadowing(
  projectId: string,
  itemId: string,
  data: Partial<ForeshadowingLine>
): Promise<ForeshadowingLine> {
  return request<ForeshadowingLine>(
    `/worldbuilding/${projectId}/foreshadowing/${itemId}`,
    {
      method: "PUT",
      body: JSON.stringify(data),
    }
  );
}

export async function deleteForeshadowing(
  projectId: string,
  itemId: string
): Promise<{ message: string }> {
  return request<{ message: string }>(
    `/worldbuilding/${projectId}/foreshadowing/${itemId}`,
    { method: "DELETE" }
  );
}

export async function listPromotionProposals(
  projectId: string
): Promise<PromotionProposal[]> {
  return request<PromotionProposal[]>(
    `/worldbuilding/${projectId}/promotions`
  );
}

export async function createPromotionProposal(
  projectId: string,
  data: Partial<PromotionProposal>
): Promise<PromotionProposal> {
  return request<PromotionProposal>(
    `/worldbuilding/${projectId}/promotions`,
    {
      method: "POST",
      body: JSON.stringify(data),
    }
  );
}

export async function approvePromotion(
  projectId: string,
  proposalId: string
): Promise<PromotionProposal> {
  return request<PromotionProposal>(
    `/worldbuilding/${projectId}/promotions/${proposalId}/approve`,
    { method: "POST" }
  );
}

export async function rejectPromotion(
  projectId: string,
  proposalId: string,
  reason?: string
): Promise<PromotionProposal> {
  return request<PromotionProposal>(
    `/worldbuilding/${projectId}/promotions/${proposalId}/reject`,
    {
      method: "POST",
      body: JSON.stringify({ reason: reason || "" }),
    }
  );
}

export async function deletePromotionProposal(
  projectId: string,
  proposalId: string
): Promise<{ message: string }> {
  return request<{ message: string }>(
    `/worldbuilding/${projectId}/promotions/${proposalId}`,
    { method: "DELETE" }
  );
}

export async function getPromotionCandidates(
  projectId: string
): Promise<Array<{ seed_id: string; content: string; reference_count: number }>> {
  return request<
    Array<{ seed_id: string; content: string; reference_count: number }>
  >(`/worldbuilding/${projectId}/promotions/candidates`);
}

export async function listStyleProfiles(projectId: string): Promise<StyleProfile[]> {
  return request<StyleProfile[]>(`/style/${projectId}/profiles`);
}

export async function learnStyleFromBook(
  projectId: string,
  file: File,
  name: string
): Promise<StyleProfile> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("name", name);
  const res = await fetch(`${getBackendUrl()}/style/${projectId}/learn`, {
    method: "POST",
    body: formData,
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(error.detail || `请求失败: ${res.status}`);
  }
  return res.json();
}

export async function activateStyleProfile(
  projectId: string,
  profileId: string
): Promise<StyleProfile> {
  return request<StyleProfile>(`/style/${projectId}/profiles/${profileId}/activate`, {
    method: "POST",
  });
}

export async function deleteStyleProfile(
  projectId: string,
  profileId: string
): Promise<{ message: string }> {
  return request<{ message: string }>(
    `/style/${projectId}/profiles/${profileId}`,
    { method: "DELETE" }
  );
}

export async function compareStyleProfiles(
  projectId: string,
  profileIds: string[]
): Promise<StyleConflictReport> {
  return request<StyleConflictReport>(`/style/${projectId}/profiles/compare`, {
    method: "POST",
    body: JSON.stringify({ profile_ids: profileIds }),
  });
}

export async function getStyleConflicts(
  projectId: string,
  profileId: string
): Promise<StyleConflictReport[]> {
  return request<StyleConflictReport[]>(
    `/style/${projectId}/profiles/${profileId}/conflicts`
  );
}

export async function mergeStyleProfiles(
  projectId: string,
  fusionRequest: StyleFusionRequest
): Promise<StyleFusionResult> {
  return request<StyleFusionResult>(`/style/${projectId}/profiles/merge`, {
    method: "POST",
    body: JSON.stringify(fusionRequest),
  });
}

export async function setStyleProfileEditorInfluence(
  projectId: string,
  profileId: string,
  enabled: boolean
): Promise<StyleProfile> {
  return request<StyleProfile>(
    `/style/${projectId}/profiles/${profileId}/editor-influence`,
    {
      method: "POST",
      body: JSON.stringify({ enabled }),
    }
  );
}

export async function rollbackStyleProfile(
  projectId: string,
  profileId: string
): Promise<StyleProfile> {
  return request<StyleProfile>(
    `/style/${projectId}/profiles/${profileId}/rollback`,
    { method: "POST" }
  );
}

export async function getStyleLearningStatus(
  projectId: string,
  profileId: string
): Promise<{
  profile_id: string;
  status: string;
  learning_progress: StyleLearningProgress | null;
  name: string;
}> {
  return request(`/style/${projectId}/learn-status/${profileId}`);
}

export interface TimelineItem {
  id: string;
  name: string;
  parent: string | null;
  branch_chapter: number | null;
  is_active?: boolean;
}

export async function listTimelines(projectId: string): Promise<TimelineItem[]> {
  return request<TimelineItem[]>(`/projects/${projectId}/state/timelines`);
}

export async function switchTimeline(
  projectId: string,
  timelineId: string
): Promise<StoryState> {
  return request<StoryState>(
    `/projects/${projectId}/state/timelines/${timelineId}/switch`,
    { method: "POST" }
  );
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

export async function getGenerationHistory(
  projectId: string
): Promise<GenerationHistoryEntry[]> {
  return request<GenerationHistoryEntry[]>(
    `/projects/${projectId}/chapters/history`
  );
}

export interface SearchResult {
  id: string;
  fact: string;
  entity_id: string;
  tier: string;
  project_id: string;
  chapter_id: string;
  scene_number: number;
  narrative_time: string;
  source_text: string;
  _score?: number;
}

export interface WorkflowExecution {
  id: string;
  project_id: string;
  status: string;
  trigger_type: string;
  current_layer: number;
  total_layers: number;
  error_message: string;
  created_at: string;
  updated_at: string;
  result_context?: WorkflowResultContext;
}

export interface WorkflowReview {
  scene_index: number;
  review_scope?: "scene" | "chapter" | string;
  candidate_text: string;
  violations: Array<WorkflowReviewIssue>;
  attempts: Array<Record<string, unknown>>;
  error_code: string;
  message: string;
  review_version: number;
  review_type?: string;
  retry_count?: number;
  passed?: boolean;
  guidance?: {
    recommended_action: string;
    action_label: string;
    blocking_count: number;
    auto_repair_attempt_count: number;
    items: Array<{
      type: string;
      severity: string;
      detail: string;
      target_span: string;
      expected_behavior: string;
      suggested_strategy: string;
      classification?: string;
      authority_source?: string;
      recommended_route?: string;
    }>;
    protocol_summary?: Record<string, unknown>;
  };
}

export interface WorkflowReviewIssue extends Record<string, unknown> {
  issue_id?: string;
  violation_id?: string;
  type?: string;
  severity?: string;
  detail?: string;
  target_span?: string;
  expected_behavior?: string;
  suggested_strategy?: string;
  classification?: string;
  authority_source?: string;
  recommended_route?: string;
  protocol_version?: string;
  evidence_span?: string;
  blocks_commit?: boolean;
  user_visible?: boolean;
  review_status?: "open" | "ignored" | "resolved" | "pending_recheck" | "pending_validator_retry";
  repair_engine?: "fbi" | "auto_repair" | string;
  fbi_case_id?: string;
  fbi_status?: string;
  scope?: 'prose_text' | 'scene_contract' | 'outline_plan' | 'style_profile' | 'validator_system' | 'advisory';
  repairable_by_text?: boolean;
  repairable_by_contract?: boolean;
  chat?: Array<{ role: "user" | "assistant"; content: string }>;
  revision_diff?: {
    segments: Array<{ operation: "equal" | "add" | "delete"; text: string }>;
    added_chars: number;
    deleted_chars: number;
  };
  // V2 诊断增强
  text_hash?: string;
  is_system_issue?: boolean;
  repair_scope?: string;
  repair_lane?: string;
  review_round?: number;
  is_stale?: boolean;
  localization_status?: "localized" | "missing_anchor" | "missing_evidence" | string;
  location_confidence?: number;
  needs_localization?: boolean;
}

export interface WorkflowCandidate {
  status: string;
  source: string;
  chapter_number: number;
  scene_count: number;
  word_count: number;
  alignment_method?: string;
  alignment_warnings?: string[];
  context_ledger_id?: string;
  candidate_text?: string;
  created_at?: string;
}

export interface WorkflowResultContext {
  review?: WorkflowReview;
  candidate_ready?: boolean;
  candidate?: WorkflowCandidate | null;
  [key: string]: unknown;
}

export interface WorkflowStep {
  id: string;
  agent_name: string;
  layer: number;
  status: string;
  duration_ms: number;
  error_message: string;
  started_at: string | null;
  completed_at: string | null;
  output_snapshot: Record<string, unknown> | null;
}

export interface WorkflowExecutionDetail extends WorkflowExecution {
  steps: WorkflowStep[];
}

export interface WorkflowStepDetail extends WorkflowStep {
  execution_id: string;
  layer: number;
  input_snapshot: Record<string, unknown>;
  output_snapshot: Record<string, unknown>;
}

export interface DagFailedNode {
  node_id: string;
  error: string;
  error_type?: string;
}

export interface DagSkippedNode {
  node_id: string;
  reason: string;
}

export interface WorkflowSSEEvent {
  type: string;
  agent_name?: string;
  layer?: number;
  duration_ms?: number;
  error?: string;
  status?: string;
  agents?: string[];
  candidate?: WorkflowCandidate;
  // 方案16：DAG 事件字段
  phase?: string;
  layer_index?: number;
  total_layers?: number;
  layer_nodes?: string[];
  executed?: string[];
  skipped?: DagSkippedNode[];
  failed_nodes?: DagFailedNode[];
  dag_execution_id?: string;
  reason?: string;
  // P1-W2：持久化降级标志，layer_complete 事件携带。
  // 当后端 _persist_failure_count 超过阈值时为 true，提示用户持久化可能不完整。
  persistence_degraded?: boolean;
}

export async function listWorkflowExecutions(
  projectId: string,
  limit: number = 20,
  offset: number = 0
): Promise<WorkflowExecution[]> {
  return request<WorkflowExecution[]>(
    `/editor/${projectId}/workflows?limit=${limit}&offset=${offset}`
  );
}

export async function getWorkflowExecution(
  projectId: string,
  executionId: string,
  options?: RequestInit
): Promise<WorkflowExecutionDetail> {
  return request<WorkflowExecutionDetail>(`/editor/${projectId}/workflow/${executionId}`, options);
}

export async function resumeWorkflowReview(
  projectId: string,
  executionId: string,
  data: {
    action: "accept_edited" | "retry" | "accept_without_recheck";
    edited_text?: string;
    review_version: number;
  }
): Promise<{ execution_id: string; status: string }> {
  return request(`/editor/${projectId}/workflow/${executionId}/resume-review`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function retryValidator(
  projectId: string,
  executionId: string
): Promise<{ execution_id: string; status: string; gate_passed: boolean; message?: string }> {
  return request(`/editor/${projectId}/workflow/${executionId}/retry-validator`, {
    method: "POST",
  });
}

export async function ignoreWorkflowReviewIssue(
  projectId: string,
  executionId: string,
  issueId: string,
  data: { review_version: number; edited_text?: string }
): Promise<{ execution_id: string; status: string; review: WorkflowReview }> {
  return request(`/editor/${projectId}/workflow/${executionId}/review/issues/${issueId}/ignore`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function chatWorkflowReviewIssue(
  projectId: string,
  executionId: string,
  issueId: string,
  data: {
    review_version: number;
    edited_text: string;
    message?: string;
    repair_strategy?: "append_ending" | "allow_scene_rewrite" | "scene_restructure" | string;
  }
): Promise<{ execution_id: string; status: string; review: WorkflowReview; assistant_message: string }> {
  return request(`/editor/${projectId}/workflow/${executionId}/review/issues/${issueId}/chat`, {
    method: "POST",
    body: JSON.stringify(data),
  }, REVIEW_REPAIR_TIMEOUT);
}

export async function cancelWorkflow(
  projectId: string,
  executionId: string
): Promise<{ execution_id: string; status: string; message?: string }> {
  return request(`/editor/${projectId}/workflow/${executionId}/cancel`, {
    method: "POST",
  });
}

export async function regenerateFromOutline(
  projectId: string,
  chapterNumber: number
): Promise<{ execution_id: string; status: string; chapter_number: number; message?: string }> {
  return request(`/editor/${projectId}/chapters/${chapterNumber}/regenerate-from-outline`, {
    method: "POST",
  });
}

export function workflowSSEUrl(projectId: string, executionId: string): string {
  return `${getBackendUrl()}/editor/${projectId}/workflow/${executionId}/stream`;
}

export async function generateCommentary(
  projectId: string,
  data: { chapter_number: number | null }
): Promise<{ report: Record<string, unknown>; chapter_number: number | null }> {
  return request(`/projects/${projectId}/intelligence/commentary`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function runDetective(
  projectId: string,
  data: { chapter_number: number | null; scan_scope: string }
): Promise<{ report: Record<string, unknown>; chapter_number: number | null; scan_scope: string }> {
  return request(`/projects/${projectId}/intelligence/detective`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function generateBranches(
  projectId: string,
  data: { chapter_number: number; branch_point?: string; branch_count?: number }
): Promise<{ branches: Record<string, unknown>; chapter_number: number }> {
  return request(`/projects/${projectId}/intelligence/branches`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function exportMarkdown(projectId: string): Promise<Blob> {
  const res = await fetch(`${getBackendUrl()}/projects/${projectId}/export/markdown`);
  if (!res.ok) throw new Error(`导出失败: ${res.status}`);
  return res.blob();
}

export async function importMarkdown(projectId: string, content: string): Promise<{
  message: string;
  imported: { chapters: number; characters: number; outline_updated: boolean };
  parsed_info: { project_name: string; chapters_found: number; outline_chapters_found: number; characters_found: number };
}> {
  return request(`/projects/${projectId}/export/import-markdown`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });
}

export async function exportDocx(projectId: string): Promise<Blob> {
  const res = await fetch(`${getBackendUrl()}/projects/${projectId}/export/docx`);
  if (!res.ok) throw new Error(`导出失败: ${res.status}`);
  return res.blob();
}

export async function importDocx(projectId: string, fileBase64: string): Promise<{
  message: string;
  imported: { chapters: number };
  parsed_info: { project_name: string; chapters_found: number; outline_chapters_found: number };
}> {
  return request(`/projects/${projectId}/export/import-docx`, {
    method: "POST",
    body: JSON.stringify({ file_base64: fileBase64 }),
  });
}

export async function getLatestChatSession(
  projectId: string,
  agentType: string,
  chapterNumber?: number
): Promise<{
  session_id: string | null;
  agent_type?: string;
  title?: string;
  chapter_number?: number | null;
  is_pinned?: boolean;
  summary?: string;
  message_count?: number;
  messages: Array<{ role: string; content: string }>;
}> {
  const params = new URLSearchParams();
  if (chapterNumber !== undefined) params.append("chapter_number", String(chapterNumber));
  return request(`/projects/${projectId}/chat-history/latest/${agentType}?${params}`);
}

export async function saveChatMessages(
  projectId: string,
  data: {
    agent_type: string;
    messages: Array<{ role: string; content: string }>;
    session_id?: string | null;
    chapter_number?: number | null;
    title?: string | null;
  }
): Promise<{ session_id: string; message_count: number }> {
  return request(`/projects/${projectId}/chat-history/save`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function listChatSessions(
  projectId: string,
  agentType?: string
): Promise<{
  sessions: Array<{
    session_id: string;
    agent_type: string;
    title: string;
    chapter_number: number | null;
    is_pinned: boolean;
    summary: string;
    message_count: number;
    created_at: string;
    updated_at: string;
  }>;
}> {
  const params = new URLSearchParams();
  if (agentType) params.append("agent_type", agentType);
  return request(`/projects/${projectId}/chat-history/sessions?${params}`);
}

export async function getChatSessionMessages(
  projectId: string,
  sessionId: string
): Promise<{
  session_id: string;
  agent_type: string;
  title: string;
  chapter_number: number | null;
  is_pinned: boolean;
  summary: string;
  message_count: number;
  messages: Array<{ id: string; role: string; content: string; created_at: string }>;
}> {
  return request(`/projects/${projectId}/chat-history/sessions/${sessionId}`);
}

export async function deleteChatSession(
  projectId: string,
  sessionId: string
): Promise<{ message: string }> {
  return request(`/projects/${projectId}/chat-history/sessions/${sessionId}`, {
    method: "DELETE",
  });
}

export async function renameChatSession(
  projectId: string,
  sessionId: string,
  name: string
): Promise<{ session_id: string; title: string }> {
  return request(`/projects/${projectId}/chat-history/sessions/${sessionId}/rename`, {
    method: "PATCH",
    body: JSON.stringify({ name }),
  });
}

export async function pinChatSession(
  projectId: string,
  sessionId: string
): Promise<{ session_id: string; is_pinned: boolean }> {
  return request(`/projects/${projectId}/chat-history/sessions/${sessionId}/pin`, {
    method: "PATCH",
  });
}

export async function unpinChatSession(
  projectId: string,
  sessionId: string
): Promise<{ session_id: string; is_pinned: boolean }> {
  return request(`/projects/${projectId}/chat-history/sessions/${sessionId}/unpin`, {
    method: "PATCH",
  });
}

export async function searchChatSessions(
  projectId: string,
  query: string,
  agentType?: string
): Promise<{
  sessions: Array<{
    session_id: string;
    agent_type: string;
    title: string;
    chapter_number: number | null;
    is_pinned: boolean;
    summary: string;
    message_count: number;
    created_at: string;
    updated_at: string;
  }>;
}> {
  const params = new URLSearchParams({ q: query });
  if (agentType) params.append("agent_type", agentType);
  return request(`/projects/${projectId}/chat-history/sessions/search?${params}`);
}

// ── 伏笔管理系统 API ──────────────────────────────────────

export async function listForeshadowingLines(
  projectId: string,
  status?: string,
  priority?: string
): Promise<{ lines: ForeshadowingLine[]; total: number }> {
  const params = new URLSearchParams();
  if (status) params.append("status", status);
  if (priority) params.append("priority", priority);
  const qs = params.toString();
  return request(`/projects/${projectId}/foreshadowing/lines${qs ? `?${qs}` : ""}`);
}

export async function createForeshadowingLine(
  projectId: string,
  data: {
    name: string;
    description?: string;
    status?: string;
    priority?: string;
    secret?: Record<string, unknown>;
    timeline?: Record<string, unknown>;
    narrative_structure?: Record<string, unknown>;
  }
): Promise<{ line: ForeshadowingLine }> {
  return request(`/projects/${projectId}/foreshadowing/lines`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function transitionForeshadowingStatus(
  projectId: string,
  lineId: string,
  newStatus: string,
  changedBy: string = "author"
): Promise<{ line: ForeshadowingLine }> {
  return request(`/projects/${projectId}/foreshadowing/lines/${lineId}/status`, {
    method: "PATCH",
    body: JSON.stringify({ new_status: newStatus, changed_by: changedBy }),
  });
}

export async function getEvidencePool(
  projectId: string,
  lineId: string
): Promise<Record<string, unknown>> {
  return request(`/projects/${projectId}/foreshadowing/lines/${lineId}/evidence-pool`);
}

export async function getRevealReadiness(
  projectId: string,
  lineId: string
): Promise<RevealReadiness> {
  return request(`/projects/${projectId}/foreshadowing/lines/${lineId}/readiness`);
}

export async function getCognitiveMap(
  projectId: string,
  params: { character?: string; line_id?: string; chapter_number?: number }
): Promise<Record<string, unknown>> {
  const qs = new URLSearchParams();
  if (params.character) qs.append("character", params.character);
  if (params.line_id) qs.append("line_id", params.line_id);
  if (params.chapter_number !== undefined) qs.append("chapter_number", String(params.chapter_number));
  return request(`/projects/${projectId}/foreshadowing/cognitive-map?${qs}`);
}

export async function getForeshadowingDashboard(
  projectId: string,
  chapterNumber?: number
): Promise<ForeshadowingDashboard> {
  const params = chapterNumber !== undefined ? `?chapter_number=${chapterNumber}` : "";
  return request(`/projects/${projectId}/foreshadowing/dashboard${params}`);
}



// ── Story Plan API ──────────────────────────────────────

export async function getStoryPlan(projectId: string): Promise<StoryPlan> {
  return request(`/outline/${projectId}/story-plan`);
}

export interface ProjectArchivePreview {
  valid: boolean;
  project_id: string;
  project_name: string;
  checksum: string;
  current_checksum: string;
  content_changed: boolean;
  differences: Array<{ table: string; current: number; archive: number }>;
  requires_confirmation: boolean;
}

export async function exportProjectArchive(projectId: string): Promise<Blob> {
  const res = await fetch(`${getBackendUrl()}/projects/${projectId}/export/archive`);
  if (!res.ok) throw new Error(`归档导出失败: ${res.status}`);
  return res.blob();
}

export async function previewProjectArchive(
  projectId: string,
  archive: Record<string, unknown>,
): Promise<ProjectArchivePreview> {
  return request(`/projects/${projectId}/export/archive/preview`, {
    method: "POST",
    body: JSON.stringify({ archive }),
  }, 120000);
}

export async function restoreProjectArchive(
  projectId: string,
  archive: Record<string, unknown>,
  confirmation: string,
): Promise<{ restored: boolean; backup_path: string; counts: Record<string, number> }> {
  return request(`/projects/${projectId}/export/archive/restore`, {
    method: "POST",
    body: JSON.stringify({ archive, confirmation }),
  }, 300000);
}

export async function getChapterBlueprint(
  projectId: string,
  chapterNumber: number,
): Promise<ChapterBlueprint> {
  return request(`/outline/${projectId}/story-plan/chapter-blueprint/${chapterNumber}`);
}

export async function saveChapterBlueprint(
  projectId: string,
  chapterNumber: number,
  data: {
    expected_revision: number | null;
    chapter: Record<string, unknown>;
    scenes: Array<Record<string, unknown>>;
    confirm_recovery?: boolean;
  },
): Promise<ChapterBlueprint> {
  return request(`/outline/${projectId}/story-plan/chapter-blueprint/${chapterNumber}`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

export async function updateStoryPlanLayer(
  projectId: string,
  layerName: string,
  data: Record<string, unknown> | unknown[]
): Promise<{ layer: string; version: number }> {
  return request(`/outline/${projectId}/story-plan/layer/${layerName}`, {
    method: "PUT",
    body: JSON.stringify({ data }),
  });
}

export async function updateChapterSpineItem(
  projectId: string,
  chapterNumber: number,
  updates: Record<string, unknown>
): Promise<{ chapter_number: number; version: number }> {
  return request(`/outline/${projectId}/story-plan/chapter-spine/${chapterNumber}`, {
    method: "PATCH",
    body: JSON.stringify(updates),
  });
}

export async function appendChapterSpineItem(
  projectId: string,
  data: { title?: string; summary?: string; pov_character?: string } = {}
): Promise<{
  chapter_number: number;
  chapter: Record<string, unknown>;
  version: number;
}> {
  return request(`/outline/${projectId}/story-plan/chapter-spine`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function deleteChapterSpineItem(
  projectId: string,
  chapterNumber: number
): Promise<{ chapter_number: number; deleted: boolean; version: number }> {
  return request(`/outline/${projectId}/story-plan/chapter-spine/${chapterNumber}`, {
    method: "DELETE",
  });
}

export async function getSceneBrief(
  projectId: string,
  chapterNumber: number
): Promise<SceneBrief | null> {
  return request(`/outline/${projectId}/scene-brief/${chapterNumber}`);
}

export async function saveSceneBrief(
  projectId: string,
  chapterNumber: number,
  briefData: Record<string, unknown>
): Promise<Record<string, unknown>> {
  return request(`/outline/${projectId}/scene-brief/${chapterNumber}`, {
    method: "PUT",
    body: JSON.stringify(briefData),
  });
}

export async function discardSceneBrief(
  projectId: string,
  chapterNumber: number
): Promise<{ discarded: string }> {
  return request(`/outline/${projectId}/scene-brief/${chapterNumber}`, {
    method: "DELETE",
  });
}

export async function generateSceneBrief(
  projectId: string,
  chapterNumber: number
): Promise<Record<string, unknown>> {
  return request(`/outline/${projectId}/scene-brief/${chapterNumber}/generate`, {
    method: "POST",
  });
}

export async function getThreadPlan(
  projectId: string
): Promise<ThreadPlan> {
  return request(`/outline/${projectId}/thread-plan`);
}

export async function getThreadsForChapter(
  projectId: string,
  chapterNumber: number
): Promise<Array<Record<string, unknown>>> {
  return request(`/outline/${projectId}/thread-plan/chapter/${chapterNumber}`);
}

export async function checkOrphanThreads(
  projectId: string
): Promise<Array<Record<string, unknown>>> {
  return request(`/outline/${projectId}/thread-plan/orphans`);
}

// ── Audit & Orchestration API ──────────────────────────

export async function runFullAudit(projectId: string): Promise<Record<string, unknown>> {
  return request(`/outline/${projectId}/audit`, { method: "POST" });
}

export async function listObservations(
  projectId: string,
  optionsOrEntityType: WorldviewObservationListParams | string = {},
  legacyStatus?: string,
): Promise<WorldviewObservationList> {
  // Keep the former `(projectId, entityType?, status?)` call shape working
  // while allowing the review workspace to use server-side pagination.
  const options: WorldviewObservationListParams = typeof optionsOrEntityType === "string"
    ? { entityType: optionsOrEntityType, status: legacyStatus }
    : {
        ...optionsOrEntityType,
        ...(legacyStatus && !optionsOrEntityType.status ? { status: legacyStatus } : {}),
      };
  const params = new URLSearchParams();
  if (options.entityType) params.set("entity_type", options.entityType);
  if (options.status) params.set("status", options.status);
  if (options.reviewState) params.set("review_state", options.reviewState);
  if (options.page !== undefined) params.set("page", String(options.page));
  if (options.pageSize !== undefined) params.set("page_size", String(options.pageSize));
  const qs = params.toString();
  return request<WorldviewObservationList>(
    `/worldbuilding/${projectId}/observations${qs ? `?${qs}` : ""}`
  );
}

export async function promoteObservation(
  projectId: string,
  observationId: string
): Promise<Record<string, unknown>> {
  return request(`/worldbuilding/${projectId}/observations/${observationId}/promote`, {
    method: "POST",
  });
}

export async function rejectObservation(
  projectId: string,
  observationId: string,
  reason?: string
): Promise<Record<string, unknown>> {
  return request(`/worldbuilding/${projectId}/observations/${observationId}/reject`, {
    method: "POST",
    body: JSON.stringify({ reason: reason || "" }),
  });
}

export async function retryWorldviewSync(
  projectId: string
): Promise<Record<string, unknown>> {
  return request(`/worldbuilding/${projectId}/observations/retry-sync`, {
    method: "POST",
  });
}

// ---- Editor Trace API ----

export const editorTraceApi = {
  getSummary: (projectId: string, lastN: number = 10) =>
    request<Record<string, unknown>>(`/editor-trace/summary/${projectId}?last_n=${lastN}`),
  getTrace: (traceId: string) =>
    request<Record<string, unknown>>(`/editor-trace/${traceId}`),
  query: (params: { project_id?: string; chapter_number?: number; execution_id?: string; final_status?: string; v2_compile_status?: string; limit?: number }) =>
    request<{ traces: Record<string, unknown>[]; count: number }>('/editor-trace/query', {
      method: "POST",
      body: JSON.stringify(params),
    }),
  getEffectivenessProof: (projectId: string, executionId: string) =>
    request<EffectivenessProof>(`/editor-trace/effectiveness/${projectId}/${executionId}`),
};

// ---- Re-review API (复检刷新) ----

export const reReviewApi = {
  recheck: (projectId: string, executionId: string, data: { candidate_text: string; scene_index?: number }) =>
    request<{
      violations: Record<string, unknown>[];
      stale_violations: Record<string, unknown>[];
      passed: boolean;
      text_hash: string;
      review_version: number;
      status: string;
      auto_resumed: boolean;
      system_retry_scheduled?: boolean;
      review?: WorkflowReview;
    }>(
      `/editor/${projectId}/workflow/${executionId}/recheck`,
      { method: "POST", body: JSON.stringify(data) },
      REVIEW_REPAIR_TIMEOUT,
    ),
};

// ---- Monitor API (不消耗 LLM token，纯内存/DB 读取) ----

export interface LLMMetricsResponse {
  calls: Record<string, number>;
  latency: Record<string, {
    buckets: Record<string, number>;
    sum_seconds: number;
    count: number;
    avg_seconds: number;
  }>;
  retries: Record<string, number>;
  tokens: Record<string, number>;
  cache_tokens: Record<string, number>;
  cache_summary: {
    cache_hit_tokens: number;
    cache_miss_tokens: number;
    cache_hit_rate: number;
    total_input_tokens: number;
  };
  active_requests: Record<string, number>;
  snapshot_at: string;
}

export interface WorkflowListItem {
  id: string;
  project_id: string;
  status: string;
  trigger_type: string;
  current_layer: number | null;
  total_layers: number | null;
  error_message: string;
  created_at: string;
  updated_at: string;
}

export interface WorkflowStepItem {
  id: string;
  agent_name: string;
  layer: number;
  status: string;
  duration_ms: number | null;
  error_message: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface WorkflowDetail extends WorkflowListItem {
  layers: Record<string, WorkflowStepItem[]>;
}

