import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsNav, openSettings, useSettingsAttention, useSettingsPane } from "./SettingsLayout";

const api = vi.hoisted(() => ({
  modelsOverview: vi.fn(),
  speechOverview: vi.fn(),
  ttsOverview: vi.fn(),
}));
vi.mock("../api", () => ({ knowledgeApi: api }));

function Harness() {
  const [pane, setPane] = useSettingsPane();
  const attention = useSettingsAttention(pane);
  return <><SettingsNav pane={pane} attention={attention} onPane={setPane} /><p data-testid="pane">{pane}</p></>;
}

describe("Settings topics", () => {
  beforeEach(() => {
    window.localStorage.clear();
    api.modelsOverview.mockResolvedValue({ roles: [{ id: "ask", problem: "DeepSeek needs an API key." }] });
    api.speechOverview.mockResolvedValue({ roles: [{ id: "recording", problem: null }] });
    // Read aloud turned off is not a problem to flag.
    api.ttsOverview.mockResolvedValue({ roles: [{ id: "answers", model: null, problem: null }] });
  });

  it("shows one topic, remembers it, and marks a topic that needs attention", async () => {
    const user = userEvent.setup();
    const { unmount } = render(<Harness />);
    expect(screen.getByTestId("pane").textContent).toBe("general");
    expect(screen.getByRole("button", { name: "General" }).getAttribute("aria-current")).toBe("page");
    await user.click(screen.getByRole("button", { name: /Voice/ }));
    expect(screen.getByTestId("pane").textContent).toBe("voice");
    const models = await screen.findByRole("button", { name: /Models/ });
    await waitFor(() => expect(models.querySelector(".settings-nav-dot")).not.toBeNull());
    expect(screen.getByRole("button", { name: /Voice/ }).querySelector(".settings-nav-dot")).toBeNull();
    unmount();
    render(<Harness />);
    expect(screen.getByTestId("pane").textContent).toBe("voice");
  });

  it("opens on the topic asked for from elsewhere", () => {
    render(<Harness />);
    act(() => openSettings("search"));
    expect(screen.getByTestId("pane").textContent).toBe("search");
  });
});
