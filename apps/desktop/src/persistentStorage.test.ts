import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { invoke } = vi.hoisted(() => ({ invoke: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke }));
vi.mock("./log", () => ({ log: { error: vi.fn() } }));

describe("native preferences and pending text", () => {
  beforeEach(() => {
    vi.resetModules();
    invoke.mockReset();
    localStorage.clear();
    Object.defineProperty(window, "__TAURI_INTERNALS__", { configurable: true, value: {} });
  });
  afterEach(() => { Reflect.deleteProperty(window, "__TAURI_INTERNALS__"); });

  it("reopens disk preferences and captures after webview storage has been removed", async () => {
    const captures = '[{"id":"kept","content":"An unsent note"}]';
    invoke.mockResolvedValue({ "gunther:theme": "dark", "gunther:local-captures": captures });
    const storage = await import("./persistentStorage");
    await storage.initializePersistentStorage();
    expect(storage.persistentStorage.getItem("gunther:local-captures")).toBe(captures);
    expect(storage.persistentStorage.getItem("gunther:theme")).toBe("dark");
    expect(invoke).toHaveBeenCalledWith("desktop_storage_load", { legacy: {} });
  });

  it("waits for disk persistence and retries a failed pending write", async () => {
    invoke.mockRejectedValueOnce(new Error("temporary disk error")).mockResolvedValue(undefined);
    const storage = await import("./persistentStorage");
    storage.persistentStorage.setItem("gunther:local-captures", "[pending]");
    await storage.flushPersistentStorage();
    expect(invoke).toHaveBeenCalledTimes(2);
    expect(invoke).toHaveBeenLastCalledWith("desktop_storage_set", {
      key: "gunther:local-captures", value: "[pending]",
    });
  });
});
