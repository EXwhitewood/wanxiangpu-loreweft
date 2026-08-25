import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DownloadEvent, Update } from "@tauri-apps/plugin-updater";

const mocks = vi.hoisted(() => ({
  check: vi.fn(),
  getDesktopSystemProxy: vi.fn(),
  prepareDesktopUpdate: vi.fn(),
  recordDesktopUpdateEvent: vi.fn(),
  restartDesktopApp: vi.fn(),
}));

vi.mock("@tauri-apps/plugin-updater", () => ({
  check: mocks.check,
}));

vi.mock("@/utils/platform", () => ({
  isTauri: () => true,
  getDesktopSystemProxy: mocks.getDesktopSystemProxy,
  prepareDesktopUpdate: mocks.prepareDesktopUpdate,
  recordDesktopUpdateEvent: mocks.recordDesktopUpdateEvent,
  restartDesktopApp: mocks.restartDesktopApp,
}));

describe("desktop updater", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubEnv("VITE_DESKTOP_UPDATES_ENABLED", "1");
    mocks.check.mockReset();
    mocks.getDesktopSystemProxy.mockReset();
    mocks.prepareDesktopUpdate.mockReset();
    mocks.recordDesktopUpdateEvent.mockReset();
    mocks.restartDesktopApp.mockReset();
    mocks.prepareDesktopUpdate.mockResolvedValue(undefined);
    mocks.recordDesktopUpdateEvent.mockResolvedValue(undefined);
    mocks.restartDesktopApp.mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("passes the desktop system proxy to the signed update check", async () => {
    mocks.getDesktopSystemProxy.mockResolvedValue("http://127.0.0.1:7890");
    mocks.check.mockResolvedValue(null);
    const { checkDesktopUpdate } = await import("./updater");

    await expect(checkDesktopUpdate()).resolves.toEqual({ kind: "up-to-date" });
    expect(mocks.check).toHaveBeenCalledWith({
      timeout: 12_000,
      proxy: "http://127.0.0.1:7890",
    });
  });

  it("falls back to a direct check when proxy discovery is unavailable", async () => {
    mocks.getDesktopSystemProxy.mockRejectedValue(new Error("invoke unavailable"));
    mocks.check.mockResolvedValue(null);
    const { checkDesktopUpdate } = await import("./updater");

    await expect(checkDesktopUpdate()).resolves.toEqual({ kind: "up-to-date" });
    expect(mocks.check).toHaveBeenCalledWith({ timeout: 12_000 });
  });

  it("retries raw transport errors before returning an actionable message", async () => {
    vi.useFakeTimers();
    mocks.getDesktopSystemProxy.mockResolvedValue(null);
    mocks.check.mockRejectedValue(
      new Error("error sending request for url (https://github.com/example/latest.json)"),
    );
    const { checkDesktopUpdate } = await import("./updater");

    const checking = checkDesktopUpdate();
    await vi.runAllTimersAsync();
    const result = await checking;
    expect(result).toEqual({
      kind: "error",
      message: "无法连接 GitHub 更新源。请确认系统代理或网络连接可用，然后重试；也可以从官方发布页手动下载安装。",
    });
    expect(mocks.check).toHaveBeenCalledTimes(3);
    expect(JSON.stringify(result)).not.toContain("https://github.com/example");
  });

  it("retries a transient check failure with the same resolved proxy", async () => {
    vi.useFakeTimers();
    const update = { version: "0.3.3" } as unknown as Update;
    mocks.getDesktopSystemProxy.mockResolvedValue("http://127.0.0.1:7890");
    mocks.check
      .mockRejectedValueOnce("TLS stream ended with EOF")
      .mockResolvedValueOnce(update);
    const { checkDesktopUpdate } = await import("./updater");

    const checking = checkDesktopUpdate();
    await vi.runAllTimersAsync();
    await expect(checking).resolves.toEqual({ kind: "available", update });

    expect(mocks.getDesktopSystemProxy).toHaveBeenCalledOnce();
    expect(mocks.check).toHaveBeenCalledTimes(2);
    expect(mocks.check).toHaveBeenNthCalledWith(1, {
      timeout: 12_000,
      proxy: "http://127.0.0.1:7890",
    });
    expect(mocks.check).toHaveBeenNthCalledWith(2, {
      timeout: 12_000,
      proxy: "http://127.0.0.1:7890",
    });
  });

  it("does not retry a deterministic missing update manifest", async () => {
    mocks.getDesktopSystemProxy.mockResolvedValue(null);
    mocks.check.mockRejectedValue("Download request failed with status: 404 Not Found");
    const { checkDesktopUpdate } = await import("./updater");

    await expect(checkDesktopUpdate()).resolves.toEqual({
      kind: "error",
      message: "更新清单暂未发布或地址无效，请稍后重试。",
    });
    expect(mocks.check).toHaveBeenCalledOnce();
  });

  it("keeps signature failures distinct from network failures", async () => {
    const { describeDesktopUpdateError } = await import("./updater");

    expect(describeDesktopUpdateError("signature verification failed")).toContain("签名校验失败");
    expect(describeDesktopUpdateError("error sending request: operation timed out")).toContain("无法连接 GitHub");
    expect(describeDesktopUpdateError(new Error("unexpected failure"))).toContain("手动下载安装");
  });

  it("downloads before stopping the backend and only then launches the installer", async () => {
    const order: string[] = [];
    const stages: string[] = [];
    const progress: Array<number | null> = [];
    const download = vi.fn(async (onEvent?: (event: DownloadEvent) => void) => {
      order.push("download");
      onEvent?.({ event: "Started", data: { contentLength: 100 } });
      onEvent?.({ event: "Progress", data: { chunkLength: 100 } });
      onEvent?.({ event: "Finished" });
    });
    const install = vi.fn(async () => {
      order.push("install");
    });
    mocks.prepareDesktopUpdate.mockImplementation(async () => {
      order.push("prepare");
    });
    const update = { download, install } as unknown as Update;
    const { installDesktopUpdate } = await import("./updater");

    await installDesktopUpdate(update, (value) => progress.push(value), (stage) => stages.push(stage));

    expect(order).toEqual(["download", "prepare", "install"]);
    expect(stages).toEqual(["downloading", "preparing", "installing"]);
    expect(progress).toEqual([0, 100, 100]);
    expect(download).toHaveBeenCalledWith(expect.any(Function), { timeout: 30 * 60_000 });
  });

  it("retries a transient string transport failure before stopping the backend", async () => {
    vi.useFakeTimers();
    const order: string[] = [];
    const stages: string[] = [];
    const download = vi
      .fn()
      .mockRejectedValueOnce("error sending request for url (https://example.invalid/update)")
      .mockImplementationOnce(async () => {
        order.push("download-success");
      });
    const install = vi.fn(async () => {
      order.push("install");
    });
    mocks.prepareDesktopUpdate.mockImplementation(async () => {
      order.push("prepare");
    });
    const update = { version: "0.3.3", download, install } as unknown as Update;
    const { installDesktopUpdate } = await import("./updater");

    const installing = installDesktopUpdate(update, undefined, (stage) => stages.push(stage));
    await vi.runAllTimersAsync();
    await installing;

    expect(download).toHaveBeenCalledTimes(2);
    expect(download).toHaveBeenNthCalledWith(1, expect.any(Function), { timeout: 30 * 60_000 });
    expect(download).toHaveBeenNthCalledWith(2, expect.any(Function), { timeout: 30 * 60_000 });
    expect(order).toEqual(["download-success", "prepare", "install"]);
    expect(stages).toEqual(["downloading", "retrying", "preparing", "installing"]);
    expect(mocks.recordDesktopUpdateEvent).toHaveBeenCalledWith(
      "download_retryable_error",
      expect.not.stringContaining("https://example.invalid"),
    );
    vi.useRealTimers();
  });

  it("never retries a deterministic signature failure or stops the backend", async () => {
    const download = vi.fn().mockRejectedValue("signature verification failed");
    const install = vi.fn(async () => {});
    const update = { version: "0.3.3", download, install } as unknown as Update;
    const { installDesktopUpdate } = await import("./updater");

    await expect(installDesktopUpdate(update)).rejects.toBe("signature verification failed");

    expect(download).toHaveBeenCalledOnce();
    expect(mocks.prepareDesktopUpdate).not.toHaveBeenCalled();
    expect(install).not.toHaveBeenCalled();
  });

  it("stops after three transient failures without preparing or installing", async () => {
    vi.useFakeTimers();
    const download = vi.fn().mockRejectedValue("TLS stream ended with EOF");
    const install = vi.fn(async () => {});
    const update = { version: "0.3.3", download, install } as unknown as Update;
    const { installDesktopUpdate } = await import("./updater");

    const installing = installDesktopUpdate(update);
    const rejection = expect(installing).rejects.toBe("TLS stream ended with EOF");
    await vi.runAllTimersAsync();
    await rejection;

    expect(download).toHaveBeenCalledTimes(3);
    expect(mocks.prepareDesktopUpdate).not.toHaveBeenCalled();
    expect(install).not.toHaveBeenCalled();
    vi.useRealTimers();
  });

  it("shares one active install across concurrent update entry points", async () => {
    let releaseDownload: (() => void) | undefined;
    const download = vi.fn(() => new Promise<void>((resolve) => {
      releaseDownload = resolve;
    }));
    const install = vi.fn(async () => {});
    const update = { version: "0.3.3", download, install } as unknown as Update;
    const { installDesktopUpdate } = await import("./updater");

    const first = installDesktopUpdate(update);
    const second = installDesktopUpdate(update);
    expect(second).toBe(first);
    await vi.waitFor(() => expect(download).toHaveBeenCalledOnce());

    releaseDownload?.();
    await first;
    expect(mocks.prepareDesktopUpdate).toHaveBeenCalledOnce();
    expect(install).toHaveBeenCalledOnce();
  });

  it("defers and deduplicates resource release until its active install settles", async () => {
    const order: string[] = [];
    let releaseDownload: (() => void) | undefined;
    const download = vi.fn(() => new Promise<void>((resolve) => {
      releaseDownload = resolve;
    }));
    const install = vi.fn(async () => {
      order.push("install");
    });
    const close = vi.fn(async () => {
      order.push("close");
    });
    const update = { version: "0.3.3", download, install, close } as unknown as Update;
    const { installDesktopUpdate, releaseDesktopUpdate } = await import("./updater");

    const installing = installDesktopUpdate(update);
    await vi.waitFor(() => expect(download).toHaveBeenCalledOnce());
    const firstRelease = releaseDesktopUpdate(update);
    const secondRelease = releaseDesktopUpdate(update);

    expect(secondRelease).toBe(firstRelease);
    expect(close).not.toHaveBeenCalled();
    releaseDownload?.();
    await installing;
    await firstRelease;

    expect(order).toEqual(["install", "close"]);
    expect(close).toHaveBeenCalledOnce();
  });

  it("blocks installation and restarts the current version when backend shutdown fails", async () => {
    const recoveryOrder: string[] = [];
    const download = vi.fn(async () => {});
    const install = vi.fn(async () => {});
    mocks.recordDesktopUpdateEvent.mockImplementation(async (stage: string) => {
      if (stage === "installation_error") recoveryOrder.push("log");
    });
    mocks.restartDesktopApp.mockImplementation(async () => {
      recoveryOrder.push("restart");
    });
    mocks.prepareDesktopUpdate.mockRejectedValue(
      new Error("无法安全关闭本地后台：port remained occupied"),
    );
    const update = { download, install } as unknown as Update;
    const { installDesktopUpdate } = await import("./updater");

    await expect(installDesktopUpdate(update)).rejects.toThrow("无法安全关闭本地后台");
    expect(install).not.toHaveBeenCalled();
    expect(mocks.restartDesktopApp).toHaveBeenCalledOnce();
    expect(recoveryOrder).toEqual(["log", "restart"]);
  });
});
