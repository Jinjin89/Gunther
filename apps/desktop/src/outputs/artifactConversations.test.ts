import type { ArtifactSummary } from "@gunther/contracts";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { knowledgeApi } from "../api";
import { importDeviceConversations, libraryScope } from "./artifactConversations";

const key = "gunther:artifact-conversations:workspace:library";
const turn = { id: "request", instruction: "Review the evidence", status: "running" };
const old = { id: "previous", title: "Earlier reading", untitled: false, scope: libraryScope(), turns: [turn] };

beforeEach(() => window.localStorage.clear());
afterEach(() => vi.restoreAllMocks());

it("imports a device conversation once, pairing an interrupted request to the version it saved", async () => {
  window.localStorage.setItem(key, JSON.stringify({ version: 1, conversations: [old, { id: "blank", draft: "Half written", turns: [] }] }));
  const upload = vi.spyOn(knowledgeApi, "importArtifactConversations").mockResolvedValue([]);
  const version = { id: "art_1", clientRequestId: turn.id } as ArtifactSummary;

  await importDeviceConversations("library", "workspace", [version]);
  await importDeviceConversations("library", "workspace", [version]);
  expect(upload).toHaveBeenCalledExactlyOnceWith("library", { conversations: [{
    id: old.id, title: old.title, scope: old.scope,
    turns: [{ id: turn.id, instruction: turn.instruction, status: "saved", artifactId: version.id, error: null }],
  }] }, "workspace");
});

it("retries a pending device import after the service rejects it", async () => {
  const legacy = [{ ...old, turns: [{ ...turn, status: "interrupted" }] }];
  window.localStorage.setItem(key, JSON.stringify({ version: 2, imported: false, legacy }));
  const upload = vi.spyOn(knowledgeApi, "importArtifactConversations")
    .mockRejectedValueOnce(new Error("Try later")).mockResolvedValueOnce([]);
  await expect(importDeviceConversations("library", "workspace", [])).rejects.toThrow("Try later");
  await importDeviceConversations("library", "workspace", []);
  expect(upload).toHaveBeenCalledTimes(2);
  expect(JSON.parse(window.localStorage.getItem(key)!)).toMatchObject({ imported: true, legacy: [] });
});
