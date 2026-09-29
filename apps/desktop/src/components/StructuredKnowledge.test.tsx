import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { KnowledgeTopic, SourceStructure } from "@gunther/contracts";
import { knowledgeApi } from "../api";
import { SourceEvidence } from "./SourceEvidence";
import { TopicManager } from "./TopicManager";

const topic: KnowledgeTopic = {
  id: "topic_1", knowledgeBaseId: "biology", parentId: null, title: "Genomics",
  description: "Study genes", position: 0, version: 2, blockIds: [], sourceIds: [],
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

  it("turns a suggested group of papers into a topic", async () => {
    const user = userEvent.setup();
    vi.spyOn(knowledgeApi, "topicSuggestions").mockResolvedValue({
      suggestions: [{ key: "k1", title: "Rocket · Fuel", keywords: ["rocket", "fuel"], sourceIds: ["s1", "s2"], examples: ["Reusable boosters", "Liquid oxygen"], years: [2019, 2024] }],
      unfiled: 9, method: "semantic", reason: null,
    });
    const save = vi.spyOn(knowledgeApi, "saveTopic").mockResolvedValue({ ...topic, id: "topic_2", sourceIds: ["s1", "s2"] });
    const changed = vi.fn();
    render(<TopicManager baseId="biology" topics={[topic]} onChange={changed} onAsk={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Suggest topics" }));
    expect(await screen.findByText("9 papers not in a topic, grouped by meaning.")).toBeVisible();
    expect(screen.getByText("2 papers · 2019–2024 · rocket, fuel")).toBeVisible();
    const name = screen.getByDisplayValue("Rocket · Fuel");
    await user.clear(name);
    await user.type(name, "Launch vehicles");
    await user.click(screen.getByRole("button", { name: "Create topic" }));
    expect(save).toHaveBeenCalledWith("biology", expect.objectContaining({ title: "Launch vehicles", sourceIds: ["s1", "s2"] }));
    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
    expect(await screen.findByText("All suggestions are now topics.")).toBeVisible();
  });

  it("writes a topic overview and opens a cited paper", async () => {
    const user = userEvent.setup();
    const filed = { ...topic, sourceIds: ["s1"] };
    vi.spyOn(knowledgeApi, "topicOverview").mockRejectedValue(new Error("This topic has no overview yet"));
    const write = vi.spyOn(knowledgeApi, "writeTopicOverview").mockResolvedValue({
      topicId: "topic_1", markdown: "# Genomics\n\n1 paper.\n\n## Papers\n\nGenome quality needs controls. [1]\n",
      citations: [{ number: 1, sourceId: "s1", sourceTitle: "Quality paper", blockId: "b1", revisionId: "r1", quote: "Genome quality needs controls." }],
      method: "local", sourceCount: 1, createdAt: "2026-09-28T10:00:00",
    });
    const open = vi.fn();
    render(<TopicManager baseId="biology" topics={[filed]} onChange={vi.fn()} onAsk={vi.fn()} onOpenSource={open} />);
    expect(screen.getByText("1 paper")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Overview" }));
    await user.click(await screen.findByRole("button", { name: "Write overview" }));
    expect(write).toHaveBeenCalledWith("biology", "topic_1");
    expect(await screen.findByText("Genome quality needs controls. [1]")).toBeVisible();
    expect(screen.getByText(/From the papers’ own abstracts/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Quality paper" }));
    expect(open).toHaveBeenCalledWith("s1");
    expect(screen.getByRole("button", { name: "Rewrite overview" })).toBeEnabled();
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
