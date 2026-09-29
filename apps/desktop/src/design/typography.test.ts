import { beforeEach, describe, expect, it } from "vitest";
import { applyTypography, readFont, readTextSize, saveTypography } from "./typography";

describe("typography", () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.removeAttribute("style");
  });

  it("starts comfortable, a little larger than the old small text", () => {
    expect(readTextSize()).toBe("default");
    applyTypography();
    expect(document.documentElement.style.getPropertyValue("--gx-fs")).toBe("1.1");
  });

  it("remembers a choice and applies it to the whole app", () => {
    saveTypography("xlarge", "serif");
    expect(readTextSize()).toBe("xlarge");
    expect(readFont()).toBe("serif");
    expect(document.documentElement.dataset.font).toBe("serif");
    expect(document.documentElement.style.getPropertyValue("--gx-fs")).toBe("1.36");
  });

  it("ignores a stored value it does not know", () => {
    window.localStorage.setItem("gunther:text-size", "gigantic");
    expect(readTextSize()).toBe("default");
  });
});
