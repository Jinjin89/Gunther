import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.hoisted(() => vi.fn());
vi.mock("@tauri-apps/api/core", () => ({ invoke }));

const MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko)";
const shell = window as unknown as Record<string, unknown>;

/** Each test starts a fresh launch: the answer is remembered per launch. */
async function launch(userAgent: string | null) {
  if (userAgent) {
    shell.__TAURI_INTERNALS__ = {};
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue(userAgent);
  }
  vi.resetModules();
  return (await import("./speakerAccess")).allowSpeakerSound;
}

describe("the permission a Mac's speakers need", () => {
  beforeEach(() => invoke.mockReset());
  afterEach(() => {
    delete shell.__TAURI_INTERNALS__;
    vi.restoreAllMocks();
  });

  it("asks nothing in a browser or in the app on other systems", async () => {
    await expect((await launch(null))()).resolves.toBe("granted");
    await expect((await launch("Mozilla/5.0 (Windows NT 10.0; Win64; x64)"))()).resolves.toBe("granted");
    expect(invoke).not.toHaveBeenCalled();
  });

  it("asks the Mac app once per launch, whatever the answer", async () => {
    invoke.mockResolvedValue("denied");
    const allow = await launch(MAC);
    await expect(allow()).resolves.toBe("denied");
    await expect(allow()).resolves.toBe("denied");
    expect(invoke).toHaveBeenCalledTimes(1);
    expect(invoke).toHaveBeenCalledWith("ask_microphone_access");
  });

  it("asks again when the question went unanswered or could not be asked", async () => {
    invoke.mockResolvedValueOnce("undetermined").mockRejectedValueOnce(new Error("no shell")).mockResolvedValueOnce("granted");
    const allow = await launch(MAC);
    await expect(allow()).resolves.toBe("undetermined");
    await expect(allow()).resolves.toBe("undetermined");
    await expect(allow()).resolves.toBe("granted");
    await expect(allow()).resolves.toBe("granted");
    expect(invoke).toHaveBeenCalledTimes(3);
  });
});
