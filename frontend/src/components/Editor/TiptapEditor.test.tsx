import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import TiptapEditor from "./TiptapEditor";

describe("TiptapEditor word count", () => {
  it("updates without typing when an existing chapter arrives asynchronously", async () => {
    const onChange = vi.fn();
    const { rerender } = render(<TiptapEditor content="" onChange={onChange} />);

    expect(screen.getByText("0 字")).toBeInTheDocument();

    rerender(
      <TiptapEditor
        content="<p>异步正文 alpha 42</p>"
        onChange={onChange}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("6 字")).toBeInTheDocument();
    });
  });
});
