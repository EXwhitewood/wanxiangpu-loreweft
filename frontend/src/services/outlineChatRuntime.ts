import * as api from "@/api/client";
import type { OutlineChatMessage } from "@/types";

export type OutlineChatRunStatus = "running" | "completed" | "failed" | "cancelled";

export interface OutlineChatRunSnapshot {
  key: string;
  status: OutlineChatRunStatus;
  messages: OutlineChatMessage[];
  accumulated: string;
  toolStatus: string | null;
  error: string | null;
  startedAt: string;
}

interface OutlineChatRun extends OutlineChatRunSnapshot {
  controller: AbortController;
  listeners: Set<(snapshot: OutlineChatRunSnapshot) => void>;
  persistMessages: (messages: OutlineChatMessage[]) => void;
  onOutlineSaved?: () => void;
  lastToolResult: { tool: string; summary: string; changed?: boolean } | null;
  finishedAt: number | null;
}

const runs = new Map<string, OutlineChatRun>();
const pendingListeners = new Map<string, Set<(snapshot: OutlineChatRunSnapshot) => void>>();
const pendingOptions = new Map<string, {
  persistMessages?: (messages: OutlineChatMessage[]) => void;
  onOutlineSaved?: () => void;
}>();
const RUN_RETENTION_MS = 5 * 60 * 1000;
const MAX_RETAINED_RUNS = 24;

function snapshot(run: OutlineChatRun): OutlineChatRunSnapshot {
  return {
    key: run.key,
    status: run.status,
    messages: [...run.messages],
    accumulated: run.accumulated,
    toolStatus: run.toolStatus,
    error: run.error,
    startedAt: run.startedAt,
  };
}

function publish(run: OutlineChatRun): void {
  const value = snapshot(run);
  // A stale subscriber must not prevent the runtime from notifying the
  // remaining subscribers or reaching a terminal state.
  run.listeners.forEach((listener) => {
    try {
      listener(value);
    } catch {
      // React subscribers can disappear between an event and cleanup.
    }
  });
}

function takePendingSubscriptions(key: string): {
  listeners: Set<(snapshot: OutlineChatRunSnapshot) => void>;
  options: {
    persistMessages?: (messages: OutlineChatMessage[]) => void;
    onOutlineSaved?: () => void;
  };
} {
  const listeners = pendingListeners.get(key) || new Set();
  const options = pendingOptions.get(key) || {};
  pendingListeners.delete(key);
  pendingOptions.delete(key);
  return { listeners, options };
}

function appendAssistant(run: OutlineChatRun, content: string): void {
  if (!content.trim()) return;
  run.messages = [
    ...run.messages,
    { role: "assistant", content },
  ];
}

function toolFallback(result: OutlineChatRun["lastToolResult"]): string {
  if (!result) return "";
  // The server summary is the mutation receipt.  A tool name alone is never
  // proof that its write succeeded (validation, timeout or upstream errors may
  // have occurred before commit).
  if (result.summary?.trim()) return result.summary;
  return result.changed ? "操作已完成并保存。" : "操作未产生已确认的保存结果。";
}

function finish(run: OutlineChatRun, status: OutlineChatRunStatus, error?: string): void {
  // Terminal callbacks can be delivered more than once when an SSE stream
  // reports an error and then closes. The first terminal state is authoritative.
  if (run.status !== "running") return;
  run.status = status;
  run.toolStatus = null;
  run.error = error || null;
  if (status === "failed" && error) {
    run.messages = [
      ...run.messages,
      {
        role: "assistant",
        content: `本次生成失败：${error}\n\n你可以修正要求后重试，已生成的内容不会自动覆盖。`,
      },
    ];
  }
  publish(run);
  try {
    run.persistMessages(run.messages);
  } catch {
    // Persistence is best-effort here; the terminal state must still be
    // visible and the transport must never remain falsely marked running.
  }
  run.finishedAt = Date.now();

  setTimeout(() => {
    if (runs.get(run.key) === run && run.listeners.size === 0) runs.delete(run.key);
  }, RUN_RETENTION_MS);

  const completedRuns = [...runs.values()]
    .filter((item) => item.status !== "running")
    .sort((a, b) => (a.finishedAt || 0) - (b.finishedAt || 0));
  while (completedRuns.length > MAX_RETAINED_RUNS) {
    const stale = completedRuns.shift();
    if (stale && runs.get(stale.key) === stale) runs.delete(stale.key);
  }
}

export function outlineChatRunKey(
  projectId: string,
  contextMode: "master" | "chapter",
  selectedChapterNumber?: number,
): string {
  return `${projectId}:${contextMode}:${selectedChapterNumber ?? "master"}`;
}

export function getOutlineChatRun(key: string): OutlineChatRunSnapshot | null {
  const run = runs.get(key);
  return run ? snapshot(run) : null;
}

export function subscribeOutlineChatRun(
  key: string,
  listener: (snapshot: OutlineChatRunSnapshot) => void,
  options?: {
    persistMessages?: (messages: OutlineChatMessage[]) => void;
    onOutlineSaved?: () => void;
  },
): () => void {
  const run = runs.get(key);
  if (run) {
    if (options && "persistMessages" in options && options.persistMessages) {
      run.persistMessages = options.persistMessages;
    }
    if (options && "onOutlineSaved" in options) {
      run.onOutlineSaved = options.onOutlineSaved;
    }
    run.listeners.add(listener);
    listener(snapshot(run));
  } else {
    const listeners = pendingListeners.get(key) || new Set();
    listeners.add(listener);
    pendingListeners.set(key, listeners);
    pendingOptions.set(key, options || {});
  }
  return () => {
    const current = runs.get(key);
    if (current) {
      current.listeners.delete(listener);
      return;
    }
    const listeners = pendingListeners.get(key);
    if (!listeners) return;
    listeners.delete(listener);
    if (listeners.size === 0) {
      pendingListeners.delete(key);
      pendingOptions.delete(key);
    }
  };
}

export function stopOutlineChatRun(key: string): boolean {
  const run = runs.get(key);
  if (!run || run.status !== "running") return false;
  run.controller.abort();
  const partial = run.accumulated.trim();
  if (partial) {
    const last = run.messages[run.messages.length - 1];
    if (last?.role === "assistant") {
      run.messages = [
        ...run.messages.slice(0, -1),
        { ...last, content: `${partial}\n\n（已停止生成）` },
      ];
    }
  } else {
    appendAssistant(run, "已停止生成。你可以继续补充创作要求。\n");
  }
  finish(run, "cancelled");
  return true;
}

export function startOutlineChatRun(options: {
  key: string;
  projectId: string;
  messages: OutlineChatMessage[];
  contextMode: "master" | "chapter";
  selectedChapterNumber?: number;
  persistMessages: (messages: OutlineChatMessage[]) => void;
  onOutlineSaved?: () => void;
}): OutlineChatRunSnapshot {
  const existing = runs.get(options.key);
  if (existing && existing.status === "running") {
    // A component may have been remounted while the request was still live.
    // Always attach the newest persistence and save callbacks to that run.
    existing.persistMessages = options.persistMessages;
    existing.onOutlineSaved = options.onOutlineSaved;
    return snapshot(existing);
  }

  const pending = takePendingSubscriptions(options.key);
  if (existing) {
    // Keep a mounted editor subscribed when a new turn starts after the
    // previous turn reached a terminal state.
    pending.listeners = existing.listeners;
    pending.options.persistMessages ||= existing.persistMessages;
    pending.options.onOutlineSaved ||= existing.onOutlineSaved;
  }
  const run: OutlineChatRun = {
    key: options.key,
    status: "running",
    messages: [...options.messages],
    accumulated: "",
    toolStatus: null,
    error: null,
    startedAt: new Date().toISOString(),
    controller: new AbortController(),
    listeners: pending.listeners,
    persistMessages: options.persistMessages || pending.options.persistMessages || (() => {}),
    onOutlineSaved: options.onOutlineSaved ?? pending.options.onOutlineSaved,
    lastToolResult: null,
    finishedAt: null,
  };
  runs.set(options.key, run);
  publish(run);

  void api.outlineChatStream(
    options.projectId,
    options.messages,
    options.contextMode,
    options.selectedChapterNumber,
    (data) => {
      run.toolStatus = data.display;
      publish(run);
    },
    (data) => {
      run.toolStatus = null;
      run.lastToolResult = data;
      if (data.changed) {
        try {
          run.onOutlineSaved?.();
        } catch {
          // A refresh callback belongs to the current view and must not
          // change the outcome of the active chat run.
        }
      }
      publish(run);
    },
    (text) => {
      run.accumulated += text;
      const last = run.messages[run.messages.length - 1];
      if (last?.role === "assistant") {
        run.messages = [...run.messages.slice(0, -1), { ...last, content: run.accumulated }];
      } else {
        run.messages = [...run.messages, { role: "assistant", content: run.accumulated }];
      }
      publish(run);
    },
    () => {
      const finalContent = run.accumulated.trim() || toolFallback(run.lastToolResult);
      if (finalContent) {
        const last = run.messages[run.messages.length - 1];
        if (last?.role === "assistant") {
          run.messages = [...run.messages.slice(0, -1), { ...last, content: finalContent }];
        } else {
          appendAssistant(run, finalContent);
        }
      }
      finish(run, "completed");
    },
    (error) => finish(run, "failed", error),
    () => {
      // stopOutlineChatRun() finalizes immediately; this callback is kept as
      // the transport-level fallback for an abort that happens elsewhere.
      if (run.status !== "running") return;
      const partial = run.accumulated.trim();
      if (partial) {
        const last = run.messages[run.messages.length - 1];
        if (last?.role === "assistant") {
          run.messages = [
            ...run.messages.slice(0, -1),
            { ...last, content: `${partial}\n\n（已停止生成）` },
          ];
        }
      } else {
        appendAssistant(run, "已停止生成。你可以继续补充创作要求。\n");
      }
      finish(run, "cancelled");
    },
    run.controller.signal,
  );

  return snapshot(run);
}
