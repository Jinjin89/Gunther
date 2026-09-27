import { beforeEach, describe, expect, it } from "vitest";
import {
  LOCAL_CAPTURE_QUARANTINE_KEY,
  LOCAL_CAPTURE_QUEUE_KEY,
  appendStoredCapture,
  loadStoredCaptures,
  removeStoredCapture,
} from "./localCaptureQueue";

describe("local capture queue", () => {
  beforeEach(() => window.localStorage.clear());

  it("preserves legacy captures but makes their missing workspace binding explicit", () => {
    window.localStorage.setItem(LOCAL_CAPTURE_QUEUE_KEY, JSON.stringify([{
      id: "legacy-1",
      title: "Older offline thought",
      content: "Keep me",
      kind: "note",
      baseId: null,
    }]));

    expect(loadStoredCaptures()).toEqual([expect.objectContaining({
      id: "legacy-1",
      workspaceId: null,
    })]);
  });

  it("quarantines malformed elements without poisoning valid removal", () => {
    window.localStorage.setItem(LOCAL_CAPTURE_QUEUE_KEY, JSON.stringify([
      {
        id: "valid-1",
        title: "Bound capture",
        content: "Safe",
        kind: "note",
        baseId: null,
        workspaceId: "wsp_primary",
      },
      null,
      { id: "missing-fields" },
    ]));

    expect(loadStoredCaptures()).toHaveLength(1);
    expect(window.localStorage.getItem(LOCAL_CAPTURE_QUARANTINE_KEY)).toContain("invalid-queue-elements");

    removeStoredCapture("valid-1");
    expect(loadStoredCaptures()).toEqual([]);
  });

  it("appends an exact workspace binding to new durable captures", () => {
    appendStoredCapture({
      id: "capture-1",
      title: "Workspace-bound thought",
      content: "Never send this elsewhere",
      kind: "note",
      baseId: null,
      workspaceId: "wsp_primary",
    });

    expect(loadStoredCaptures()).toEqual([
      expect.objectContaining({ id: "capture-1", workspaceId: "wsp_primary" }),
    ]);
  });
});
