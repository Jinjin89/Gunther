import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { KnowledgeTopic, SourceStructure } from "@gunther/contracts";
import { knowledgeApi } from "../api";
import { SourceEvidence } from "./SourceEvidence";
import { TopicManager } from "./TopicManager";

const topic: KnowledgeTopic = {
  id: "topic_1", knowledgeBaseId: "biology", parentId: null, title: "Genomics",
  description: "Study genes", position: 0, version: 2, blockIds: [],
};
const ready: SourceStructure = {
  sourceId: "source_1", revisionId: "revision_1", parser: "text-v1", nextOffset: null,
  processing: { state: "ready", revisionId: "revision_1", jobId: null, warning: null },
  blocks: [{ id: "block_1", parentId: null, kind: "paragraph", content: "Genome quality evidence.", locator: "Page 8", headings: ["Methods"], anchor: { page: 8 } }],
};

beforeEach(() => {
  vi.spyOn(knowledgeApi, "sourceStructure").mockResolvedValue(ready);
  vi.spyOn(knowledgeApi, "topics").mockResolvedValue([topic]);
});
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe("Structured evidence", () => {
  it("opens the pinned revision/block and links evidence without changing the source", async () => {
    const user = userEvent.setup();
    const link = vi.spyOn(knowledgeApi, "linkTopicEvidence").mockResolvedValue({ ...topic, blockIds: ["block_1"] });
    render(<SourceEvidence sourceId="source_1" assetId="asset_1" baseId="biology" revisionId="revision_1" blockId="block_1" />);
    await screen.findByText("Genome quality evidence.");
    expect(knowledgeApi.sourceStructure).toHaveBeenCalledWith("source_1", 0, "revision_1", "block_1");
    expect(screen.getByRole("link", { name: "Open page 8" }).getAttribute("href")).toContain("#page=8");
    expect(screen.queryByRole("button", { name: "Reprocess source" })).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Evidence topic"), "topic_1");
    await user.click(screen.getByRole("button", { name: "Link to topic" }));
    expect(link).toHaveBeenCalledWith("biology", "topic_1", "block_1");
    await screen.findByRole("button", { name: "Linked" });
  });

  it("polls queued work until readable evidence is ready", async () => {
    vi.useFakeTimers();
    vi.mocked(knowledgeApi.sourceStructure).mockResolvedValueOnce({
      ...ready, blocks: [], processing: { ...ready.processing, state: "queued" },
    }).mockResolvedValue(ready);
    await act(async () => { render(<SourceEvidence sourceId="source_1" />); });
    expect(screen.getByText(/Original saved/)).toBeVisible();
    await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
    expect(screen.getByText("Genome quality evidence.")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Cancel processing" })).not.toBeInTheDocument();
  });

  it("surfaces failed extraction without hiding preserved evidence", async () => {
    vi.mocked(knowledgeApi.sourceStructure).mockResolvedValue({
      ...ready, processing: { ...ready.processing, state: "failed", warning: "Original preserved. Retry processing." },
    });
    render(<SourceEvidence sourceId="source_1" />);
    expect(await screen.findByText("Original preserved. Retry processing.")).toBeVisible();
    expect(screen.getByText("Genome quality evidence.")).toBeVisible();
  });
});

describe("Knowledge topics", () => {
  it("creates a persistent child topic", async () => {
    const user = userEvent.setup();
    const save = vi.spyOn(knowledgeApi, "saveTopic").mockResolvedValue(topic);
    const changed = vi.fn();
    render(<TopicManager baseId="biology" topics={[topic]} onChange={changed} onAsk={vi.fn()} />);
    await user.type(screen.getByLabelText("Topic name"), "Quality control");
    await user.selectOptions(screen.getByLabelText("Parent topic"), "topic_1");
    await user.click(screen.getByRole("button", { name: "Add topic" }));
    expect(save).toHaveBeenCalledWith("biology", expect.objectContaining({ title: "Quality control", parentId: "topic_1" }), undefined);
    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
  });

  it("retains edits and reports a version conflict", async () => {
    const user = userEvent.setup();
    const save = vi.spyOn(knowledgeApi, "saveTopic").mockRejectedValue(new Error("This topic changed. Reload before saving."));
    render(<TopicManager baseId="biology" topics={[topic]} onChange={vi.fn()} onAsk={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Edit" }));
    await user.click(screen.getByRole("button", { name: "Save topic" }));
    expect(save).toHaveBeenCalledWith("biology", expect.objectContaining({ version: 2 }), "topic_1");
    expect(await screen.findByRole("alert")).toHaveTextContent("Reload before saving");
    expect(screen.getByLabelText("Topic name")).toHaveValue("Genomics");
  });
});
