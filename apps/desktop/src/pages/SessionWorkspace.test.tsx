import type { KnowledgeProposal, KnowledgeUnit, SessionMessage } from "@gunther/contracts";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { knowledgeApi } from "../api";
import { Composer, ConversationMessage } from "./SessionWorkspace";
import { useSavedAnswers } from "./savedAnswers";

const renderComposer = (ready: boolean) => {
  const onChange = vi.fn();
  const onSend = vi.fn();
  render(
    <Composer
      value="What does this source support?"
      sending={false}
      sourceCount={0}
      chapterTitle={undefined}
      onChange={onChange}
      onSend={onSend}
      onStop={vi.fn()}
      onSources={vi.fn()}
      readOnly={false}
      ready={ready}
    />,
  );
  return { onChange, onSend };
};

describe("Session composer durability boundary", () => {
  it("does not accept a question until its durable session is ready", async () => {
    const user = userEvent.setup();
    const { onSend } = renderComposer(false);

    expect(screen.getByRole("textbox", { name: "Message Gunther" })).toBeDisabled();
    expect(screen.getByText("Preparing a durable conversation before accepting questions…")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Send message" }));
    expect(onSend).not.toHaveBeenCalled();
  });

  it("enables sending after the durable session is ready", async () => {
    const user = userEvent.setup();
    const { onSend } = renderComposer(true);

    expect(screen.getByRole("textbox", { name: "Message Gunther" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Send message" }));
    expect(onSend).toHaveBeenCalledOnce();
  });

  it("has a Web switch that reflects whether web search is set up", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const { rerender } = render(
      <Composer value="" sending={false} sourceCount={0} chapterTitle={undefined} onChange={vi.fn()} onSend={vi.fn()} onStop={vi.fn()} onSources={vi.fn()} readOnly={false} ready web={{ available: false, enabled: false, onChange }} />,
    );
    expect(screen.getByRole("button", { name: "Web" })).toBeDisabled();
    rerender(
      <Composer value="" sending={false} sourceCount={0} chapterTitle={undefined} onChange={vi.fn()} onSend={vi.fn()} onStop={vi.fn()} onSources={vi.fn()} readOnly={false} ready web={{ available: true, enabled: false, onChange }} />,
    );
    await user.click(screen.getByRole("button", { name: "Web" }));
    expect(onChange).toHaveBeenCalledWith(true);
  });
});


const answer: SessionMessage = {
  id: "msg_1",
  sessionId: "ses_1",
  role: "assistant",
  content: "CD3D marks T cells. [1]",
  citations: [{ id: "cit_1", sourceId: "src_1", sourceTitle: "Marker paper", assertionId: null, quote: "CD3D is a T-cell marker.", locator: "p. 2", status: "verified", confidence: 0.9, ref: 1 }],
  context: { sourcesConsidered: 1, assertionsConsidered: 1, verifiedAssertions: 1, retrievalMode: "all", responderMode: "model" },
  createdAt: "2026-10-01T08:00:00.000Z",
};

const proposal = (status: KnowledgeProposal["status"]): KnowledgeProposal => ({
  id: "prp_1",
  knowledgeBaseId: "biology",
  sessionId: "ses_1",
  messageId: "msg_1",
  targetChapterId: null,
  kind: "knowledge_unit",
  title: "T-cell identity",
  content: answer.content,
  status,
  decisionReason: null,
  knowledgeUnitId: "unt_1",
  sourceSessionTitle: "Markers",
  createdAt: "2026-10-01T08:01:00.000Z",
  updatedAt: "2026-10-01T08:01:00.000Z",
});

const unit = (status: KnowledgeUnit["status"]): KnowledgeUnit => ({
  id: "unt_1",
  knowledgeBaseId: "biology",
  title: "T-cell identity",
  kind: "knowledge_unit",
  status,
  content: answer.content,
  revisionCount: 1,
  sourceProposalId: "prp_1",
  sourceSessionId: "ses_1",
  sourceMessageId: "msg_1",
  targetChapterId: null,
  evidenceCount: 1,
  createdAt: "2026-10-01T08:01:00.000Z",
  updatedAt: "2026-10-01T08:01:00.000Z",
});

/** One answer wired the way the workspace wires it. */
function SavableAnswer({ onNotify, onUnits }: { onNotify: (message: string) => void; onUnits: (units: KnowledgeUnit[]) => void }) {
  const answers = useSavedAnswers("biology", onNotify, vi.fn());
  onUnits(answers.units);
  return <ConversationMessage
    message={answer}
    retryDisabled={false}
    onRetry={vi.fn()}
    onEdit={vi.fn()}
    onCite={vi.fn()}
    selected={false}
    promoting={answers.busyId === answer.id}
    promoted={answers.isSaved(answer.id)}
    branching={false}
    onSelect={vi.fn()}
    onCopy={vi.fn()}
    onPromote={() => void answers.save({ id: "ses_1", focusChapterId: null }, answer)}
    onRemove={() => void answers.remove(answer)}
    onBranch={vi.fn()}
  />;
}

describe("Saving an answer as knowledge", () => {
  afterEach(() => vi.restoreAllMocks());

  it("saves with one click, shows it saved, and takes it back out with Remove", async () => {
    const user = userEvent.setup();
    const onNotify = vi.fn();
    const units = vi.fn();
    vi.spyOn(knowledgeApi, "proposals").mockResolvedValue([]);
    const listUnits = vi.spyOn(knowledgeApi, "knowledgeUnits").mockResolvedValue([]);
    const create = vi.spyOn(knowledgeApi, "createProposal").mockResolvedValue(proposal("accepted"));
    const update = vi.spyOn(knowledgeApi, "updateProposal").mockResolvedValue(proposal("rejected"));
    render(<SavableAnswer onNotify={onNotify} onUnits={units} />);

    expect(screen.queryByRole("button", { name: "Remove" })).not.toBeInTheDocument();
    listUnits.mockResolvedValue([unit("trusted")]);
    await user.click(screen.getByRole("button", { name: "Save as knowledge" }));

    expect(await screen.findByRole("button", { name: "Saved as knowledge" })).toBeDisabled();
    expect(create).toHaveBeenCalledWith("ses_1", "msg_1", { targetChapterId: null });
    expect(onNotify).toHaveBeenLastCalledWith("Saved as knowledge.");
    await waitFor(() => expect(units).toHaveBeenLastCalledWith([expect.objectContaining({ id: "unt_1" })]));

    listUnits.mockResolvedValue([unit("deprecated")]);
    await user.click(screen.getByRole("button", { name: "Remove" }));

    expect(await screen.findByRole("button", { name: "Save as knowledge" })).toBeEnabled();
    expect(update).toHaveBeenCalledWith("prp_1", { status: "rejected", reason: "Removed from knowledge" });
    expect(onNotify).toHaveBeenLastCalledWith("Removed from knowledge.");
    expect(screen.queryByRole("button", { name: "Remove" })).not.toBeInTheDocument();
    // Only trusted units are listed, so the removed answer's unit is gone from the list.
    await waitFor(() => expect(units).toHaveBeenLastCalledWith([]));

    // Saving it again goes through the same single step.
    update.mockClear();
    create.mockResolvedValue(proposal("accepted"));
    await user.click(screen.getByRole("button", { name: "Save as knowledge" }));
    expect(await screen.findByRole("button", { name: "Saved as knowledge" })).toBeVisible();
    expect(create).toHaveBeenCalledTimes(2);
  });

  it("shows a saved answer as saved when the conversation opens", async () => {
    vi.spyOn(knowledgeApi, "proposals").mockResolvedValue([proposal("accepted")]);
    vi.spyOn(knowledgeApi, "knowledgeUnits").mockResolvedValue([unit("trusted")]);
    render(<SavableAnswer onNotify={vi.fn()} onUnits={vi.fn()} />);

    expect(await screen.findByRole("button", { name: "Saved as knowledge" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Remove" })).toBeEnabled();
  });

  it("treats an answer that was removed earlier as not saved", async () => {
    vi.spyOn(knowledgeApi, "proposals").mockResolvedValue([proposal("rejected")]);
    vi.spyOn(knowledgeApi, "knowledgeUnits").mockResolvedValue([unit("deprecated")]);
    render(<SavableAnswer onNotify={vi.fn()} onUnits={vi.fn()} />);

    await waitFor(() => expect(knowledgeApi.proposals).toHaveBeenCalled());
    expect(await screen.findByRole("button", { name: "Save as knowledge" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Remove" })).not.toBeInTheDocument();
  });

  it("says why it could not save, and leaves the answer unsaved", async () => {
    const user = userEvent.setup();
    const onError = vi.fn();
    vi.spyOn(knowledgeApi, "proposals").mockResolvedValue([]);
    vi.spyOn(knowledgeApi, "knowledgeUnits").mockResolvedValue([]);
    vi.spyOn(knowledgeApi, "createProposal").mockRejectedValue(new Error("The service is offline"));
    function Failing() {
      const answers = useSavedAnswers("biology", vi.fn(), onError);
      return <button onClick={() => void answers.save({ id: "ses_1", focusChapterId: null }, answer)}>{answers.isSaved("msg_1") ? "Saved" : "Not saved"}</button>;
    }
    render(<Failing />);

    await user.click(screen.getByRole("button", { name: "Not saved" }));

    await waitFor(() => expect(onError).toHaveBeenLastCalledWith("The service is offline"));
    expect(screen.getByRole("button", { name: "Not saved" })).toBeVisible();
  });

  it("does not try to save into a conversation that exists only on this screen", async () => {
    const user = userEvent.setup();
    const onNotify = vi.fn();
    vi.spyOn(knowledgeApi, "proposals").mockResolvedValue([]);
    vi.spyOn(knowledgeApi, "knowledgeUnits").mockResolvedValue([]);
    const create = vi.spyOn(knowledgeApi, "createProposal");
    function Local() {
      const answers = useSavedAnswers("biology", onNotify, vi.fn());
      return <button onClick={() => void answers.save({ id: "local-1", focusChapterId: null }, answer)}>Save</button>;
    }
    render(<Local />);

    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(onNotify).toHaveBeenCalledWith("Reconnect the knowledge service to save this answer.");
    expect(create).not.toHaveBeenCalled();
  });
});
