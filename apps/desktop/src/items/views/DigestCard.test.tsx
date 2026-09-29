import type { SourceDigestState } from "@gunther/contracts";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { knowledgeApi } from "../../api";
import { DigestCard } from "./DigestCard";

vi.mock("../../api", () => ({
  knowledgeApi: { sourceDigest: vi.fn(), writeSourceDigest: vi.fn() },
}));

const ready: SourceDigestState = {
  sourceId: "src_1",
  state: "ready",
  stale: false,
  method: "deepseek:deepseek-v4-flash",
  offReason: null,
  error: null,
  digest: {
    sourceId: "src_1",
    revisionId: "srev_1",
    profile: "meeting",
    method: "deepseek:deepseek-v4-flash",
    engine: "deepseek",
    model: "deepseek-v4-flash",
    suggestedTitle: "Assay planning",
    overview: "The team agreed to rerun the assay.",
    keyPoints: [{
      text: "The assay is rerun with fresh controls.",
      citations: [{ number: 3, blockId: "blk_3", quote: "We will rerun it with fresh controls.", locator: "Transcript" }],
    }],
    actionItems: ["Dana orders reagents by Friday"],
    openQuestions: [],
    terms: ["qPCR"],
    markdown: "",
    updatedAt: "2026-09-29T10:00:00Z",
  },
};

describe("DigestCard", () => {
  beforeEach(() => {
    vi.useRealTimers();
    vi.mocked(knowledgeApi.sourceDigest).mockReset();
    vi.mocked(knowledgeApi.writeSourceDigest).mockReset();
  });

  it("shows a meeting's notes and opens the passage a point came from", async () => {
    vi.mocked(knowledgeApi.sourceDigest).mockResolvedValue(ready);
    const user = userEvent.setup();
    render(<DigestCard sourceId="src_1" />);

    expect(await screen.findByRole("heading", { name: "Meeting notes" })).toBeVisible();
    expect(screen.getByText("The team agreed to rerun the assay.")).toBeVisible();
    expect(screen.getByRole("heading", { name: "Action items" })).toBeVisible();
    expect(screen.getByText("Dana orders reagents by Friday")).toBeVisible();
    expect(screen.getByText(/Written by DeepSeek deepseek-v4-flash/)).toBeVisible();
    expect(screen.queryByText("We will rerun it with fresh controls.")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Show the passage this comes from/ }));
    expect(screen.getByText("We will rerun it with fresh controls.")).toBeVisible();
  });

  it("follows a summary while it is written, then tells the page", async () => {
    vi.mocked(knowledgeApi.sourceDigest)
      .mockResolvedValueOnce({ ...ready, state: "writing", digest: null })
      .mockResolvedValue(ready);
    const onWritten = vi.fn();
    render(<DigestCard sourceId="src_1" onWritten={onWritten} />);

    expect(await screen.findByText("Writing a summary…")).toBeVisible();
    await waitFor(() => expect(screen.getByRole("heading", { name: "Meeting notes" })).toBeVisible(), { timeout: 4_000 });
    expect(onWritten).toHaveBeenCalledOnce();
  });

  it("offers to summarize when there is no summary, and stays out of the way when they are off", async () => {
    vi.mocked(knowledgeApi.sourceDigest)
      .mockResolvedValueOnce({ ...ready, state: "none", digest: null })
      .mockResolvedValue({ ...ready, state: "writing", digest: null });
    vi.mocked(knowledgeApi.writeSourceDigest).mockResolvedValue({ ...ready, state: "writing", digest: null });
    const user = userEvent.setup();
    const view = render(<DigestCard sourceId="src_1" />);

    await user.click(await screen.findByRole("button", { name: "Summarize" }));
    expect(knowledgeApi.writeSourceDigest).toHaveBeenCalledWith("src_1");
    expect(await screen.findByText("Writing a summary…")).toBeVisible();
    view.unmount();

    vi.mocked(knowledgeApi.sourceDigest).mockResolvedValue({ ...ready, state: "off", offReason: "setting", method: null, digest: null });
    const off = render(<DigestCard sourceId="src_2" />);
    await waitFor(() => expect(knowledgeApi.sourceDigest).toHaveBeenCalledWith("src_2"));
    expect(off.container).toBeEmptyDOMElement();
  });

  it("says a key is needed rather than showing a lesser summary", async () => {
    vi.mocked(knowledgeApi.sourceDigest).mockResolvedValue({ ...ready, state: "off", offReason: "no_key", method: null, digest: null });
    render(<DigestCard sourceId="src_1" />);
    expect(await screen.findByText(/Summaries need an OpenAI or DeepSeek API key/)).toBeVisible();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("shows why the model failed and lets it be tried again", async () => {
    vi.mocked(knowledgeApi.sourceDigest)
      .mockResolvedValueOnce({ ...ready, state: "failed", error: "The model could not write the summary (APIConnectionError).", digest: null })
      .mockResolvedValue({ ...ready, state: "writing", digest: null });
    vi.mocked(knowledgeApi.writeSourceDigest).mockResolvedValue({ ...ready, state: "writing", digest: null });
    const user = userEvent.setup();
    render(<DigestCard sourceId="src_1" />);

    expect(await screen.findByRole("alert")).toHaveTextContent("The model could not write the summary (APIConnectionError).");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(knowledgeApi.writeSourceDigest).toHaveBeenCalledWith("src_1");
    expect(await screen.findByText("Writing a summary…")).toBeVisible();
  });
});
