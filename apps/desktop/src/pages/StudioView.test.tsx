import type { Artifact, ArtifactSummary, KnowledgeUnit } from "@gunther/contracts";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { knowledgeApi } from "../api";
import { makeBase } from "../test/fixtures";
import { StudioView } from "./KnowledgeBaseWorkspace";

const unit: KnowledgeUnit = {
  id: "unt_reviewed_1",
  knowledgeBaseId: "biology",
  title: "T-cell identity",
  kind: "knowledge_unit",
  status: "trusted",
  content: "CD3D supports a T-cell identity.",
  revisionCount: 3,
  sourceProposalId: "prp_1",
  sourceSessionId: "ses_1",
  sourceMessageId: "msg_1",
  targetChapterId: null,
  evidenceCount: 2,
  createdAt: "2026-08-30T00:00:00.000Z",
  updatedAt: "2026-08-30T00:00:00.000Z",
};

function artifact(versionNumber = 1, id = `art_${versionNumber}`): Artifact {
  return {
    id,
    workspaceId: "wsp_primary",
    knowledgeBaseId: "biology",
    lineageId: "arl_1",
    versionNumber,
    supersedesArtifactId: versionNumber > 1 ? `art_${versionNumber - 1}` : null,
    format: "field_guide",
    audience: "scientist",
    title: "Biology · Field guide",
    contentHash: `${versionNumber}`.repeat(64),
    manifestHash: "ab".repeat(32),
    acceptedUnitIds: [unit.id],
    unitCount: 1,
    createdAt: `2026-08-30T00:0${versionNumber}:00.000Z`,
    content: `# Saved artifact version ${versionNumber}`,
    unitSnapshots: [{
      unitId: unit.id,
      revisionId: `kur_pinned_${versionNumber}`,
      revisionNumber: versionNumber + 2,
      title: unit.title,
      content: `Pinned content version ${versionNumber}`,
      contentHash: "cd".repeat(32),
      sourceProposalId: unit.sourceProposalId,
      sourceSessionId: unit.sourceSessionId,
      sourceMessageId: `msg_pinned_${versionNumber}`,
      evidenceCount: 2,
    }],
    provenance: {
      schemaVersion: 1,
      generator: "gunther.local-template.v1",
      workspaceId: "wsp_primary",
      knowledgeBaseId: "biology",
      knowledgeBaseQuestion: "What is supported?",
      acceptedOnly: true,
      acceptedUnitIds: [unit.id],
      revisionIds: [`kur_pinned_${versionNumber}`],
    },
  };
}

function summary(value: Artifact): ArtifactSummary {
  return {
    id: value.id,
    workspaceId: value.workspaceId,
    knowledgeBaseId: value.knowledgeBaseId,
    lineageId: value.lineageId,
    versionNumber: value.versionNumber,
    supersedesArtifactId: value.supersedesArtifactId,
    format: value.format,
    audience: value.audience,
    title: value.title,
    contentHash: value.contentHash,
    manifestHash: value.manifestHash,
    acceptedUnitIds: value.acceptedUnitIds,
    unitCount: value.unitCount,
    createdAt: value.createdAt,
  };
}

function renderStudio(onNotify = vi.fn()) {
  render(
    <StudioView
      base={makeBase({ id: "biology", title: "Biology", question: "What is supported?" })}
      workspaceId="wsp_primary"
      onNotify={onNotify}
      onMode={vi.fn()}
    />,
  );
  return onNotify;
}

describe("persistent Output history", () => {
  beforeEach(() => window.localStorage.clear());

  it("reopens an immutable version with pinned revision and evidence provenance", async () => {
    const saved = artifact();
    vi.spyOn(knowledgeApi, "knowledgeUnits").mockResolvedValue([unit]);
    vi.spyOn(knowledgeApi, "artifacts").mockResolvedValue([summary(saved)]);
    vi.spyOn(knowledgeApi, "artifact").mockResolvedValue(saved);

    renderStudio();

    expect(await screen.findByText("Pinned content version 1")).toBeVisible();
    expect(screen.getByText(/Pinned revision 3/)).toHaveTextContent("2 evidence links");
    expect(screen.getByText(/Pinned revision 3/)).toHaveTextContent("kur_pinned_1");
    expect(screen.getByText(/Pinned revision 3/)).toHaveTextContent("msg_pinned_1");
    expect(screen.getByText(/manifest abab/)).toBeVisible();
    expect(knowledgeApi.artifact).toHaveBeenCalledWith("biology", saved.id, "wsp_primary");
  });

  it("creates version one, then appends a new version from the current lineage head", async () => {
    const user = userEvent.setup();
    const first = artifact(1);
    const second = artifact(2);
    vi.spyOn(knowledgeApi, "knowledgeUnits").mockResolvedValue([unit]);
    vi.spyOn(knowledgeApi, "artifacts")
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([summary(first)])
      .mockResolvedValueOnce([summary(second), summary(first)]);
    const create = vi.spyOn(knowledgeApi, "createArtifact")
      .mockResolvedValueOnce(first)
      .mockResolvedValueOnce(second);
    vi.spyOn(knowledgeApi, "artifact").mockResolvedValue(first);

    renderStudio();
    await user.click(await screen.findByRole("button", { name: /Build and save output/i }));

    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0]?.[0]).toBe("biology");
    expect(create.mock.calls[0]?.[1]).toEqual(expect.objectContaining({
      clientRequestId: expect.stringMatching(/^[A-Za-z0-9_-]{8,128}$/),
      acceptedUnitIds: [unit.id],
      format: "field_guide",
      audience: "scientist",
    }));
    expect(create.mock.calls[0]?.[2]).toBe("wsp_primary");

    await user.click(await screen.findByRole("button", { name: /Generate version 2/i }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(2));
    expect(create.mock.calls[1]?.[1]).toEqual(expect.objectContaining({
      supersedesArtifactId: first.id,
    }));
    expect(create.mock.calls[1]?.[1].clientRequestId)
      .not.toBe(create.mock.calls[0]?.[1].clientRequestId);
  });

  it("recovers from a stale lineage-head conflict by reopening the latest history", async () => {
    const user = userEvent.setup();
    const first = artifact(1);
    const second = artifact(2);
    const notify = vi.fn();
    vi.spyOn(knowledgeApi, "knowledgeUnits").mockResolvedValue([unit]);
    vi.spyOn(knowledgeApi, "artifacts")
      .mockResolvedValueOnce([summary(first)])
      .mockResolvedValueOnce([summary(second), summary(first)]);
    vi.spyOn(knowledgeApi, "artifact")
      .mockResolvedValueOnce(first)
      .mockResolvedValueOnce(second);
    vi.spyOn(knowledgeApi, "createArtifact").mockRejectedValue(
      new Error("supersedesArtifactId is not the current Artifact lineage head"),
    );

    renderStudio(notify);
    await user.click(await screen.findByRole("button", { name: /Generate version 2/i }));

    expect(await screen.findByText("Pinned content version 2")).toBeVisible();
    expect(notify).toHaveBeenCalledWith(
      "Output history changed in another request. Gunther refreshed the latest saved version.",
    );
  });

  it("reuses the idempotency key after a reload when the create response was uncertain", async () => {
    const user = userEvent.setup();
    const saved = artifact(1);
    vi.spyOn(knowledgeApi, "knowledgeUnits").mockResolvedValue([unit]);
    vi.spyOn(knowledgeApi, "artifacts")
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([summary(saved)]);
    const create = vi.spyOn(knowledgeApi, "createArtifact")
      .mockRejectedValueOnce(new Error("The local service connection closed before a response arrived."))
      .mockResolvedValueOnce(saved);

    const firstRender = render(
      <StudioView
        base={makeBase({ id: "biology", title: "Biology", question: "What is supported?" })}
        workspaceId="wsp_primary"
        onNotify={vi.fn()}
        onMode={vi.fn()}
      />,
    );
    await user.click(await screen.findByRole("button", { name: /Build and save output/i }));
    expect(await screen.findByRole("status")).toHaveTextContent(/connection closed/i);
    const firstRequestId = create.mock.calls[0]?.[1].clientRequestId;
    firstRender.unmount();

    renderStudio();
    await user.click(await screen.findByRole("button", { name: /Build and save output/i }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(2));
    expect(create.mock.calls[1]?.[1].clientRequestId).toBe(firstRequestId);
    expect(await screen.findByText("Pinned content version 1")).toBeVisible();
    expect(window.localStorage.getItem("gunther:artifact-attempt:wsp_primary:biology")).toBeNull();
  });
});
