import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  outlineChatStream: vi.fn(),
}));

vi.mock("@/api/client", () => ({
  outlineChatStream: mocks.outlineChatStream,
}));

describe("outlineChatRuntime mutation receipts", () => {
  beforeEach(() => {
    mocks.outlineChatStream.mockReset();
  });

  it("does not turn a failed tool call into a saved message", async () => {
    mocks.outlineChatStream.mockImplementation(async (...args: unknown[]) => {
      const onToolResult = args[5] as (data: { tool: string; summary: string; changed: boolean }) => void;
      const onDone = args[7] as () => void;
      onToolResult({
        tool: "apply_story_plan",
        summary: "写入失败：章节数量未完成，实际10/500章",
        changed: false,
      });
      onDone();
    });
    const runtime = await import("./outlineChatRuntime");
    const key = `failed-${Date.now()}`;
    const onOutlineSaved = vi.fn();

    runtime.startOutlineChatRun({
      key,
      projectId: "project",
      messages: [{ role: "user", content: "生成500章" }],
      contextMode: "master",
      persistMessages: vi.fn(),
      onOutlineSaved,
    });

    const run = runtime.getOutlineChatRun(key);
    expect(run?.status).toBe("completed");
    expect(run?.messages.at(-1)?.content).toContain("写入失败");
    expect(run?.messages.at(-1)?.content).not.toContain("已保存");
    expect(onOutlineSaved).not.toHaveBeenCalled();
  });

  it("shows the server's verified chapter count after a successful write", async () => {
    mocks.outlineChatStream.mockImplementation(async (...args: unknown[]) => {
      const onToolResult = args[5] as (data: { tool: string; summary: string; changed: boolean }) => void;
      const onDone = args[7] as () => void;
      onToolResult({
        tool: "apply_story_plan",
        summary: "Story Plan 已写入 500/500 章（版本 2）",
        changed: true,
      });
      onDone();
    });
    const runtime = await import("./outlineChatRuntime");
    const key = `success-${Date.now()}`;
    const onOutlineSaved = vi.fn();

    runtime.startOutlineChatRun({
      key,
      projectId: "project",
      messages: [{ role: "user", content: "生成500章" }],
      contextMode: "master",
      persistMessages: vi.fn(),
      onOutlineSaved,
    });

    const run = runtime.getOutlineChatRun(key);
    expect(run?.messages.at(-1)?.content).toContain("500/500");
    expect(onOutlineSaved).toHaveBeenCalledTimes(1);
  });

  it("refreshes the outline after a verified single-chapter structural change", async () => {
    mocks.outlineChatStream.mockImplementation(async (...args: unknown[]) => {
      const onToolResult = args[5] as (data: { tool: string; summary: string; changed: boolean }) => void;
      const onDone = args[7] as () => void;
      onToolResult({
        tool: "delete_chapter_outline",
        summary: "原第 12 章已删除；当前共 79 章，前移 68 章",
        changed: true,
      });
      onDone();
    });
    const runtime = await import("./outlineChatRuntime");
    const key = `chapter-delete-${Date.now()}`;
    const onOutlineSaved = vi.fn();

    runtime.startOutlineChatRun({
      key,
      projectId: "project",
      messages: [{ role: "user", content: "删除第12章" }],
      contextMode: "master",
      persistMessages: vi.fn(),
      onOutlineSaved,
    });

    const run = runtime.getOutlineChatRun(key);
    expect(run?.messages.at(-1)?.content).toContain("当前共 79 章");
    expect(onOutlineSaved).toHaveBeenCalledTimes(1);
  });
});
