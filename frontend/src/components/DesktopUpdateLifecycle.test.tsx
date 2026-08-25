import { StrictMode } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Update } from "@tauri-apps/plugin-updater";

const mocks = vi.hoisted(() => ({
  checkDesktopUpdate: vi.fn(),
  installDesktopUpdate: vi.fn(),
  releaseDesktopUpdate: vi.fn(),
  restartDesktopApp: vi.fn(),
}));

vi.mock("@/utils/platform", () => ({
  isTauri: () => true,
  restartDesktopApp: mocks.restartDesktopApp,
}));

vi.mock("@/utils/updater", () => ({
  checkDesktopUpdate: mocks.checkDesktopUpdate,
  describeDesktopUpdateError: () => "更新失败",
  desktopUpdatesEnabled: true,
  installDesktopUpdate: mocks.installDesktopUpdate,
  releaseDesktopUpdate: mocks.releaseDesktopUpdate,
}));

import DesktopUpdateNotice from "./DesktopUpdateNotice";
import DesktopUpdateSettingsSection from "@/pages/sections/DesktopUpdateSettingsSection";

describe("desktop update resource lifecycle", () => {
  beforeEach(() => {
    mocks.checkDesktopUpdate.mockReset();
    mocks.installDesktopUpdate.mockReset();
    mocks.releaseDesktopUpdate.mockReset();
    mocks.restartDesktopApp.mockReset();
    mocks.releaseDesktopUpdate.mockResolvedValue(undefined);
    mocks.restartDesktopApp.mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("releases a startup-check result that arrives after the notice unmounts", async () => {
    vi.useFakeTimers();
    let resolveCheck: ((result: { kind: "available"; update: Update }) => void) | undefined;
    const update = { version: "0.3.3", close: vi.fn() } as unknown as Update;
    mocks.checkDesktopUpdate.mockImplementation(
      () => new Promise((resolve) => {
        resolveCheck = resolve;
      }),
    );

    const view = render(
      <StrictMode>
        <DesktopUpdateNotice />
      </StrictMode>,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6_000);
    });
    expect(mocks.checkDesktopUpdate).toHaveBeenCalledOnce();

    view.unmount();
    await act(async () => {
      resolveCheck?.({ kind: "available", update });
      await Promise.resolve();
    });

    expect(mocks.releaseDesktopUpdate).toHaveBeenCalledWith(update);
    expect(update.close).not.toHaveBeenCalled();
  });

  it("cannot dismiss the notice while an install owns the update resource", async () => {
    vi.useFakeTimers();
    const update = { version: "0.3.3", close: vi.fn() } as unknown as Update;
    mocks.checkDesktopUpdate.mockResolvedValue({ kind: "available", update });
    mocks.installDesktopUpdate.mockImplementation(() => new Promise<void>(() => {}));

    const view = render(
      <StrictMode>
        <DesktopUpdateNotice />
      </StrictMode>,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6_000);
    });
    expect(screen.getByText("发现新版本 v0.3.3")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "下载并重启" }));
    expect(screen.queryByRole("button", { name: "关闭更新提示" })).not.toBeInTheDocument();

    view.unmount();
    expect(mocks.releaseDesktopUpdate).toHaveBeenCalledWith(update);
    expect(update.close).not.toHaveBeenCalled();
  });

  it("releases the previous settings result when checking again and the current one on unmount", async () => {
    const first = { version: "0.3.3" } as unknown as Update;
    const second = { version: "0.3.4" } as unknown as Update;
    mocks.checkDesktopUpdate
      .mockResolvedValueOnce({ kind: "available", update: first })
      .mockResolvedValueOnce({ kind: "available", update: second });

    const view = render(
      <StrictMode>
        <DesktopUpdateSettingsSection />
      </StrictMode>,
    );
    fireEvent.click(screen.getByRole("button", { name: "检查更新" }));
    expect(await screen.findByText("发现新版本 v0.3.3")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "检查更新" }));
    expect(await screen.findByText("发现新版本 v0.3.4")).toBeInTheDocument();
    expect(mocks.releaseDesktopUpdate).toHaveBeenCalledWith(first);

    view.unmount();
    await waitFor(() => {
      expect(mocks.releaseDesktopUpdate).toHaveBeenCalledWith(second);
    });
  });
});
