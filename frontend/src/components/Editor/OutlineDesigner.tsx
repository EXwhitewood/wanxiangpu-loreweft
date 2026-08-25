import { useState, useRef, useEffect } from "react";
import {
  Send,
  Loader2,
  Check,
  ScrollText,
  X,
  Lock,
  Unlock,
  MessageSquare,
  Sparkles,
  Brain,
  ArrowRight,
  ArrowLeft,
  Lightbulb,
  Search,
  FileEdit,
  GitCompare,
  AlertTriangle,
  Trash2,
  PanelLeft,
  PanelLeftClose,
  Plus,
  ShieldCheck,
  SlidersHorizontal,
  Square,
} from "lucide-react";
import * as api from "@/api/client";
import type {
  OutlineChatMessage as ChatMessage,
  ChatMessageItem,
  OutlineExpansionStatus,
  OutlineStatus,
} from "@/types";
import { useChatSession } from "@/hooks/useChatSession";
import ChatSidebar from "@/components/Chat/ChatSidebar";
import ChatErrorBoundary from "@/components/Chat/ChatErrorBoundary";
import ChatSkeleton from "@/components/Chat/ChatSkeleton";
import ChatSearchBar, { useChatSearch, highlightText } from "@/components/Chat/ChatSearchBar";
import ChatMessageBubble, { DateDivider } from "@/components/Chat/ChatMessageBubble";
import WorkspacePaneHeader from "@/components/Editor/WorkspacePaneHeader";
import {
  getOutlineChatRun,
  outlineChatRunKey,
  startOutlineChatRun,
  stopOutlineChatRun,
  subscribeOutlineChatRun,
} from "@/services/outlineChatRuntime";
import clsx from "clsx";

/* Hallmark · pre-emit critique: P5 H4 E4 S5 R4 V4 · component-scope recovery and audit surfaces */

const GUIDED_TOPICS = [
  "core_theme",
  "protagonist_type",
  "conflict_style",
  "antagonist_design",
  "emotional_tone",
  "chapter_count",
  "special_requirements",
];

const TOPIC_LABELS: Record<string, string> = {
  core_theme: "核心主题",
  protagonist_type: "主角人设",
  conflict_style: "冲突节奏",
  antagonist_design: "对手设计",
  emotional_tone: "情感基调",
  chapter_count: "章节数量",
  special_requirements: "特殊要求",
};

interface GuidedOption {
  label: string;
  value: string;
  recommended: boolean;
  reasoning: string;
}

interface FollowupQuestion {
  question: string;
  placeholder?: string;
}

interface OutlineDesignerProps {
  projectId: string;
  projectName: string;
  outlineData?: Record<string, unknown>;
  contextMode?: "master" | "chapter";
  selectedChapterNumber?: number;
  onClose: () => void;
  onOutlineSaved?: () => void;
}

type Mode = "choose" | "guided" | "free";

type GuidedPhase =
  | "init"
  | "topics"
  | "review"
  | "followup"
  | "generate";

function getOutlineChapterCount(outline: Record<string, unknown> | null): number {
  if (!outline) return 0;
  if (Array.isArray(outline.chapters)) return outline.chapters.length;
  if (Array.isArray(outline.chapter_spine)) return outline.chapter_spine.length;
  return 0;
}

function OutlineImpactSummary({ report }: { report: Record<string, unknown> | null }) {
  const summary = report?.summary && typeof report.summary === "object"
    ? report.summary as Record<string, unknown>
    : {};
  const issues = Array.isArray(report?.issues)
    ? report.issues.filter(
        (item): item is Record<string, unknown> => Boolean(item) && typeof item === "object",
      )
    : [];
  const errors = Number(summary.errors || 0);
  const warnings = Number(summary.warnings || 0);
  const total = Number(summary.total_issues || issues.length);

  return (
    <div className="rounded-lg border border-magic-500/30 bg-magic-500/10 px-4 py-3 text-sm text-magic-300">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 font-medium">
          <AlertTriangle className="h-4 w-4" />
          大纲检查结果
        </div>
        <span className="text-xs text-magic-200/80">{errors > 0 ? "需要先处理" : "可以继续"}</span>
      </div>
      <div className="mt-3 grid grid-cols-3 divide-x divide-magic-500/20 border-y border-magic-500/20 py-2 text-center text-xs">
        <div><span className="block text-base font-semibold">{total}</span>全部提醒</div>
        <div><span className="block text-base font-semibold">{errors}</span>必须处理</div>
        <div><span className="block text-base font-semibold">{warnings}</span>建议留意</div>
      </div>
      {issues.length > 0 ? (
        <ul className="mt-3 divide-y divide-magic-500/20 border-y border-magic-500/20 text-xs leading-5">
          {issues.slice(0, 6).map((issue, index) => (
            <li key={String(issue.item_id || index)} className="py-2">
              {String(issue.message || "大纲中有一处需要确认的内容。")}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-3 text-xs leading-5 text-magic-200/80">当前大纲没有发现会阻止继续操作的问题。</p>
      )}
    </div>
  );
}

export default function OutlineDesigner({
  projectId,
  projectName,
  outlineData: _outlineData,
  contextMode = "master",
  selectedChapterNumber,
  onClose,
  onOutlineSaved,
}: OutlineDesignerProps) {
  const [mode, setMode] = useState<Mode>(contextMode === "chapter" ? "free" : "choose");

  function shouldShowDateDividerMsg(currentDate: string | undefined, prevDate: string | undefined) {
    if (!prevDate || !currentDate) return false;
    try {
      const curr = new Date(currentDate);
      const prev = new Date(prevDate);
      return (
        curr.getDate() !== prev.getDate() ||
        curr.getMonth() !== prev.getMonth() ||
        curr.getFullYear() !== prev.getFullYear()
      );
    } catch {
      return false;
    }
  }

  const chat = useChatSession({
    projectId,
    agentType: "outline_architect",
    chapterNumber: contextMode === "chapter" ? selectedChapterNumber : undefined,
    welcomeMessage: contextMode === "chapter"
      ? `已进入第 ${selectedChapterNumber} 章工作台。你可以告诉我这一章需要调整的冲突、节奏、场景简报或章节钩子。我只会修改当前章节。`
      : `你好！我是「万象谱」大纲架构师，很高兴为你设计《${projectName}》的大纲。\n\n请告诉我你的创作想法：\n- 这个故事的核心冲突是什么？\n- 主要人物有哪些？\n- 你希望什么样的叙事风格？\n- 大致计划多少章节？\n\n我们可以一起讨论，直到你满意为止。`,
  });
  const messages = chat.messages as ChatMessage[];
  const chatSearch = useChatSearch(chat.messages);
  const sending = chat.sending;
  const outlineRunKey = outlineChatRunKey(projectId, contextMode, selectedChapterNumber);
  const [input, setInput] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [parsing, setParsing] = useState(false);
  const [parseError, setParseError] = useState<string | null>(null);
  const [parseSuccess, setParseSuccess] = useState(false);
  const [saveOutcome, setSaveOutcome] = useState<"official" | "draft" | null>(null);
  const [toolStatus, setToolStatus] = useState<string | null>(null);
  const [changeRecordCount, setChangeRecordCount] = useState<number | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);

  const [outlineStatus, setOutlineStatus] = useState<OutlineStatus | null>(null);
  const [expansionStatus, setExpansionStatus] = useState<OutlineExpansionStatus | null>(null);
  const [expansionAction, setExpansionAction] = useState<"resume" | "cancel" | null>(null);
  const [freezing, setFreezing] = useState(false);
  const [outlineActionError, setOutlineActionError] = useState<string | null>(null);
  const [recoveringContinuity, setRecoveringContinuity] = useState(false);

  const [draftPanel, setDraftPanel] = useState<{
    open: boolean;
    loading: boolean;
    diff: {
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
    confirming: boolean;
    discarding: boolean;
    error: string | null;
  }>({ open: false, loading: false, diff: null, confirming: false, discarding: false, error: null });

  const [amendmentPanel, setAmendmentPanel] = useState<{
    open: boolean;
    targetChapters: string;
    modification: string;
    reason: string;
    submitting: boolean;
    impactReport: Record<string, unknown> | null;
    amendmentId: string | null;
    applying: boolean;
  }>({ open: false, targetChapters: "", modification: "", reason: "", submitting: false, impactReport: null, amendmentId: null, applying: false });
  const [auditPanel, setAuditPanel] = useState<{
    open: boolean;
    loading: boolean;
    result: Record<string, unknown> | null;
    error: string | null;
  }>({ open: false, loading: false, result: null, error: null });
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const confirmControllerRef = useRef<AbortController | null>(null);
  const notifiedExpansionRef = useRef<string | null>(null);

  const [guidedPhase, setGuidedPhase] = useState<GuidedPhase>("init");
  const [guidedTopicIndex, setGuidedTopicIndex] = useState(0);
  const [guidedAnswers, setGuidedAnswers] = useState<Record<string, string>>({});
  const [guidedGeneratedAnswers, setGuidedGeneratedAnswers] = useState<Record<string, string>>({});
  const [guidedSupplement, setGuidedSupplement] = useState("");
  const [guidedLLMContent, setGuidedLLMContent] = useState("");
  const [guidedQuestion, setGuidedQuestion] = useState("");
  const [guidedOptions, setGuidedOptions] = useState<GuidedOption[]>([]);
  const [guidedCoreThemes, setGuidedCoreThemes] = useState<string[]>([]);
  const [guidedReviewContent, setGuidedReviewContent] = useState("");
  const [guidedFollowupQuestions, setGuidedFollowupQuestions] = useState<FollowupQuestion[]>([]);
  const [guidedFollowupAnswers, setGuidedFollowupAnswers] = useState<Record<number, string>>({});
  const [guidedCurrentAnswer, setGuidedCurrentAnswer] = useState("");
  const [guidedCustomInput, setGuidedCustomInput] = useState("");
  const [guidedLoading, setGuidedLoading] = useState(false);
  const [guidedGenerating, setGuidedGenerating] = useState(false);
  const [generateOutlineText, setGenerateOutlineText] = useState("");
  const [generateOutlineJson, setGenerateOutlineJson] = useState<Record<string, unknown> | null>(null);
  const [guidedError, setGuidedError] = useState<string | null>(null);
  const [guidedSaved, setGuidedSaved] = useState(false);
  const [guidedDraftSaving, setGuidedDraftSaving] = useState(false);
  const [guidedRecoveryNote, setGuidedRecoveryNote] = useState<string | null>(null);
  const [guidedStaged, setGuidedStaged] = useState(false);
  const [guidedRequestedChapters, setGuidedRequestedChapters] = useState(0);
  const [guidedGeneratedChapters, setGuidedGeneratedChapters] = useState(0);
  const outlineJsonRef = useRef<Record<string, unknown> | null>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chat.messages]);

  useEffect(() => {
    if (!chat.historyLoaded) return;
    if (chat.messages.length > 0 && chat.messages[0].role === "assistant") {
      setMode("free");
    }
    api.getOutlineStatus(projectId).then(setOutlineStatus).catch((error) => {
      setOutlineActionError((error as Error).message || "无法读取大纲状态，请稍后重试");
    });
  }, [chat.historyLoaded, projectId]);

  useEffect(() => {
    if (contextMode !== "master") return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const poll = async () => {
      try {
        const status = await api.getOutlineExpansionStatus(projectId);
        if (disposed) return;
        setExpansionStatus(status);
        if (
          status.status === "completed"
          && status.job_id
          && notifiedExpansionRef.current !== status.job_id
        ) {
          notifiedExpansionRef.current = status.job_id;
          onOutlineSaved?.();
          api.getOutlineStatus(projectId).then(setOutlineStatus).catch(() => {});
        }
        const active = ["started", "already_running", "resumed", "queued", "running", "completing"].includes(status.status);
        timer = setTimeout(poll, active ? 2000 : 8000);
      } catch {
        if (!disposed) timer = setTimeout(poll, 10000);
      }
    };

    void poll();
    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
    };
  }, [contextMode, onOutlineSaved, projectId]);

  useEffect(() => {
    if (!chat.historyLoaded) return;
    const unsubscribe = subscribeOutlineChatRun(
      outlineRunKey,
      (run) => {
        chat.setSending(run.status === "running");
        setToolStatus(run.toolStatus);
        if (run.messages.length > 0) {
          chat.setMessages(run.messages as ChatMessage[]);
        }
      },
      { persistMessages: chat.persistMessages, onOutlineSaved },
    );
    const activeRun = getOutlineChatRun(outlineRunKey);
    if (activeRun?.status === "running") {
      chat.setSending(true);
      setToolStatus(activeRun.toolStatus);
      chat.setMessages(activeRun.messages as ChatMessage[]);
    }
    return unsubscribe;
  }, [chat.historyLoaded, outlineRunKey, chat.persistMessages, onOutlineSaved]);

  // ==================== Guided Mode Logic ====================

  const appendGuidedMessages = (items: ChatMessageItem[]) => {
    const stamped = items.map((item) => ({
      ...item,
      created_at: item.created_at || new Date().toISOString(),
    }));

    chat.setMessages((prev) => {
      const updated = [...prev, ...stamped];
      chat.persistMessages(updated);
      return updated;
    });
  };

  const formatGuidedOptions = (options: GuidedOption[]) => {
    if (!options.length) return "";
    return options
      .map((option, index) => {
        const mark = option.recommended ? "（推荐）" : "";
        const reason = option.reasoning ? `：${option.reasoning}` : "";
        return `${index + 1}. ${option.label}${mark}${reason}`;
      })
      .join("\n");
  };

  const appendGuidedAssistantStep = (
    title: string,
    content: string,
    question?: string,
    options?: GuidedOption[],
  ) => {
    const optionText = options ? formatGuidedOptions(options) : "";
    const parts = [
      `### ${title}`,
      content.trim(),
      question?.trim() ? `**问题**：${question.trim()}` : "",
      optionText ? `**选项**：\n${optionText}` : "",
    ].filter(Boolean);

    appendGuidedMessages([{ role: "assistant", content: parts.join("\n\n") }]);
  };

  const initGuidedMode = async () => {
    setMode("guided");
    setGuidedPhase("init");
    setGuidedLoading(true);
    setGuidedError(null);
    appendGuidedMessages([
      {
        role: "system",
        content: `## 宝宝巴士模式开始\n项目：${projectName}\n系统会自动保存每一步问答，最终生成结果会先进入草稿区。`,
      },
    ]);
    try {
      const result = await api.guidedStep(projectId, "init");
      setGuidedLLMContent(result.content || "");
      setGuidedCoreThemes(result.core_themes || []);
      const question = result.question || "你有什么想补充或调整的吗？";
      setGuidedQuestion(question);
      appendGuidedAssistantStep("初始分析", result.content || "", question);
    } catch (e) {
      const message = (e as Error).message;
      setGuidedError(message);
      appendGuidedMessages([{ role: "assistant", content: `宝宝巴士模式启动失败：${message}` }]);
    } finally {
      setGuidedLoading(false);
    }
  };

  const handleInitContinue = () => {
    const supplement = guidedSupplement.trim();
    const nextAnswers = supplement
      ? { ...guidedAnswers, _supplement: supplement }
      : guidedAnswers;
    appendGuidedMessages([
      {
        role: "user",
        content: supplement ? `初始补充：${supplement}` : "初始分析无补充，继续下一步。",
      },
    ]);
    setGuidedAnswers(nextAnswers);
    loadGuidedTopic(0, nextAnswers, supplement);
  };

  const loadGuidedTopic = async (
    index: number,
    answers: Record<string, string> = guidedAnswers,
    supplement = guidedSupplement,
  ) => {
    setGuidedPhase("topics");
    setGuidedTopicIndex(index);
    setGuidedCurrentAnswer("");
    setGuidedCustomInput("");
    setGuidedLLMContent("");
    setGuidedQuestion("");
    setGuidedOptions([]);
    setGuidedLoading(true);
    setGuidedError(null);

    try {
      const topic = GUIDED_TOPICS[index];
      const result = await api.guidedStep(
        projectId,
        topic,
        answers,
        supplement
      );
      setGuidedLLMContent(result.content || "");
      setGuidedQuestion(result.question || "");
      setGuidedOptions(result.options || []);
      appendGuidedAssistantStep(
        TOPIC_LABELS[topic] || topic,
        result.content || "",
        result.question || "",
        result.options || [],
      );
    } catch (e) {
      const message = (e as Error).message;
      const topic = GUIDED_TOPICS[index];
      setGuidedError(message);
      appendGuidedMessages([{ role: "assistant", content: `加载「${TOPIC_LABELS[topic] || topic}」失败：${message}` }]);
    } finally {
      setGuidedLoading(false);
    }
  };

  const handleTopicAnswer = (value: string) => {
    setGuidedCurrentAnswer(value);
    setGuidedCustomInput("");
  };

  const handleTopicCustomInput = (value: string) => {
    setGuidedCustomInput(value);
    setGuidedCurrentAnswer("");
  };

  const handleTopicNext = () => {
    const topic = GUIDED_TOPICS[guidedTopicIndex];
    const answer = guidedCurrentAnswer || guidedCustomInput.trim();
    if (!answer) return;

    const newAnswers = { ...guidedAnswers, [topic]: answer };
    setGuidedAnswers(newAnswers);
    appendGuidedMessages([
      {
        role: "user",
        content: `【${TOPIC_LABELS[topic] || topic}】${answer}`,
      },
    ]);

    if (guidedTopicIndex < GUIDED_TOPICS.length - 1) {
      loadGuidedTopic(guidedTopicIndex + 1, newAnswers);
    } else {
      startReview(newAnswers);
    }
  };

  const handleTopicPrev = () => {
    if (guidedTopicIndex > 0) {
      loadGuidedTopic(guidedTopicIndex - 1);
    }
  };

  const startReview = async (answers: Record<string, string>) => {
    setGuidedPhase("review");
    setGuidedLoading(true);
    setGuidedError(null);
    try {
      const result = await api.guidedStep(projectId, "review", answers);
      setGuidedReviewContent(result.content || "");
      setGuidedFollowupQuestions(result.followup_questions || []);
      const followupText = (result.followup_questions || [])
        .map((item: FollowupQuestion, index: number) => `${index + 1}. ${item.question}`)
        .join("\n");
      appendGuidedMessages([
        {
          role: "assistant",
          content: [
            "### 复盘与补充问题",
            (result.content || "").trim(),
            followupText ? `**补充问题**：\n${followupText}` : "",
          ].filter(Boolean).join("\n\n"),
        },
      ]);
    } catch (e) {
      const message = (e as Error).message;
      setGuidedError(message);
      appendGuidedMessages([{ role: "assistant", content: `复盘生成失败：${message}` }]);
    } finally {
      setGuidedLoading(false);
    }
  };

  const handleFollowupContinue = () => {
    // Merge followup answers into guidedAnswers
    const merged = { ...guidedAnswers };
    guidedFollowupQuestions.forEach((_, i) => {
      const answer = guidedFollowupAnswers[i]?.trim();
      if (answer) {
        merged[`_followup_${i}`] = answer;
      }
    });
    const followupLines = guidedFollowupQuestions
      .map((question, i) => {
        const answer = guidedFollowupAnswers[i]?.trim();
        return answer ? `${i + 1}. ${question.question}\n${answer}` : "";
      })
      .filter(Boolean)
      .join("\n\n");
    appendGuidedMessages([
      {
        role: "user",
        content: followupLines ? `复盘补充：\n\n${followupLines}` : "复盘补充无新增内容，开始生成大纲草稿。",
      },
    ]);
    setGuidedAnswers(merged);
    startGenerate(merged);
  };

  const startGenerate = async (answers: Record<string, string>) => {
    setGuidedPhase("generate");
    setGuidedGenerating(true);
    setGuidedError(null);
    setGuidedRecoveryNote(null);
    setGuidedSaved(false);
    setGuidedStaged(false);
    appendGuidedMessages([{ role: "assistant", content: "开始整理连续章节规划。系统会根据目标规模自动选择批次，不会用跳号节点代替章节。" }]);
    try {
      const result = await api.guidedStep(projectId, "generate", answers);
      setGuidedGeneratedAnswers(answers);
      setGenerateOutlineText(result.outline_text || "");
      setGenerateOutlineJson(result.outline_json || null);
      setGuidedRecoveryNote(result.recovery_note || null);
      setGuidedStaged(Boolean(result.staged));
      setGuidedRequestedChapters(Number(result.requested_chapters || 0));
      setGuidedGeneratedChapters(Number(result.generated_chapters || 0));

      if (result.saved || result.draft_saved) {
        outlineJsonRef.current = result.outline_json || null;
        persistGuidedSummary(
          answers,
          Boolean(result.staged),
          Number(result.requested_chapters || 0),
          Number(result.generated_chapters || 0),
        );
        setGuidedSaved(true);
        api.getOutlineStatus(projectId).then(setOutlineStatus).catch((error) => {
          setOutlineActionError((error as Error).message || "草稿已保存，但大纲状态读取失败");
        });
        if (onOutlineSaved) onOutlineSaved();
      }
    } catch (e) {
      const message = (e as Error).message;
      setGuidedError(message);
      appendGuidedMessages([{ role: "assistant", content: `大纲草稿生成失败：${message}` }]);
    } finally {
      setGuidedGenerating(false);
    }
  };

  const persistGuidedSummary = (
    answers: Record<string, string>,
    stagedOverride?: boolean,
    requestedChapters = 0,
    generatedChapters = 0,
  ) => {
    const guidedSummary = Object.entries(answers)
      .filter(([key]) => !key.startsWith("_followup_"))
      .map(([key, value]) => `- ${key}: ${value}`)
      .join("\n");

    const followupSummary = Object.entries(answers)
      .filter(([key]) => key.startsWith("_followup_"))
      .map(([key, value]) => `- ${key.replace("_followup_", "后续问题")}: ${value}`)
      .join("\n");

    const staged = stagedOverride ?? guidedStaged;
    const target = requestedChapters || guidedRequestedChapters;
    const materialized = generatedChapters || guidedGeneratedChapters;
    const resultSummary = staged
      ? `已启动 ${target || "多章节"} 章规划任务，当前 ${materialized}/${target || "目标"} 章；全部完成并通过连续性校验后才会写入正式大纲。`
      : "已生成完整大纲草稿。";
    const summaryContent = `## 大纲架构师 - 宝宝巴士模式完成记录\n\n### 创作构想:\n${guidedSummary}\n${followupSummary ? `\n### 后续补充:\n${followupSummary}` : ""}\n\n### 生成结果:\n${resultSummary}`;

    appendGuidedMessages([
      {
        role: "assistant",
        content: staged
          ? `多章节规划任务已启动：${materialized}/${target} 章。请在进度面板查看，失败后可继续。`
          : "宝宝巴士模式已完成！完整大纲已保存为草稿，请在「草稿」中查看和确认。",
      },
      { role: "system", content: summaryContent },
    ]);
  };

  const handleSaveGeneratedDraft = async () => {
    setGuidedDraftSaving(true);
    setGuidedError(null);
    try {
      let outlineJson = generateOutlineJson;
      if (outlineJson) {
        await api.saveDraftOutline(projectId, outlineJson);
      } else {
        const parsed = await api.parseDraftOutline(projectId, generateOutlineText);
        outlineJson = parsed.outline_data || null;
        setGenerateOutlineJson(outlineJson);
        setGuidedRecoveryNote(parsed.recovery_note || null);
      }
      outlineJsonRef.current = outlineJson;
      persistGuidedSummary(guidedGeneratedAnswers);
      setGuidedSaved(true);
      api.getOutlineStatus(projectId).then(setOutlineStatus).catch(() => {});
      if (onOutlineSaved) onOutlineSaved();
    } catch (e) {
      setGuidedError((e as Error).message);
    } finally {
      setGuidedDraftSaving(false);
    }
  };



  // ==================== Free Chat Logic ====================

  const initFreeChat = () => {
    setMode("free");
    chat.createNewSession();
  };

  const handleSend = () => {
    const text = input.trim();
    if (!text || sending) return;

    const newMessages = [...messages, { role: "user" as const, content: text }];
    chat.setMessages(newMessages);
    setInput("");
    setParseError(null);
    setParseSuccess(false);
    setSaveOutcome(null);
    setToolStatus(null);

    startOutlineChatRun({
      key: outlineRunKey,
      projectId,
      messages: newMessages,
      contextMode,
      selectedChapterNumber,
      persistMessages: (updated) => {
        chat.setMessages(updated as ChatMessage[]);
        chat.persistMessages(updated);
      },
      onOutlineSaved,
    });
  };

  const handleStopGeneration = () => {
    if (sending) {
      stopOutlineChatRun(outlineRunKey);
    } else if (confirming) {
      confirmControllerRef.current?.abort();
    }
  };

  const handleResumeExpansion = async () => {
    setExpansionAction("resume");
    setOutlineActionError(null);
    try {
      const status = await api.resumeOutlineExpansion(projectId);
      setExpansionStatus(status);
    } catch (error) {
      setOutlineActionError((error as Error).message || "章节扩展续跑失败");
    } finally {
      setExpansionAction(null);
    }
  };

  const handleCancelExpansion = async () => {
    setExpansionAction("cancel");
    setOutlineActionError(null);
    try {
      const status = await api.cancelOutlineExpansion(projectId);
      setExpansionStatus(status);
    } catch (error) {
      setOutlineActionError((error as Error).message || "章节扩展取消失败");
    } finally {
      setExpansionAction(null);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleConfirmOutline = async () => {
    setConfirming(true);
    const controller = new AbortController();
    confirmControllerRef.current = controller;
    try {
      const confirmMsg: ChatMessage = {
        role: "user",
        content: "我对大纲很满意，请输出完整的 JSON 格式大纲。",
      };
      const newMessages = [...messages, confirmMsg];
      chat.setMessages(newMessages);

      const result = await api.outlineChat(
        projectId,
        newMessages,
        contextMode,
        selectedChapterNumber,
        controller.signal,
      );
      const assistantMsg: ChatMessage = {
        role: "assistant",
        content: result.response,
      };
      chat.setMessages((prev) => [...prev, assistantMsg]);
    } catch (e) {
      const error = e as DOMException;
      const timeoutMessage = error?.name === "AbortError"
        ? "大纲生成时间较长，连接等待超时。请重试；已生成的内容不会自动覆盖现有大纲。"
        : (e as Error).message;
      const message = controller.signal.aborted
        ? "已停止生成。现有大纲不会被自动覆盖。"
        : timeoutMessage;
      chat.setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: `生成大纲时出错：${message}`,
        },
      ]);
    } finally {
      if (confirmControllerRef.current === controller) {
        confirmControllerRef.current = null;
      }
      setConfirming(false);
    }
  };

  const handleParseAndSave = async () => {
    const lastAssistantMsg = [...messages]
      .reverse()
      .find((m) => m.role === "assistant");
    if (!lastAssistantMsg) return;

    setParsing(true);
    setParseError(null);
    setParseSuccess(false);
    setSaveOutcome(null);
    setChangeRecordCount(null);
    try {
      const result = await api.parseOutline(projectId, lastAssistantMsg.content);
      setParseSuccess(true);
      setSaveOutcome("official");
      if (result.change_records !== undefined) {
        setChangeRecordCount(result.change_records);
      }
      api.getOutlineStatus(projectId).then(setOutlineStatus).catch((error) => {
        setOutlineActionError((error as Error).message || "大纲已保存，但状态读取失败");
      });
      if (onOutlineSaved) onOutlineSaved();
    } catch (e) {
      setParseError((e as Error).message);
    } finally {
      setParsing(false);
    }
  };

  const handleSaveAsDraft = async () => {
    const lastAssistantMsg = [...messages]
      .reverse()
      .find((m) => m.role === "assistant");
    if (!lastAssistantMsg) return;

    setParsing(true);
    setParseError(null);
    setParseSuccess(false);
    setSaveOutcome(null);
    try {
      const parseResult = await api.parseDraftOutline(projectId, lastAssistantMsg.content);
      setParseSuccess(true);
      setSaveOutcome("draft");
      setGuidedRecoveryNote(parseResult.recovery_note || null);
      chat.setMessages((prev) => [
        ...prev,
        { role: "assistant", content: "大纲已保存为草案，正式大纲未受影响。你可以查看差异，确认后再使其生效。" },
      ]);
      api.getOutlineStatus(projectId).then(setOutlineStatus).catch((error) => {
        setOutlineActionError((error as Error).message || "草稿已保存，但大纲状态读取失败");
      });
      if (onOutlineSaved) onOutlineSaved();
    } catch (e) {
      setParseError((e as Error).message);
    } finally {
      setParsing(false);
    }
  };

  const handlePreviewDraft = async () => {
    setDraftPanel((prev) => ({ ...prev, open: true, loading: true, diff: null, error: null }));
    try {
      const result = await api.readDraftOutline(projectId);
      setDraftPanel((prev) => ({
        ...prev,
        loading: false,
        diff: result.diff_summary,
        error: null,
      }));
    } catch (error) {
      setDraftPanel((prev) => ({
        ...prev,
        loading: false,
        error: (error as Error).message || "草稿读取失败，请重试",
      }));
    }
  };

  const handleConfirmDraft = async (): Promise<boolean> => {
    setDraftPanel((prev) => ({ ...prev, confirming: true, error: null }));
    try {
      const result = await api.confirmDraftOutline(projectId);
      setDraftPanel((prev) => ({ ...prev, open: false, confirming: false }));
      if (result.change_records !== undefined) {
        setChangeRecordCount(result.change_records);
      }
      api.getOutlineStatus(projectId).then(setOutlineStatus).catch((error) => {
        setOutlineActionError((error as Error).message || "草案已确认，但状态读取失败");
      });
      if (onOutlineSaved) onOutlineSaved();
      chat.setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `✅ 草案已确认为正式大纲！${result.change_records ? `本次变更产生了 ${result.change_records} 处修改记录。` : ""}` },
      ]);
      return true;
    } catch (error) {
      setDraftPanel((prev) => ({
        ...prev,
        confirming: false,
        error: (error as Error).message || "草稿确认失败，正式大纲未发生变化",
      }));
      return false;
    }
  };

  const handleConfirmDraftAndStartChat = async () => {
    const ok = await handleConfirmDraft();
    if (!ok) return;
    setMode("free");
    setGuidedSaved(false);
    chat.setMessages([
      {
        role: "assistant",
        content: "草稿已保存为正式大纲。现在可以继续告诉我你想微调的地方，我会直接基于这份唯一的大纲总文件修改。",
      },
    ]);
  };

  const handleDiscardDraft = async () => {
    setDraftPanel((prev) => ({ ...prev, discarding: true, error: null }));
    try {
      await api.discardDraftOutline(projectId);
      setDraftPanel((prev) => ({ ...prev, open: false, discarding: false }));
      api.getOutlineStatus(projectId).then(setOutlineStatus).catch((error) => {
        setOutlineActionError((error as Error).message || "草案已丢弃，但状态读取失败");
      });
      chat.setMessages((prev) => [
        ...prev,
        { role: "assistant", content: "草案已丢弃，正式大纲未受影响。" },
      ]);
    } catch (error) {
      setDraftPanel((prev) => ({
        ...prev,
        discarding: false,
        error: (error as Error).message || "草稿丢弃失败，请重试",
      }));
    }
  };

  const handleRecoverContinuity = async () => {
    if (recoveringContinuity) return;
    setRecoveringContinuity(true);
    setOutlineActionError(null);
    try {
      const result = await api.recoverOutlineContinuityDraft(projectId);
      const nextStatus = await api.getOutlineStatus(projectId);
      setOutlineStatus(nextStatus);
      chat.setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: `${result.message}\n\n补齐章节均标记为“待细化”，请先查看草稿差异；只有你确认草稿后，正式大纲才会更新。`,
        },
      ]);
      await handlePreviewDraft();
      onOutlineSaved?.();
    } catch (error) {
      setOutlineActionError((error as Error).message || "连续草稿生成失败，正式大纲未发生变化");
    } finally {
      setRecoveringContinuity(false);
    }
  };

  const handleSubmitAmendment = async () => {
    if (!amendmentPanel.modification.trim()) return;
    setAmendmentPanel((prev) => ({ ...prev, submitting: true }));
    try {
      const chapters = amendmentPanel.targetChapters
        .split(/[,，\s]+/)
        .map(Number)
        .filter((n) => !isNaN(n));
      const result = await api.createAmendment(projectId, {
        target_chapters: chapters,
        modification: amendmentPanel.modification,
        reason: amendmentPanel.reason,
      });
      setAmendmentPanel((prev) => ({
        ...prev,
        submitting: false,
        impactReport: result.impact_report,
        amendmentId: result.amendment_id,
      }));
    } catch (error) {
      setOutlineActionError((error as Error).message || "修订案提交失败，请重试");
      setAmendmentPanel((prev) => ({ ...prev, submitting: false }));
    }
  };

  const handleApplyAmendment = async () => {
    if (!amendmentPanel.amendmentId) return;
    setAmendmentPanel((prev) => ({ ...prev, applying: true }));
    try {
      await api.applyAmendment(projectId, amendmentPanel.amendmentId);
      setAmendmentPanel({
        open: false,
        targetChapters: "",
        modification: "",
        reason: "",
        submitting: false,
        impactReport: null,
        amendmentId: null,
        applying: false,
      });
      api.getOutlineStatus(projectId).then(setOutlineStatus).catch((error) => {
        setOutlineActionError((error as Error).message || "修订案已应用，但状态读取失败");
      });
      if (onOutlineSaved) onOutlineSaved();
      chat.setMessages((prev) => [
        ...prev,
        { role: "assistant", content: "✅ 修订案已应用，大纲已更新并重新冻结。" },
      ]);
    } catch (error) {
      setOutlineActionError((error as Error).message || "修订案应用失败，大纲未发生变化");
      setAmendmentPanel((prev) => ({ ...prev, applying: false }));
    }
  };

  const handleFreeze = async () => {
    setFreezing(true);
    setOutlineActionError(null);
    try {
      await api.freezeOutline(projectId);
      setOutlineStatus(await api.getOutlineStatus(projectId));
      onOutlineSaved?.();
    } catch (e) {
      setOutlineActionError((e as Error).message || "冻结大纲失败，请重试");
    } finally {
      setFreezing(false);
    }
  };

  const handleUnfreeze = async () => {
    setFreezing(true);
    setOutlineActionError(null);
    try {
      await api.unfreezeOutline(projectId);
      setOutlineStatus(await api.getOutlineStatus(projectId));
      onOutlineSaved?.();
    } catch (e) {
      setOutlineActionError((e as Error).message || "解冻大纲失败，请重试");
    } finally {
      setFreezing(false);
    }
  };

  const handleRunAudit = async () => {
    setAuditPanel({ open: true, loading: true, result: null, error: null });
    try {
      const result = await api.runFullAudit(projectId);
      setAuditPanel({ open: true, loading: false, result, error: null });
    } catch (error) {
      setAuditPanel({
        open: true,
        loading: false,
        result: null,
        error: (error as Error).message || "大纲审查失败，请重试",
      });
    }
  };

  const hasJsonInResponse = messages.some(
    (m) => m.role === "assistant" && m.content.includes("```json")
  );

  // ==================== Render: Mode Choose ====================

  const renderModeChoose = () => (
    <div className="outline-mode-choose relative flex min-h-0 flex-1 overflow-y-auto px-6 py-8 sm:px-8">
      <div className="relative mx-auto flex w-full max-w-2xl flex-col">
        <div className="flex items-start justify-between gap-6 border-b border-[var(--border-subtle)] pb-6">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-[10px] font-semibold tracking-[0.18em] text-[var(--color-accent)]">
              <span className="h-1.5 w-1.5 rounded-full bg-[var(--color-highlight)]" />
              OUTLINE STUDIO
            </div>
            <h1 className="mt-4 text-2xl font-semibold tracking-tight text-[var(--color-ink-strong)]">
              从这里开始搭建
            </h1>
            <p className="mt-2 max-w-md text-sm leading-6 text-[var(--color-ink-muted)]">
              先选择进入方式，再一步步把《{projectName}》变成可编辑的故事骨架。
            </p>
          </div>
          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-[var(--border-emphasis)] bg-[var(--color-accent-soft)] text-[var(--color-accent)]">
            <ScrollText className="h-5 w-5" />
          </div>
        </div>

        <div className="outline-mode-canvas mt-8">
          <div className="outline-mode-canvas__content relative min-w-0">
            <div className="mb-4 flex items-center justify-between gap-3">
              <h2 className="text-xs font-semibold text-[var(--color-ink-strong)]">选择进入方式</h2>
              <span className="text-[10px] font-medium text-[var(--color-accent)]">推荐路径已标记</span>
            </div>

            <button
              onClick={initGuidedMode}
              className="group relative w-full overflow-hidden rounded-xl border-2 border-[var(--color-accent)] bg-[var(--color-accent-soft)] p-5 text-left shadow-[var(--shadow-raised)] transition-colors duration-200 hover:bg-[var(--surface-raised)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)] active:bg-[var(--surface-tool)]"
            >
              <div className="flex items-start gap-3">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-[var(--color-accent)] text-[var(--color-on-accent)]">
                  <Sparkles className="h-5 w-5" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-[15px] font-semibold text-[var(--color-ink-strong)]">引导构建</span>
                    <span className="rounded-full bg-[var(--color-highlight)] px-2 py-0.5 text-[10px] font-semibold text-[var(--color-ink-strong)]">推荐</span>
                  </div>
                  <p className="mt-2 text-xs leading-5 text-[var(--color-ink)]">
                    架构师会先读取项目上下文，再用关键问题帮你搭起主题、人物与冲突骨架。
                  </p>
                </div>
                <ArrowRight className="mt-1 h-4 w-4 shrink-0 text-[var(--color-accent)] transition-transform duration-200 group-hover:translate-x-1" />
              </div>
              <div className="mt-5 flex items-center justify-between border-t border-[var(--color-accent)]/20 pt-3 text-[10px] font-semibold text-[var(--color-accent)]">
                <span>从模糊想法到可编辑骨架</span>
                <span className="flex items-center gap-1">开始引导 <ArrowRight className="h-3 w-3" /></span>
              </div>
            </button>

            <div className="my-4 flex items-center gap-3 text-[10px] text-[var(--color-ink-muted)]">
              <span className="h-px flex-1 bg-[var(--border-subtle)]" />
              已有明确构思
              <span className="h-px flex-1 bg-[var(--border-subtle)]" />
            </div>

            <button
              onClick={initFreeChat}
              className="group flex w-full items-start gap-3 rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-4 text-left transition-colors duration-200 hover:bg-[var(--color-highlight-soft)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)] active:bg-[var(--surface-tool)]"
            >
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-tool)] text-[var(--color-ink)]">
                <MessageSquare className="h-4 w-4" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-sm font-semibold text-[var(--color-ink-strong)]">自由对话</span>
                  <span className="text-[10px] font-medium text-[var(--color-ink-muted)]">已有明确方向</span>
                </div>
                <p className="mt-1 text-xs leading-5 text-[var(--color-ink-muted)]">直接讨论结构、冲突、节奏与章节细节，边聊边调整。</p>
              </div>
              <ArrowRight className="mt-1 h-4 w-4 shrink-0 text-[var(--color-ink-muted)] transition-transform duration-200 group-hover:translate-x-1 group-hover:text-[var(--color-ink)]" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );

  // ==================== Render: LLM Analysis Bubble ====================

  const renderLLMAnalysis = (content: string, loading: boolean) => (
    <div className="mx-auto max-w-lg">
      <div className="flex items-start gap-3">
        <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-magic-500/20 mt-0.5">
          {loading ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin text-magic-400" />
          ) : (
            <Brain className="h-3.5 w-3.5 text-magic-400" />
          )}
        </div>
        <div className="flex-1">
          <span className="text-xs font-medium text-magic-400">架构师的思考</span>
          {loading ? (
            <div className="mt-2 flex items-center gap-2 text-sm text-pine-700">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              正在分析你的项目...
            </div>
          ) : (
            <div className="mt-2 rounded-2xl rounded-tl-sm bg-white/50 px-4 py-3 text-sm text-pine-600 leading-relaxed whitespace-pre-wrap">
              {content}
            </div>
          )}
        </div>
      </div>
    </div>
  );

  // ==================== Render: Init Phase ====================

  const renderInitPhase = () => (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="border-b border-pine-200/40 px-5 py-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-[var(--color-accent)]" />
            <span className="text-xs font-medium text-magic-400">
              引导构建
            </span>
          </div>
          <span className="text-xs text-pine-700">第1步：项目分析</span>
        </div>
        <div className="mt-2 h-1 rounded-full bg-white">
          <div className="h-1 w-1/12 rounded-full bg-magic-500" />
        </div>
      </div>

      {guidedError && (
        <div className="mx-5 mt-3 rounded-lg border border-crimson-500/30 bg-crimson-500/10 px-4 py-2 text-xs text-crimson-400">
          {guidedError}
        </div>
      )}
      {guidedRecoveryNote && !guidedSaved && !guidedGenerating && (
        <div className="mx-5 mt-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-2 text-xs leading-relaxed text-amber-700">
          {guidedRecoveryNote}
        </div>
      )}

      <div className="flex-1 overflow-auto px-5 py-6">
        {renderLLMAnalysis(guidedLLMContent, guidedLoading)}

        {!guidedLoading && guidedCoreThemes.length > 0 && (
          <div className="mx-auto mt-4 max-w-lg">
            <div className="flex flex-wrap gap-2">
              {guidedCoreThemes.map((theme, i) => (
                <span
                  key={i}
                  className="flex items-center gap-1 rounded-full bg-magic-500/10 border border-magic-500/20 px-3 py-1 text-xs text-magic-300"
                >
                  <Lightbulb className="h-3 w-3" />
                  {theme}
                </span>
              ))}
            </div>
          </div>
        )}

        {!guidedLoading && guidedQuestion && (
          <div className="mx-auto mt-5 max-w-lg">
            <h3 className="mb-3 text-sm font-medium text-pine-600">
              {guidedQuestion}
            </h3>
            <textarea
              value={guidedSupplement}
              onChange={(e) => setGuidedSupplement(e.target.value)}
              placeholder="请输入你的补充想法（也可以留空直接继续）..."
              rows={4}
              className="w-full rounded-xl border border-pine-200 bg-white/30 px-4 py-3 text-sm text-pine-600 outline-none transition-colors focus:border-magic-500/50 placeholder:text-pine-800 resize-none"
            />
          </div>
        )}
      </div>

      {!guidedLoading && (
        <div className="outline-guided-footer border-t border-pine-200/40 px-5 py-3">
          <div className="flex items-center justify-between">
            <button
              onClick={() => setMode("choose")}
              className="rounded-lg border border-pine-200 bg-white/30 px-4 py-2 text-xs text-pine-700 transition-colors hover:bg-white/50 hover:text-pine-600"
            >
              重新选择模式
            </button>
            <button
              onClick={handleInitContinue}
              className="flex items-center gap-1.5 rounded-lg bg-magic-500 px-5 py-2 text-xs font-medium text-pine-950 transition-colors hover:bg-magic-400"
            >
              继续
              <ArrowRight className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      )}
    </div>
  );

  // ==================== Render: Topic Questions ====================

  const renderTopicQuestions = () => {
    const topic = GUIDED_TOPICS[guidedTopicIndex];
    const topicLabel = TOPIC_LABELS[topic];
    const hasAnswer = guidedCurrentAnswer || guidedCustomInput.trim();
    const progress = guidedTopicIndex + 1; // Current topic is answering
    const totalSteps = GUIDED_TOPICS.length + 2; // topics + init + review

    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="border-b border-pine-200/40 px-5 py-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-[var(--color-accent)]" />
              <span className="text-xs font-medium text-magic-400">
                引导构建
              </span>
            </div>
            <span className="text-xs text-pine-700">
              {progress + 1}/{totalSteps} · {topicLabel}
            </span>
          </div>
          <div className="mt-2 h-1 rounded-full bg-white">
            <div
              className="h-1 rounded-full bg-magic-500"
              style={{ width: `${((progress + 1) / totalSteps) * 100}%` }}
            />
          </div>
        </div>

        {guidedError && (
          <div className="mx-5 mt-3 rounded-lg border border-crimson-500/30 bg-crimson-500/10 px-4 py-2 text-xs text-crimson-400">
            {guidedError}
          </div>
        )}

        <div className="flex-1 overflow-auto px-5 py-6">
          {renderLLMAnalysis(guidedLLMContent, guidedLoading)}

          {!guidedLoading && guidedQuestion && (
            <div className="mx-auto mt-4 max-w-lg">
              <h3 className="mb-4 text-sm font-medium text-pine-600 leading-relaxed">
                {guidedQuestion}
              </h3>

              <div className="space-y-2">
                {guidedOptions.map((opt) => (
                  <button
                    key={opt.value}
                    onClick={() => handleTopicAnswer(opt.value)}
                    className={clsx(
                      "flex w-full items-start gap-3 rounded-xl border p-3.5 text-left transition-colors",
                      guidedCurrentAnswer === opt.value
                        ? "border-magic-500/50 bg-magic-500/10"
                        : "border-pine-200/40 bg-white/30 hover:bg-white/40"
                    )}
                  >
                    <div
                      className={clsx(
                        "mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border",
                        guidedCurrentAnswer === opt.value
                          ? "border-magic-500 bg-magic-500"
                          : "border-pine-200"
                      )}
                    >
                      {guidedCurrentAnswer === opt.value && (
                        <Check className="h-3 w-3 text-pine-950" />
                      )}
                    </div>
                    <div className="flex-1">
                      <div className="flex items-center gap-2">
                        <span
                          className={clsx(
                            "text-sm",
                            guidedCurrentAnswer === opt.value
                              ? "text-magic-300"
                              : "text-pine-600"
                          )}
                        >
                          {opt.label}
                        </span>
                        {opt.recommended && (
                          <span className="flex items-center gap-0.5 rounded-full bg-magic-500/20 px-1.5 py-0.5 text-xs text-magic-400">
                            <Sparkles className="h-2.5 w-2.5" />
                            推荐
                          </span>
                        )}
                      </div>
                      {opt.reasoning && (
                        <p className="mt-1 text-xs text-pine-700">
                          {opt.reasoning}
                        </p>
                      )}
                    </div>
                  </button>
                ))}
              </div>

              <div className="mt-4">
                <div className="mb-2 flex items-center gap-2">
                  <div
                    className={clsx(
                      "h-px flex-1",
                      guidedCustomInput.trim() ? "bg-magic-500/30" : "bg-white"
                    )}
                  />
                  <span
                    className={clsx(
                      "text-xs",
                      guidedCustomInput.trim() ? "text-magic-400" : "text-pine-800"
                    )}
                  >
                    或自定义
                  </span>
                  <div
                    className={clsx(
                      "h-px flex-1",
                      guidedCustomInput.trim() ? "bg-magic-500/30" : "bg-white"
                    )}
                  />
                </div>
                <textarea
                  value={guidedCustomInput}
                  onChange={(e) =>
                    handleTopicCustomInput(e.target.value)
                  }
                  placeholder="输入你自己的想法..."
                  rows={2}
                  className="w-full rounded-lg border border-pine-200 bg-white/30 px-3 py-2 text-sm text-pine-600 outline-none transition-colors focus:border-magic-500/50 placeholder:text-pine-800 resize-none"
                />
              </div>
            </div>
          )}
        </div>

        {!guidedLoading && (
          <div className="outline-guided-footer border-t border-pine-200/40 px-5 py-3">
            <div className="flex items-center justify-between">
              <div className="flex gap-2">
                <button
                  onClick={handleTopicPrev}
                  disabled={guidedTopicIndex === 0}
                  className="flex items-center gap-1 rounded-lg border border-pine-200 bg-white/30 px-4 py-2 text-xs text-pine-600 transition-colors hover:bg-white/50 hover:text-pine-500 disabled:opacity-40"
                >
                  <ArrowLeft className="h-3.5 w-3.5" />
                  上一步
                </button>
                <button
                  onClick={() => setMode("choose")}
                  className="rounded-lg border border-pine-200 bg-white/30 px-4 py-2 text-xs text-pine-700 transition-colors hover:bg-white/50 hover:text-pine-600"
                >
                  重新选择
                </button>
              </div>
              <button
                onClick={handleTopicNext}
                disabled={!hasAnswer}
                className={clsx(
                  "flex items-center gap-1.5 rounded-lg px-5 py-2 text-xs font-medium transition-colors",
                  hasAnswer
                    ? "bg-magic-500 text-pine-950 hover:bg-magic-400"
                    : "bg-white text-pine-700"
                )}
              >
                {guidedTopicIndex < GUIDED_TOPICS.length - 1
                  ? "下一步"
                  : "完成问答"}
                <ArrowRight className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
        )}
      </div>
    );
  };

  // ==================== Render: Review Phase ====================

  const renderReviewPhase = () => (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="border-b border-pine-200/40 px-5 py-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-[var(--color-accent)]" />
            <span className="text-xs font-medium text-magic-400">
              引导构建
            </span>
          </div>
          <span className="text-xs text-pine-700">架构师审阅中</span>
        </div>
        <div className="mt-2 h-1 rounded-full bg-white">
          <div className="h-1 w-3/4 rounded-full bg-magic-500" />
        </div>
      </div>

      {guidedError && (
        <div className="mx-5 mt-3 rounded-lg border border-crimson-500/30 bg-crimson-500/10 px-4 py-2 text-xs text-crimson-400">
          {guidedError}
        </div>
      )}

      <div className="flex-1 overflow-auto px-5 py-6">
        {renderLLMAnalysis(guidedReviewContent, guidedLoading)}

        {!guidedLoading &&
          guidedFollowupQuestions.length > 0 &&
          !generateOutlineText && (
            <div className="mx-auto mt-5 max-w-lg">
              <div className="flex items-center gap-2 mb-3">
                <Search className="h-4 w-4 text-magic-400" />
                <h3 className="text-sm font-semibold text-magic-400">
                  架构师还想了解更多
                </h3>
              </div>
              <div className="space-y-4">
                {guidedFollowupQuestions.map((q, i) => (
                  <div key={i}>
                    <label className="mb-1.5 block text-sm text-pine-600">
                      {i + 1}. {q.question}
                    </label>
                    <textarea
                      value={guidedFollowupAnswers[i] || ""}
                      onChange={(e) =>
                        setGuidedFollowupAnswers((prev) => ({
                          ...prev,
                          [i]: e.target.value,
                        }))
                      }
                      placeholder={q.placeholder || "输入你的回答..."}
                      rows={2}
                      className="w-full rounded-lg border border-pine-200 bg-white/30 px-3 py-2 text-sm text-pine-600 outline-none transition-colors focus:border-magic-500/50 placeholder:text-pine-800 resize-none"
                    />
                  </div>
                ))}
              </div>
            </div>
          )}

        {!guidedLoading && guidedFollowupQuestions.length === 0 && !guidedError && (
          <div className="mx-auto mt-5 max-w-lg text-center">
            <p className="text-sm text-pine-700">架构师认为你的设定已经比较完善，没有问题需要补充。</p>
          </div>
        )}
      </div>

      {!guidedLoading && (
        <div className="outline-guided-footer border-t border-pine-200/40 px-5 py-3">
          <div className="flex items-center justify-between">
            <button
              onClick={() => {
                setGuidedPhase("topics");
                setGuidedTopicIndex(GUIDED_TOPICS.length - 1);
              }}
              className="flex items-center gap-1 rounded-lg border border-pine-200 bg-white/30 px-4 py-2 text-xs text-pine-600 transition-colors hover:bg-white/50 hover:text-pine-500"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
              返回修改
            </button>
            <button
              onClick={handleFollowupContinue}
              className="flex items-center gap-1.5 rounded-lg bg-magic-500 px-5 py-2 text-xs font-medium text-pine-950 transition-colors hover:bg-magic-400"
            >
              <Sparkles className="h-3.5 w-3.5" />
              生成大纲
            </button>
          </div>
        </div>
      )}
    </div>
  );

  // ==================== Render: Generate Phase ====================

  const renderGeneratePhase = () => (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="border-b border-pine-200/40 px-5 py-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-[var(--color-accent)]" />
            <span className="text-xs font-medium text-magic-400">
              引导构建
            </span>
          </div>
          <span className="text-xs text-pine-700">生成大纲</span>
        </div>
        <div className="mt-2 h-1 rounded-full bg-white">
          <div
            className={clsx(
              "h-1 rounded-full bg-magic-500",
              guidedGenerating ? "animate-pulse motion-reduce:animate-none w-5/6" : "w-full"
            )}
          />
        </div>
      </div>

      {guidedError && (
        <div className="mx-5 mt-3 rounded-lg border border-crimson-500/30 bg-crimson-500/10 px-4 py-2 text-xs text-crimson-400">
          {guidedError}
        </div>
      )}

      <div className="flex-1 overflow-auto px-5 py-6">
        <div className="mx-auto max-w-lg">
          {guidedGenerating && (
            <div className="flex flex-col items-center justify-center py-12">
              <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-magic-500/20 mb-4">
                <ScrollText className="h-8 w-8 text-magic-400 animate-pulse" />
              </div>
              <p className="text-sm text-pine-600 font-medium">正在生成完整大纲...</p>
              <p className="mt-1 text-xs text-pine-700">
                架构师正在根据你的回答精心设计每一章
              </p>
            </div>
          )}

          {!guidedGenerating && guidedSaved && (
            <div className="flex flex-col items-center justify-center py-12">
              <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-magic-500/20 mb-4">
                <FileEdit className="h-8 w-8 text-magic-400" />
              </div>
              <p className="text-sm text-magic-300 font-medium">
                {guidedStaged ? "多章节规划任务已启动" : "大纲草稿已生成"}
              </p>
              {guidedStaged && guidedRequestedChapters > 0 && (
                <p className="mt-1 text-xs text-pine-700">
                  当前 {guidedGeneratedChapters}/{guidedRequestedChapters} 章；系统将在后台逐批生成并校验
                </p>
              )}
              {generateOutlineJson && (
                <p className="mt-1 text-xs text-pine-700">
                   共 {getOutlineChapterCount(generateOutlineJson)} 章，已保存到草稿区
                </p>
              )}
              {guidedRecoveryNote && (
                <p className="mt-2 max-w-sm rounded-lg border border-magic-500/20 bg-magic-500/10 px-3 py-2 text-center text-xs leading-relaxed text-magic-300">
                  {guidedRecoveryNote}
                </p>
              )}
              <p className="mt-3 max-w-sm text-center text-xs leading-relaxed text-pine-700">
                {guidedStaged
                  ? "生成期间原正式大纲保持不变；失败批次可以续跑，全部章节完成后系统才会一次性替换。"
                  : "草稿不会覆盖正式大纲。你可以在大纲总文件标题旁的「草稿」中查看全文，确认保存后再进入自由对话做精细化修改。"}
              </p>
            </div>
          )}

          {!guidedGenerating && !guidedSaved && generateOutlineText && (
            <>
              <div className="flex items-start gap-3 mb-4">
                <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-magic-500/20 mt-0.5">
                  <ScrollText className="h-3.5 w-3.5 text-magic-400" />
                </div>
                <div>
                   <span className="text-xs font-medium text-magic-400">
                     {guidedStaged ? "连续首批章节已生成" : "大纲已生成"}
                   </span>
                  {generateOutlineJson && (
                    <p className="mt-1 text-xs text-pine-700">
                      共 {getOutlineChapterCount(generateOutlineJson)} 章大纲
                    </p>
                  )}
                </div>
              </div>

              <div className="rounded-xl border border-pine-200/40 bg-white/30 p-4 text-sm text-pine-600">
                {generateOutlineJson
                   ? guidedStaged
                     ? "连续首批章节已经生成，但还没有写入草稿。请先点击「保存为草稿」，确认后再按连续编号追加后续章节。"
                     : "完整大纲已经生成，但还没有写入草稿。请先点击「保存为草稿」，再到大纲总文件旁的「草稿」入口查看完整内容。"
                  : "生成结果的结构还不完整，因此暂时无法保存为大纲草稿。请返回补充要求后重新生成。"}
              </div>
            </>
          )}


        </div>
      </div>

      {!guidedGenerating && guidedSaved && (
        <div className="outline-guided-footer border-t border-pine-200/40 px-5 py-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <button
                onClick={() => {
                  setMode("free");
                  if (!guidedStaged) handlePreviewDraft();
                }}
                className="flex items-center gap-1.5 rounded-lg border border-magic-500/30 bg-magic-500/10 px-4 py-2 text-xs text-magic-300 transition-colors hover:bg-magic-500/20"
              >
                <FileEdit className="h-3.5 w-3.5" />
                {guidedStaged ? "查看生成进度" : "查看草稿"}
              </button>
              {!guidedStaged && (
                <button
                  onClick={handleConfirmDraftAndStartChat}
                  disabled={draftPanel.confirming}
                  className="flex items-center gap-1.5 rounded-lg bg-magic-500 px-4 py-2 text-xs font-medium text-pine-950 transition-colors hover:bg-magic-400 disabled:opacity-50"
                >
                  {draftPanel.confirming ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
                  保存为正式大纲
                </button>
              )}
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => {
                  setGuidedSaved(false);
                  setGuidedPhase("review");
                  setGenerateOutlineText("");
                  setGenerateOutlineJson(null);
                }}
                className="flex items-center gap-1 rounded-lg border border-pine-200 bg-white/30 px-4 py-2 text-xs text-pine-600 transition-colors hover:bg-white/50 hover:text-pine-500"
              >
                <ArrowLeft className="h-3.5 w-3.5" />
                返回修改
              </button>
              <button
                onClick={() => {
                  setGuidedSaved(false);
                  setGuidedPhase("init");
                  setGuidedTopicIndex(0);
                  setGuidedAnswers({});
                  setGuidedGeneratedAnswers({});
                  setGuidedSupplement("");
                  setGuidedLLMContent("");
                  setGuidedQuestion("");
                  setGuidedOptions([]);
                  setGuidedCoreThemes([]);
                  setGuidedReviewContent("");
                  setGuidedFollowupQuestions([]);
                  setGuidedFollowupAnswers({});
                  setGuidedCurrentAnswer("");
                  setGuidedCustomInput("");
                  setGenerateOutlineText("");
                  setGenerateOutlineJson(null);
                  setGuidedRecoveryNote(null);
                }}
                className="rounded-lg border border-pine-200 bg-white/30 px-4 py-2 text-xs text-pine-700 transition-colors hover:bg-white/50 hover:text-pine-600"
              >
                重新开始
              </button>
            </div>
          </div>
        </div>
      )}

      {!guidedGenerating && !guidedSaved && (
        <div className="outline-guided-footer border-t border-pine-200/40 px-5 py-3">
          <div className="flex items-center justify-between">
            <button
              onClick={() => {
                setGuidedPhase("review");
                setGenerateOutlineText("");
                setGenerateOutlineJson(null);
              }}
              className="flex items-center gap-1 rounded-lg border border-pine-200 bg-white/30 px-4 py-2 text-xs text-pine-600 transition-colors hover:bg-white/50 hover:text-pine-500"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
              返回修改
            </button>
            {generateOutlineText && (
              <button
                onClick={handleSaveGeneratedDraft}
                disabled={guidedDraftSaving}
                className="flex items-center gap-1.5 rounded-lg bg-magic-500 px-4 py-2 text-xs font-medium text-pine-950 transition-colors hover:bg-magic-400 disabled:opacity-50"
              >
                {guidedDraftSaving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <FileEdit className="h-3.5 w-3.5" />}
                {generateOutlineJson ? "保存为草稿" : "尝试解析并保存草稿"}
              </button>
            )}
            <button
              onClick={() => {
                setGuidedPhase("init");
                setGuidedTopicIndex(0);
                  setGuidedAnswers({});
                  setGuidedGeneratedAnswers({});
                setGuidedSupplement("");
                setGuidedLLMContent("");
                setGuidedQuestion("");
                setGuidedOptions([]);
                setGuidedCoreThemes([]);
                setGuidedReviewContent("");
                setGuidedFollowupQuestions([]);
                setGuidedFollowupAnswers({});
                setGuidedCurrentAnswer("");
                setGuidedCustomInput("");
                setGenerateOutlineText("");
                setGenerateOutlineJson(null);
                setGuidedRecoveryNote(null);
              }}
              className="rounded-lg border border-pine-200 bg-white/30 px-4 py-2 text-xs text-pine-700 transition-colors hover:bg-white/50 hover:text-pine-600"
            >
              重新开始
            </button>
          </div>
        </div>
      )}
    </div>
  );

  // ==================== Render: Guided Mode Entry ====================

  const renderGuidedMode = () => {
    switch (guidedPhase) {
      case "init":
        return renderInitPhase();
      case "topics":
        return renderTopicQuestions();
      case "review":
        return renderReviewPhase();
      case "followup":
      case "generate":
        return renderGeneratePhase();
      default:
        return renderInitPhase();
    }
  };

  const renderExpansionProgress = () => {
    if (contextMode !== "master" || !expansionStatus?.has_job) return null;
    const target = Number(expansionStatus.target_chapters || 0);
    const completed = Number(expansionStatus.completed_chapters || 0);
    const progress = Math.max(0, Math.min(100, Number(
      expansionStatus.progress ?? (target > 0 ? (completed / target) * 100 : 0),
    )));
    const active = ["started", "already_running", "resumed", "queued", "running", "completing"].includes(expansionStatus.status);
    const resumable = ["failed", "paused", "cancelled"].includes(expansionStatus.status);
    const completedStatus = expansionStatus.status === "completed" || expansionStatus.status === "already_complete";
    const statusLabel = completedStatus
      ? "已完成并写入正式大纲"
      : active
        ? (expansionStatus.message || "正在按批生成")
        : resumable
          ? (expansionStatus.message || "任务已暂停")
          : (expansionStatus.message || expansionStatus.status);

    return (
      <section
        className={clsx(
          "mx-5 mb-3 rounded-lg border px-4 py-3",
          expansionStatus.status === "failed"
            ? "border-[var(--color-error)] bg-[var(--color-error-soft)]"
            : completedStatus
              ? "border-[var(--border-emphasis)] bg-[var(--color-accent-soft)]"
              : "border-[var(--color-highlight)] bg-[var(--color-highlight-soft)]",
        )}
        aria-label="多章节规划进度"
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 text-sm font-semibold text-[var(--color-ink-strong)]">
              {active ? (
                <Loader2 className="h-4 w-4 shrink-0 animate-spin motion-reduce:animate-none" />
              ) : completedStatus ? (
                <Check className="h-4 w-4 shrink-0" />
              ) : (
                <AlertTriangle className="h-4 w-4 shrink-0" />
              )}
              多章节规划：{completed}/{target} 章
            </div>
            <p className="mt-1 text-xs leading-5 text-[var(--color-ink-muted)]">{statusLabel}</p>
            {expansionStatus.error && (
              <p className="mt-1 text-xs leading-5 text-[var(--color-error)]">
                {expansionStatus.error}。已完成批次仍保存在草稿中，正式大纲没有被部分覆盖。
              </p>
            )}
            <div className="mt-2 h-2 overflow-hidden rounded-full bg-black/10" aria-hidden="true">
              <div
                className="h-full rounded-full bg-[var(--color-accent)] transition-[width] duration-300"
                style={{ width: `${progress}%` }}
              />
            </div>
            <p className="mt-1 text-[11px] text-[var(--color-ink-muted)]">
              {progress.toFixed(progress >= 10 ? 0 : 1)}% · 每批 {expansionStatus.batch_size || 25} 章 · 支持失败续跑
            </p>
          </div>
          <div className="flex shrink-0 gap-2">
            {resumable && (
              <button
                type="button"
                onClick={() => void handleResumeExpansion()}
                disabled={expansionAction !== null}
                className="inline-flex min-h-8 items-center gap-1 rounded-md bg-[var(--color-accent)] px-3 text-xs font-semibold text-[var(--color-on-accent)] disabled:opacity-50"
              >
                {expansionAction === "resume" && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                继续
              </button>
            )}
            {active && (
              <button
                type="button"
                onClick={() => void handleCancelExpansion()}
                disabled={expansionAction !== null}
                className="inline-flex min-h-8 items-center gap-1 rounded-md border border-[var(--border-emphasis)] px-3 text-xs font-medium text-[var(--color-ink)] disabled:opacity-50"
              >
                {expansionAction === "cancel" && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                取消
              </button>
            )}
          </div>
        </div>
      </section>
    );
  };

  // ==================== Render: Chat Area (shared) ====================

  const renderChatArea = () => (
    <>
      <div className="flex-1 overflow-auto px-5 py-4 space-y-4">
        {chat.loading ? (
          <ChatSkeleton />
        ) : (
        <>
          {chat.messages.map((msg, i) => {
            const prevMsg = i > 0 ? chat.messages[i - 1] : undefined;
            const showDateDivider = prevMsg && shouldShowDateDividerMsg(msg.created_at, prevMsg.created_at);
            const isSearchCurrent = chatSearch.isCurrentMatch(i);
            const displayContent = chatSearch.searchQuery
              ? highlightText(msg.content, chatSearch.searchQuery)
              : msg.content;
            return (
            <div
              key={i}
              data-msg-index={i}
              className={`transition-shadow duration-300 ${isSearchCurrent ? "ring-1 ring-magic-500/40 rounded-lg" : ""}`}
            >
              {showDateDivider && <DateDivider date={msg.created_at || new Date().toISOString()} />}
              <ChatMessageBubble
                role={msg.role as "user" | "assistant" | "system"}
                content={displayContent}
                copyText={msg.content}
                timestamp={msg.created_at}
              />
            </div>
            );
          })}
          {sending && !toolStatus && (
            <div className="flex justify-start">
              <div className="flex items-center gap-2 rounded-xl bg-white/50 px-4 py-3 text-sm text-pine-700">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                架构师正在思考...
              </div>
            </div>
          )}
          {toolStatus && (
            <div className="flex justify-start">
              <div className="flex items-center gap-2 rounded-xl bg-magic-500/10 border border-magic-500/20 px-4 py-3 text-sm text-magic-300">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                {toolStatus}
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </>
        )}
      </div>

      {parseSuccess && saveOutcome && (
        <div className="mx-5 mb-2 rounded-lg border border-[var(--border-emphasis)] bg-[var(--color-accent-soft)] px-4 py-2 text-xs text-[var(--color-ink)]" role="status">
          {saveOutcome === "draft"
            ? "草案已保存，正式大纲没有变化。请预览差异并确认后再使其生效。"
            : <>正式大纲已保存。{changeRecordCount !== null && changeRecordCount > 0 && `本次保存产生了 ${changeRecordCount} 处变更记录。`}</>}
        </div>
      )}
      {parseError && (
        <div className="mx-5 mb-2 rounded-lg border border-[var(--color-error)] bg-[var(--color-error-soft)] px-4 py-2 text-xs text-[var(--color-error)]" role="alert">
          解析失败：{parseError}。原内容仍保留，正式大纲没有变化；你可以调整输出后重新保存为草案。
        </div>
      )}
      {outlineActionError && (
        <div className="mx-5 mb-2 rounded-lg border border-[var(--color-error)] bg-[var(--color-error-soft)] px-4 py-2 text-xs text-[var(--color-error)]" role="alert">
          操作未完成：{outlineActionError}
        </div>
      )}

      {renderExpansionProgress()}

      {contextMode === "master" && outlineStatus?.has_outline && outlineStatus.chapter_continuity?.valid === false && (
        <section
          id="outline-continuity-warning"
          className="mx-5 mb-3 rounded-md border border-[var(--color-highlight)] bg-[var(--color-highlight-soft)] px-4 py-3 text-[var(--color-ink)]"
          aria-labelledby="outline-continuity-title"
        >
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <div className="flex items-start gap-2">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-highlight)]" />
                <div>
                  <p id="outline-continuity-title" className="text-sm font-semibold text-[var(--color-ink-strong)]">
                    当前正式大纲只有关键锚点，章节并不连续
                  </p>
                  <p className="mt-1 text-xs leading-5 text-[var(--color-ink-muted)]">
                    现有 {outlineStatus.chapter_continuity.chapter_count} 个章节节点，跨度至第 {outlineStatus.chapter_continuity.max_chapter} 章；缺少
                    {" "}{outlineStatus.chapter_continuity.missing.slice(0, 8).join("、")}
                    {outlineStatus.chapter_continuity.missing.length > 8 ? ` 等 ${outlineStatus.chapter_continuity.missing.length} 章` : " 章"}。
                    系统会保留现有节点，只把缺章补成“待细化”草案，未经你确认不会改动正式大纲。
                  </p>
                </div>
              </div>
            </div>
            <button
              type="button"
              onClick={outlineStatus.has_draft ? handlePreviewDraft : handleRecoverContinuity}
              disabled={recoveringContinuity}
              className="inline-flex min-h-9 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md bg-[var(--color-accent)] px-3 text-xs font-semibold text-[var(--color-on-accent)] transition-colors duration-150 hover:bg-[var(--color-accent-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)] active:bg-[var(--color-accent-hover)] disabled:cursor-not-allowed disabled:opacity-50"
            >
              {recoveringContinuity ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <GitCompare className="h-3.5 w-3.5" />}
              {outlineStatus.has_draft ? "查看待确认草案" : recoveringContinuity ? "正在生成草案" : "生成连续草案"}
            </button>
          </div>
        </section>
      )}

      <div className="border-t border-pine-200/40 px-5 py-3">
        <div className="flex gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="输入你的想法..."
            disabled={sending || confirming}
            rows={1}
            className="flex-1 rounded-lg border border-pine-200 bg-white/50 px-3 py-2 text-sm text-pine-600 outline-none transition-colors focus:border-magic-500/50 disabled:opacity-50 resize-none max-h-24"
            style={{ minHeight: "36px" }}
          />
          {(sending || confirming) && (
            <button
              type="button"
              onClick={handleStopGeneration}
              className="flex items-center gap-1.5 rounded-lg border border-crimson-400/50 bg-crimson-500/10 px-4 py-2 text-sm font-medium text-crimson-500 transition-colors hover:bg-crimson-500/20"
              aria-label="停止生成"
            >
              <Square className="h-3.5 w-3.5 fill-current" />
              停止生成
            </button>
          )}
          <button
            onClick={handleSend}
            disabled={sending || confirming || !input.trim()}
            className="flex items-center gap-1.5 rounded-lg bg-magic-500 px-4 py-2 text-sm font-medium text-pine-950 transition-colors hover:bg-magic-400 disabled:opacity-50"
          >
            {sending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Send className="h-4 w-4" />
            )}
            发送
          </button>
        </div>
        <div className="mt-2 flex gap-2">
          {contextMode === "master" && (
          <>
          {!hasJsonInResponse && (
            <button
              onClick={handleConfirmOutline}
              disabled={sending || confirming || messages.length < 2}
              className="flex items-center gap-1.5 rounded-lg border border-pine-200 bg-white/30 px-3 py-1.5 text-xs text-pine-600 transition-colors hover:bg-white/50 hover:text-pine-500 disabled:opacity-50"
            >
              {confirming ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Check className="h-3.5 w-3.5" />
              )}
              确认并生成大纲
            </button>
          )}
          {hasJsonInResponse && (
            <>
              <button
                onClick={handleParseAndSave}
                disabled={parsing}
                className="flex items-center gap-1.5 rounded-lg bg-jade-500/20 px-3 py-1.5 text-xs text-jade-400 transition-colors hover:bg-jade-500/30 disabled:opacity-50"
              >
                {parsing ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Check className="h-3.5 w-3.5" />
                )}
                直接保存
              </button>
              <button
                onClick={handleSaveAsDraft}
                disabled={parsing}
                className="flex items-center gap-1.5 rounded-lg border border-pine-200 bg-white/30 px-3 py-1.5 text-xs text-pine-600 transition-colors hover:bg-white/50 hover:text-pine-500 disabled:opacity-50"
              >
                {parsing ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <FileEdit className="h-3.5 w-3.5" />
                )}
                保存为草案
              </button>
            </>
          )}
          {outlineStatus?.has_draft && (
            <button
              onClick={handlePreviewDraft}
              className="flex items-center gap-1.5 rounded-lg border border-pine-200 bg-white/30 px-3 py-1.5 text-xs text-pine-600 transition-colors hover:bg-white/50 hover:text-pine-500"
            >
              <GitCompare className="h-3.5 w-3.5" />
              预览草案
            </button>
          )}
          {outlineStatus?.frozen && (
            <>
              <span className="flex items-center gap-1 rounded-lg bg-crimson-500/15 px-3 py-1.5 text-xs text-crimson-400 border border-crimson-500/30">
                <Lock className="h-3.5 w-3.5" />
                已冻结 v{outlineStatus.version}
              </span>
              <button
                onClick={() => setAmendmentPanel((prev) => ({ ...prev, open: true }))}
                className="flex items-center gap-1.5 rounded-lg border border-magic-500/30 bg-magic-500/10 px-3 py-1.5 text-xs text-magic-400 transition-colors hover:bg-magic-500/20"
              >
                <FileEdit className="h-3.5 w-3.5" />
                提出修订案
              </button>
              <button
                onClick={handleUnfreeze}
                disabled={freezing}
                className="flex items-center gap-1.5 rounded-lg border border-pine-200 bg-white/30 px-3 py-1.5 text-xs text-pine-600 transition-colors hover:bg-white/50 hover:text-pine-500 disabled:opacity-50"
              >
                <Unlock className="h-3.5 w-3.5" />
                解冻
              </button>
            </>
          )}
          {!outlineStatus?.frozen && outlineStatus?.has_outline && (
            <button
              onClick={handleFreeze}
              disabled={freezing || outlineStatus.chapter_continuity?.valid === false}
              aria-describedby={outlineStatus.chapter_continuity?.valid === false ? "outline-continuity-warning" : undefined}
              title={outlineStatus.chapter_continuity?.valid === false ? "请先补齐并确认连续章节草案" : undefined}
              className="flex items-center gap-1.5 rounded-lg border border-magic-500/30 bg-magic-500/10 px-3 py-1.5 text-xs text-magic-400 transition-colors hover:bg-magic-500/20 disabled:opacity-50"
            >
              <Lock className="h-3.5 w-3.5" />
              冻结大纲
            </button>
          )}
          {outlineStatus?.has_outline && (
            <button
              onClick={handleRunAudit}
              className="flex items-center gap-1.5 rounded-lg border border-pine-200 bg-white/30 px-3 py-1.5 text-xs text-pine-600 transition-colors hover:bg-white/50 hover:text-pine-500"
            >
              <ShieldCheck className="h-3.5 w-3.5" />
              审查大纲
            </button>
          )}
          </>
          )}
        </div>
      </div>
    </>
  );

  const renderDraftPanel = () => (
    <div className="border-t border-pine-200/40 bg-white/50 px-5 py-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <GitCompare className="h-4 w-4 text-magic-400" />
          <span className="text-sm font-medium text-magic-400">草案预览</span>
        </div>
        <button
          onClick={() => setDraftPanel((prev) => ({ ...prev, open: false }))}
          className="rounded-lg p-1 text-pine-700 hover:bg-white hover:text-pine-600"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {draftPanel.loading && (
        <div className="flex items-center gap-2 text-sm text-pine-700">
          <Loader2 className="h-4 w-4 animate-spin" />
          正在加载草案差异...
        </div>
      )}

      {draftPanel.error && (
        <div className="flex items-start gap-2 rounded-md border border-[var(--color-error)] bg-[var(--color-error-soft)] px-3 py-2 text-xs leading-5 text-[var(--color-error)]" role="alert">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>{draftPanel.error}。正式大纲没有变化。</span>
        </div>
      )}

      {draftPanel.diff && (
        <div className="space-y-3">
          <div className="flex items-center gap-3 text-xs">
            <span className="rounded-full bg-magic-500/20 px-2.5 py-1 text-magic-400">
              {draftPanel.diff.total_changes} 处变更
            </span>
            {draftPanel.diff.change_types.map((t) => (
              <span key={t} className="rounded-full bg-white px-2.5 py-1 text-pine-700">
                {t === "chapter_add" ? "新增章节" :
                 t === "chapter_delete" ? "删除章节" :
                 t === "conflict_change" ? "冲突变更" :
                 t === "chapter_modify" ? "章节修改" :
                 t === "structure_change" ? "结构变更" : t}
              </span>
            ))}
          </div>

          <div className="max-h-40 overflow-auto space-y-2">
            {draftPanel.diff.changes.slice(0, 10).map((change, i) => (
              <div key={i} className="rounded-lg border border-pine-200/40 bg-white/30 px-3 py-2 text-xs">
                <div className="flex items-center gap-2 mb-1">
                  <span className={clsx(
                    "rounded px-1.5 py-0.5",
                    change.change_type === "chapter_add" ? "bg-jade-500/20 text-jade-400" :
                    change.change_type === "chapter_delete" ? "bg-crimson-500/20 text-crimson-400" :
                    "bg-magic-500/20 text-magic-400"
                  )}>
                    {change.change_type === "chapter_add" ? "新增" :
                     change.change_type === "chapter_delete" ? "删除" :
                     change.change_type === "conflict_change" ? "冲突" :
                     change.change_type === "chapter_modify" ? "修改" :
                     change.change_type === "structure_change" ? "结构" : change.change_type}
                  </span>
                  <span className="text-pine-600">{change.reason}</span>
                </div>
                {(() => {
                  const bc = change.impact?.broken_consistency;
                  if (!Array.isArray(bc) || bc.length === 0) return null;
                  return (
                    <div className="flex items-start gap-1 text-crimson-400">
                      <AlertTriangle className="h-3 w-3 mt-0.5 shrink-0" />
                      <span>{(bc as string[]).join("；")}</span>
                    </div>
                  );
                })()}
              </div>
            ))}
            {draftPanel.diff.total_changes > draftPanel.diff.changes.length && (
              <p className="text-xs text-pine-700">
                ...还有 {draftPanel.diff.total_changes - draftPanel.diff.changes.length} 处变更
              </p>
            )}
          </div>

          <div className="flex items-center gap-2 pt-1">
            <button
              onClick={handleConfirmDraft}
              disabled={draftPanel.confirming}
              className="flex items-center gap-1.5 rounded-lg bg-jade-500/20 px-4 py-2 text-xs text-jade-400 transition-colors hover:bg-jade-500/30 disabled:opacity-50"
            >
              {draftPanel.confirming ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
              确认草案
            </button>
            <button
              onClick={handleDiscardDraft}
              disabled={draftPanel.discarding}
              className="flex items-center gap-1.5 rounded-lg border border-crimson-500/30 bg-crimson-500/10 px-4 py-2 text-xs text-crimson-400 transition-colors hover:bg-crimson-500/20 disabled:opacity-50"
            >
              {draftPanel.discarding ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
              丢弃草案
            </button>
          </div>
        </div>
      )}

      {!draftPanel.loading && !draftPanel.diff && !draftPanel.error && (
        <p className="text-sm text-pine-700">当前没有待确认的草案。</p>
      )}
    </div>
  );

  const renderAmendmentPanel = () => (
    <div className="border-t border-pine-200/40 bg-white/50 px-5 py-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <FileEdit className="h-4 w-4 text-magic-400" />
          <span className="text-sm font-medium text-magic-400">宪法修订案</span>
        </div>
        <button
          onClick={() => setAmendmentPanel({
            open: false, targetChapters: "", modification: "", reason: "",
            submitting: false, impactReport: null, amendmentId: null, applying: false,
          })}
          className="rounded-lg p-1 text-pine-700 hover:bg-white hover:text-pine-600"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {!amendmentPanel.amendmentId ? (
        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-xs text-pine-700">目标章节号（逗号分隔，如 3,5,8）</label>
            <input
              type="text"
              value={amendmentPanel.targetChapters}
              onChange={(e) => setAmendmentPanel((prev) => ({ ...prev, targetChapters: e.target.value }))}
              placeholder="3, 5, 8"
              className="w-full rounded-lg border border-pine-200 bg-white/30 px-3 py-2 text-sm text-pine-600 outline-none focus:border-magic-500/50 placeholder:text-pine-800"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-pine-700">修改内容</label>
            <textarea
              value={amendmentPanel.modification}
              onChange={(e) => setAmendmentPanel((prev) => ({ ...prev, modification: e.target.value }))}
              placeholder="描述你想要做的修改..."
              rows={3}
              className="w-full rounded-lg border border-pine-200 bg-white/30 px-3 py-2 text-sm text-pine-600 outline-none focus:border-magic-500/50 placeholder:text-pine-800 resize-none"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-pine-700">修改原因（可选）</label>
            <input
              type="text"
              value={amendmentPanel.reason}
              onChange={(e) => setAmendmentPanel((prev) => ({ ...prev, reason: e.target.value }))}
              placeholder="为什么要做这个修改..."
              className="w-full rounded-lg border border-pine-200 bg-white/30 px-3 py-2 text-sm text-pine-600 outline-none focus:border-magic-500/50 placeholder:text-pine-800"
            />
          </div>
          <button
            onClick={handleSubmitAmendment}
            disabled={amendmentPanel.submitting || !amendmentPanel.modification.trim()}
            className="flex items-center gap-1.5 rounded-lg bg-magic-500 px-4 py-2 text-xs font-medium text-pine-950 transition-colors hover:bg-magic-400 disabled:opacity-50"
          >
            {amendmentPanel.submitting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <AlertTriangle className="h-3.5 w-3.5" />}
            提交修订案
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          <OutlineImpactSummary report={amendmentPanel.impactReport} />
          <div className="flex items-center gap-2">
            <button
              onClick={handleApplyAmendment}
              disabled={amendmentPanel.applying}
              className="flex items-center gap-1.5 rounded-lg bg-magic-500 px-4 py-2 text-xs font-medium text-pine-950 transition-colors hover:bg-magic-400 disabled:opacity-50"
            >
              {amendmentPanel.applying ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
              确认应用修订
            </button>
            <button
              onClick={() => setAmendmentPanel({
                open: false, targetChapters: "", modification: "", reason: "",
                submitting: false, impactReport: null, amendmentId: null, applying: false,
              })}
              className="rounded-lg border border-pine-200 bg-white/30 px-4 py-2 text-xs text-pine-600 hover:bg-white/50"
            >
              取消
            </button>
          </div>
        </div>
      )}
    </div>
  );

  const renderAuditPanel = () => {
    if (!auditPanel.open) return null;

    const result = auditPanel.result as any;
    const reports = result?.reports || {};
    const overallVerdict = result?.overall_verdict as "pass" | "warning" | "blocking" | undefined;
    const OverallIcon = overallVerdict === "pass" ? Check : AlertTriangle;

    return (
      <aside
        className="fixed right-0 top-0 z-50 h-full w-[min(28rem,100vw)] overflow-y-auto border-l border-[var(--border-emphasis)] bg-[var(--surface-raised)] text-[var(--color-ink)] shadow-[var(--shadow-overlay)]"
        aria-label="大纲审查报告"
      >
        <div className="p-4">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-lg font-semibold text-[var(--color-ink-strong)]">审查报告</h3>
              <p className="mt-0.5 text-xs text-[var(--color-ink-muted)]">先处理阻断项，再决定是否冻结正式大纲</p>
            </div>
            <button
              type="button"
              aria-label="关闭审查报告"
              onClick={() => setAuditPanel({ open: false, loading: false, result: null, error: null })}
              className="rounded-md p-1.5 text-[var(--color-ink-muted)] transition-colors hover:bg-[var(--surface-tool)] hover:text-[var(--color-ink)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)] active:bg-[var(--color-accent-soft)]"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          {auditPanel.loading ? (
            <div className="flex items-center justify-center py-8 text-[var(--color-ink-muted)]" role="status">
              <Loader2 className="w-5 h-5 animate-spin text-[var(--color-accent)]" />
              <span className="ml-2 text-sm">正在审查...</span>
            </div>
          ) : auditPanel.error ? (
            <div className="flex items-start gap-2 rounded-md border border-[var(--color-error)] bg-[var(--color-error-soft)] px-3 py-3 text-sm leading-6 text-[var(--color-error)]" role="alert">
              <AlertTriangle className="mt-1 h-4 w-4 shrink-0" />
              <div>
                <p className="font-medium">审查没有完成</p>
                <p className="mt-1 text-xs">{auditPanel.error}。现有大纲没有变化，可以关闭后重试。</p>
              </div>
            </div>
          ) : (
            <>
              <div className={clsx("mb-4 flex items-center gap-2 rounded-md border px-3 py-2 text-sm font-medium", {
                "border-[var(--color-accent)] bg-[var(--color-accent-soft)] text-[var(--color-ink)]": overallVerdict === "pass",
                "border-[var(--color-highlight)] bg-[var(--color-highlight-soft)] text-[var(--color-ink)]": overallVerdict === "warning",
                "border-[var(--color-error)] bg-[var(--color-error-soft)] text-[var(--color-error)]": overallVerdict === "blocking",
              })}>
                <OverallIcon className="h-4 w-4 shrink-0" />
                {overallVerdict === "pass" ? "全部通过" : overallVerdict === "warning" ? "存在警告" : "存在阻断问题"}
              </div>

              {Object.entries(reports).map(([key, report]: [string, any]) => (
                <section key={key} className="mb-4 rounded-md border border-[var(--border-subtle)] bg-[var(--surface-paper)] p-3">
                  <div className="flex items-center justify-between mb-2">
                    <span className="font-medium text-sm text-[var(--color-ink-strong)]">{key === "rule_validation" ? "规则校验" : key === "thread_graph" ? "线索图" : "依赖图"}</span>
                    <span className={clsx("rounded-full px-2 py-0.5 text-xs", {
                      "bg-[var(--color-accent-soft)] text-[var(--color-accent)]": report.verdict === "pass",
                      "bg-[var(--color-highlight-soft)] text-[var(--color-ink)]": report.verdict === "warning",
                      "bg-[var(--color-error-soft)] text-[var(--color-error)]": report.verdict === "blocking",
                    })}>{report.verdict}</span>
                  </div>
                  <p className="mb-2 text-xs leading-5 text-[var(--color-ink-muted)]">{report.summary}</p>
                  {report.findings?.length > 0 && (
                    <div className="space-y-2">
                      {report.findings.map((f: any, i: number) => (
                        <div key={i} className={clsx("rounded-md border px-2 py-1.5 text-xs leading-5", {
                          "border-[var(--color-error)] bg-[var(--color-error-soft)] text-[var(--color-error)]": f.severity === "error",
                          "border-[var(--color-highlight)] bg-[var(--color-highlight-soft)] text-[var(--color-ink)]": f.severity === "warning",
                          "border-[var(--color-lavender)] bg-[var(--color-lavender-soft)] text-[var(--color-ink)]": f.severity === "info",
                        })}>
                          <span className="font-medium">[{f.scope}]</span> {f.message}
                          {f.suggestion && <span className="ml-1 opacity-75">→ {f.suggestion}</span>}
                        </div>
                      ))}
                    </div>
                  )}
                </section>
              ))}
            </>
          )}
        </div>
      </aside>
    );
  };

  const renderFreeChat = () => (
    <div className="flex flex-1 min-h-0">
      {sidebarOpen && (
        <ChatSidebar
          projectId={projectId}
          agentType="outline_architect"
          sessions={chat.sessions}
          currentSessionId={chat.currentSessionId}
          currentMessages={chat.messages}
          onSelectSession={chat.switchSession}
          onNewSession={() => { chat.createNewSession(); setMode("free"); }}
          onRename={chat.renameSession}
          onDelete={chat.deleteSession}
          onPin={chat.pinSession}
          onUnpin={chat.unpinSession}
          onSearch={chat.searchSessions}
        />
      )}
      <div className="flex h-full flex-1 flex-col min-h-0">
        {renderChatArea()}
        {draftPanel.open && renderDraftPanel()}
        {amendmentPanel.open && renderAmendmentPanel()}
      </div>
    </div>
  );

  // ==================== Main Render ====================

  return (
    <ChatErrorBoundary agentName="大纲架构师">
    <div className="outline-assistant flex min-h-0 flex-1 flex-col overflow-hidden">
      <WorkspacePaneHeader tone="assistant" className="justify-between gap-3 px-4">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-pine-200/80 bg-pine-100/80">
            <ScrollText className="h-4 w-4 text-pine-700" />
          </div>
          <div className="min-w-0">
            <h2 className="text-[13px] font-semibold text-pine-900">大纲架构师</h2>
            <p className="truncate text-[11px] text-pine-600">
              {contextMode === "chapter"
                ? `第 ${selectedChapterNumber} 章工作台 · 《${projectName}》`
                : mode === "guided"
                ? `引导构建 · 《${projectName}》`
                : mode === "free"
                ? `自由对话 · 《${projectName}》`
                : `设计《${projectName}》的大纲`}
            </p>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-0.5">
          {contextMode === "master" && mode !== "choose" && (
            <button
              onClick={() => {
                setMode("choose");
                chat.setMessages([]);
                setGuidedPhase("init");
                setGuidedTopicIndex(0);
                setGuidedAnswers({});
                setGuidedGeneratedAnswers({});
                setGuidedRecoveryNote(null);
                setGuidedSupplement("");
                setGuidedCurrentAnswer("");
                setGuidedCustomInput("");
                setGuidedFollowupAnswers({});
              }}
              className="mr-1 flex h-8 items-center gap-1.5 rounded-md px-2 text-xs font-medium text-pine-700 transition-colors hover:bg-pine-100/70 hover:text-pine-900"
              title="切换对话模式"
            >
              <SlidersHorizontal className="h-3.5 w-3.5" />
              <span className="hidden min-[1500px]:inline">
                {mode === "guided" ? "引导构建" : "自由对话"}
              </span>
            </button>
          )}
          {mode === "free" && (
            <>
              <button
                onClick={() => setSearchOpen(!searchOpen)}
                className="rounded-md p-1.5 text-pine-600 transition-colors hover:bg-pine-100/70 hover:text-pine-900"
                title="搜索"
              >
                <Search className="h-4 w-4" />
              </button>
              <button
                onClick={() => setSidebarOpen(!sidebarOpen)}
                className="rounded-md p-1.5 text-pine-600 transition-colors hover:bg-pine-100/70 hover:text-pine-900"
                title="会话列表"
              >
                {sidebarOpen ? (
                  <PanelLeftClose className="h-4 w-4" />
                ) : (
                  <PanelLeft className="h-4 w-4" />
                )}
              </button>
              <div className="mx-1.5 h-4 w-px bg-pine-200/80" />
              <button
                onClick={() => { chat.createNewSession(); }}
                className="flex h-8 items-center gap-1 rounded-md bg-pine-700 px-2.5 text-xs font-medium text-white transition-colors hover:bg-pine-800"
              >
                <Plus className="h-3.5 w-3.5" />
                新对话
              </button>
            </>
          )}
          <button
            onClick={onClose}
            className="ml-1 rounded-md p-1.5 text-pine-600 transition-colors hover:bg-pine-100/70 hover:text-pine-900"
            title="关闭"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </WorkspacePaneHeader>

      {searchOpen && mode === "free" && (
        <div className="flex justify-end px-5 py-1.5 border-b border-pine-200/20">
          <ChatSearchBar
            onSearch={chatSearch.setSearchQuery}
            onClose={() => { setSearchOpen(false); chatSearch.setSearchQuery(""); }}
            matchCount={chatSearch.matchCount}
            currentMatch={chatSearch.currentMatch}
            onPrev={chatSearch.goToPrev}
            onNext={chatSearch.goToNext}
          />
        </div>
      )}

      {mode === "choose" && renderModeChoose()}
      {mode === "guided" && renderGuidedMode()}
      {mode === "free" && renderFreeChat()}
      {renderAuditPanel()}
    </div>
    </ChatErrorBoundary>
  );
}
