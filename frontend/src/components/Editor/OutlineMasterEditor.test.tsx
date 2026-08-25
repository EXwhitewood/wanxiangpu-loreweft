import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "@/api/client";
import OutlineMasterEditor from "./OutlineMasterEditor";

vi.mock("@/api/client", () => ({
  readDraftOutline: vi.fn(),
  confirmDraftOutline: vi.fn(),
  discardDraftOutline: vi.fn(),
  getStoryPlan: vi.fn(),
  updateStoryPlanLayer: vi.fn(),
  updateChapterSpineItem: vi.fn(),
  appendChapterSpineItem: vi.fn(),
  deleteChapterSpineItem: vi.fn(),
}));

vi.mock("@/components/Editor/OutlineVisualization", () => ({ default: () => null }));
vi.mock("@/components/Editor/SceneBriefPanel", () => ({ default: () => null }));
vi.mock("@/components/Editor/ThreadPlanPanel", () => ({ default: () => null }));

const officialOutline = {
  chapter_spine: [
    {
      chapter_number: 1,
      title: "正式章节",
      conflict_text: "正式冲突",
      value_shift: "平静 -> 决断",
    },
  ],
};

const draftResult = {
  has_draft: true,
  draft: {
    chapter_spine: [
      {
        chapter_number: 1,
        title: "草稿章节",
        conflict_text: "草稿冲突",
        value_shift: "犹豫 -> 行动",
      },
    ],
  },
  diff_summary: {
    total_changes: 1,
    change_types: ["chapter_modify"],
    changes: [],
  },
};

function renderEditor(onSave = vi.fn()) {
  render(
    <OutlineMasterEditor
      projectId="project-1"
      outlineData={officialOutline}
      frozen={false}
      onSave={onSave}
      onSelectChapter={vi.fn()}
    />,
  );
  return { onSave };
}

describe("OutlineMasterEditor draft fallback", () => {
  beforeEach(() => {
    vi.mocked(api.readDraftOutline).mockReset();
    vi.mocked(api.confirmDraftOutline).mockReset();
    vi.mocked(api.discardDraftOutline).mockReset();
  });

  it("keeps the official outline visible after a load failure and can retry", async () => {
    vi.mocked(api.readDraftOutline)
      .mockRejectedValueOnce(new Error("网络中断"))
      .mockResolvedValueOnce(draftResult);
    renderEditor();

    fireEvent.click(screen.getByRole("button", { name: "草稿" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("草稿加载失败：网络中断");
    expect(screen.getByText("正式大纲")).toBeInTheDocument();
    expect(screen.getByText(/正式章节/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "重试加载" }));

    expect(await screen.findByText("大纲修改草稿")).toBeInTheDocument();
    expect(screen.getByText("草稿大纲")).toBeInTheDocument();
    expect(screen.getByText(/草稿章节/)).toBeInTheDocument();
    expect(api.readDraftOutline).toHaveBeenCalledTimes(2);
  });

  it("does not enter draft mode when the server reports an incomplete draft", async () => {
    vi.mocked(api.readDraftOutline).mockResolvedValue({
      has_draft: true,
      draft: null,
      diff_summary: null,
    });
    renderEditor();

    fireEvent.click(screen.getByRole("button", { name: "草稿" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("草稿数据不完整");
    expect(screen.getByText("正式大纲")).toBeInTheDocument();
    expect(screen.queryByText("大纲修改草稿")).not.toBeInTheDocument();
  });

  it("retains the draft and exposes confirmation and discard failures", async () => {
    const { onSave } = renderEditor();
    vi.mocked(api.readDraftOutline).mockResolvedValue(draftResult);
    vi.mocked(api.confirmDraftOutline).mockRejectedValueOnce(new Error("确认接口故障"));
    vi.mocked(api.discardDraftOutline).mockRejectedValueOnce(new Error("丢弃接口故障"));

    fireEvent.click(screen.getByRole("button", { name: "草稿" }));
    await screen.findByText("大纲修改草稿");

    fireEvent.click(screen.getByRole("button", { name: "保存为正式大纲" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "草稿确认失败：确认接口故障。正式大纲未发生变化，当前草稿仍保留。",
    );
    expect(screen.getByText(/草稿章节/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "丢弃草稿" }));
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(
        "草稿丢弃失败：丢弃接口故障。正式大纲未发生变化，当前草稿仍保留。",
      );
    });
    expect(screen.getByText(/草稿章节/)).toBeInTheDocument();
    expect(onSave).not.toHaveBeenCalled();
  });
});
