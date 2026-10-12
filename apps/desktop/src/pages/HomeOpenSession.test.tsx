import { render, waitFor } from "@testing-library/react";
import { act } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { persistentStorage } from "../persistentStorage";
import { HomePage } from "./HomePage";
import { OPEN_HOME_SESSION_KEY } from "./useHomeAsk";

const api = vi.hoisted(() => ({
  sources: vi.fn(),
  sessions: vi.fn(),
  session: vi.fn(),
  followAnswer: vi.fn(),
}));
vi.mock("../api", () => ({ knowledgeApi: api, AnswerStoppedError: class extends Error {} }));

const home = () => (
  <HomePage bases={[]} inboxCount={0} onCapture={vi.fn()} onOpenBase={vi.fn()} onOpenChapter={vi.fn()} onAskBase={vi.fn()} onOpenNote={vi.fn()} onOpenLibraries={vi.fn()} onCreateBase={vi.fn()} onOpenInbox={vi.fn()} onNotify={vi.fn()} resolveWorkspaceId={vi.fn().mockResolvedValue("wsp")} />
);

describe("Activity opens a Home conversation", () => {
  beforeEach(() => {
    window.localStorage.clear();
    api.sources.mockResolvedValue([]);
    api.sessions.mockResolvedValue([]);
    api.followAnswer.mockResolvedValue(null);
    api.session.mockReset();
    api.session.mockImplementation(async (id: string) => ({ id, knowledgeBaseId: "@home", title: "Why", messages: [], updatedAt: "2026-10-12T10:00:00Z" }));
  });

  it("opens the conversation left for a Home that was not on screen, once", async () => {
    persistentStorage.setItem(OPEN_HOME_SESSION_KEY, "ses_9");
    render(home());
    await waitFor(() => expect(api.session).toHaveBeenCalledWith("ses_9"));
    expect(persistentStorage.getItem(OPEN_HOME_SESSION_KEY)).toBeNull();
  });

  it("opens it when Home is already on screen", async () => {
    render(home());
    await act(async () => {
      persistentStorage.setItem(OPEN_HOME_SESSION_KEY, "ses_7");
      window.dispatchEvent(new CustomEvent(OPEN_HOME_SESSION_KEY, { detail: { sessionId: "ses_7" } }));
    });
    await waitFor(() => expect(api.session).toHaveBeenCalledWith("ses_7"));
    expect(persistentStorage.getItem(OPEN_HOME_SESSION_KEY)).toBeNull();
  });
});
