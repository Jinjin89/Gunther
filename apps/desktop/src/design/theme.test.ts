import { afterEach, describe, expect, it, vi } from "vitest";
import { applyTheme, readThemePreference, resolveTheme, THEME_STORAGE_KEY, watchSystemTheme } from "./theme";

const stubSystem = (dark: boolean) => {
  const listeners = new Set<() => void>();
  const query = {
    matches: dark,
    addEventListener: vi.fn((_: string, listener: () => void) => listeners.add(listener)),
    removeEventListener: vi.fn((_: string, listener: () => void) => listeners.delete(listener)),
  };
  vi.stubGlobal("matchMedia", vi.fn(() => query));
  return {
    query,
    change: (next: boolean) => {
      query.matches = next;
      listeners.forEach((listener) => listener());
    },
  };
};

describe("appearance preference", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it("defaults to light and ignores unknown stored values", () => {
    expect(readThemePreference()).toBe("light");
    window.localStorage.setItem(THEME_STORAGE_KEY, "sepia");
    expect(readThemePreference()).toBe("light");
    window.localStorage.setItem(THEME_STORAGE_KEY, "system");
    expect(readThemePreference()).toBe("system");
  });

  it("resolves Match system from the operating system and follows its changes", () => {
    const system = stubSystem(true);
    expect(resolveTheme("system")).toBe("dark");
    expect(resolveTheme("light")).toBe("light");

    const onChange = vi.fn();
    const stop = watchSystemTheme(onChange);
    system.change(false);
    expect(onChange).toHaveBeenCalledOnce();
    expect(resolveTheme("system")).toBe("light");

    stop();
    expect(system.query.removeEventListener).toHaveBeenCalledOnce();
  });

  it("applies the resolved theme to the document", () => {
    applyTheme("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(document.documentElement.style.colorScheme).toBe("dark");
    applyTheme("light");
    expect(document.documentElement.dataset.theme).toBe("light");
  });
});
