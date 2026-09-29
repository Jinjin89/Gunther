import type { ModelMenu } from "@gunther/contracts";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { OPEN_SETTINGS_EVENT, resolveEffort, usableChoice } from "./askModel";
import { ModelPicker } from "./ModelPicker";

const levels = (...ids: [string, string][]) => ids.map(([id, label]) => ({ id, label })) as ModelMenu["efforts"];

const menu: ModelMenu = {
  models: [
    { ref: "deepseek/deepseek-flash", provider: "DeepSeek", label: "Flash", display: "DeepSeek · Flash", vision: true, levels: levels(["off", "Off"], ["low", "Low"], ["high", "High"], ["max", "Max"]) },
    { ref: "glm/glm-5.3", provider: "GLM", label: "5.3", display: "GLM · 5.3", vision: false, levels: levels(["low", "Low"], ["high", "High"], ["max", "Max"]) },
    { ref: "kimi/kimi-k2.7-code", provider: "Kimi", label: "K2.7 Code", display: "Kimi · K2.7 Code", vision: false, levels: [] },
  ],
  default: { model: "deepseek/deepseek-flash", effort: "high" },
  efforts: levels(["off", "Off"], ["low", "Low"], ["medium", "Medium"], ["high", "High"], ["max", "Max"]),
};

describe("resolveEffort", () => {
  it("uses the nearest level the model has, and never turns thinking off unasked", () => {
    const deepseek = menu.models[0]!.levels;
    expect(resolveEffort("medium", deepseek)?.label).toBe("High");
    expect(resolveEffort("off", deepseek)?.label).toBe("Off");
    const glm = menu.models[1]!.levels;
    expect(resolveEffort("off", glm)?.label).toBe("Low");
    const onOff = levels(["off", "Off"], ["high", "On"]);
    expect(resolveEffort("low", onOff)?.label).toBe("On");
    expect(resolveEffort("max", [])).toBeNull();
  });

  it("keeps a choice only while its model is set up", () => {
    expect(usableChoice(menu, { model: "gone/model", effort: "max" }, { model: "glm/glm-5.3", effort: "low" })).toEqual({ model: "glm/glm-5.3", effort: "low" });
    expect(usableChoice(menu, null)).toEqual({ model: "deepseek/deepseek-flash", effort: "high" });
  });
});

describe("ModelPicker", () => {
  it("switches model from a menu grouped by provider, keeping the effort", async () => {
    const onChange = vi.fn();
    render(<ModelPicker menu={menu} choice={{ model: "deepseek/deepseek-flash", effort: "max" }} onChange={onChange} />);
    const chip = screen.getByRole("button", { name: "Model: DeepSeek · Flash" });
    await userEvent.click(chip);
    expect(screen.getByRole("group", { name: "GLM" })).toBeInTheDocument();
    expect(screen.getByRole("menuitemradio", { name: /Flash/ })).toHaveAttribute("aria-checked", "true");
    await userEvent.click(screen.getByRole("menuitemradio", { name: /5\.3/ }));
    expect(onChange).toHaveBeenCalledWith({ model: "glm/glm-5.3", effort: "max" });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("shows the level a model will really use, and why", async () => {
    const onChange = vi.fn();
    render(<ModelPicker menu={menu} choice={{ model: "glm/glm-5.3", effort: "off" }} onChange={onChange} />);
    const effort = screen.getByRole("button", { name: "Thinking effort: Low" });
    expect(effort).toHaveAttribute("title", "5.3 has no Off setting; it uses Low.");
    await userEvent.click(effort);
    expect(screen.getByRole("menuitemradio", { name: /Off/ })).toHaveTextContent("always thinks");
    expect(screen.getByRole("menuitemradio", { name: /Medium/ })).toHaveTextContent("uses High");
    await userEvent.keyboard("{ArrowDown}{Enter}");
    expect(onChange).toHaveBeenCalledWith({ model: "glm/glm-5.3", effort: "low" });
  });

  it("says when a model has no effort setting", () => {
    render(<ModelPicker menu={menu} choice={{ model: "kimi/kimi-k2.7-code", effort: "high" }} onChange={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Thinking effort: fixed" })).toBeDisabled();
  });

  it("closes with Escape and returns focus to its chip", async () => {
    render(<ModelPicker menu={menu} choice={{ model: "deepseek/deepseek-flash", effort: "high" }} onChange={vi.fn()} />);
    const chip = screen.getByRole("button", { name: "Model: DeepSeek · Flash" });
    await userEvent.click(chip);
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(chip).toHaveFocus();
  });

  it("offers to set up a model when none is ready", async () => {
    const opened = vi.fn();
    window.addEventListener(OPEN_SETTINGS_EVENT, opened);
    render(<ModelPicker menu={{ ...menu, models: [], default: null }} choice={{ model: null, effort: "high" }} onChange={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Set up a model" }));
    expect(opened).toHaveBeenCalledOnce();
    window.removeEventListener(OPEN_SETTINGS_EVENT, opened);
  });
});
