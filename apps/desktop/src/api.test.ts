import type { BuildOutputRequest } from "@gunther/contracts";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { knowledgeApi, OutputStoppedError, recordingAssetUrl, recordingSocketUrl, ServiceUnavailableError, sourceAssetUrl } from "./api";

const response = (data: unknown, status = 200): Response => ({
  ok: status >= 200 && status < 300,
  status,
  json: vi.fn().mockResolvedValue(data),
} as unknown as Response);

describe("desktop knowledge API contract", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue(response({}));
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("encodes Inbox filters and keeps credentials out of web URLs", async () => {
    fetchMock.mockResolvedValueOnce(response([]));

    await knowledgeApi.inbox({ state: "needs_review", itemType: "quick_note" });

    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/inbox?state=needs_review&itemType=quick_note");
    expect(url).not.toContain("token=");
    expect(init.method).toBeUndefined();
    expect(new Headers(init.headers).get("Content-Type")).toBe("application/json");
  });

  it("explains an unreachable service but keeps cancellations distinguishable", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await expect(knowledgeApi.inbox()).rejects.toBeInstanceOf(ServiceUnavailableError);

    const abort = new DOMException("The operation was aborted.", "AbortError");
    fetchMock.mockRejectedValueOnce(abort);
    await expect(knowledgeApi.inbox()).rejects.toBe(abort);
  });

  it("scopes knowledge search to encoded library identifiers", async () => {
    fetchMock.mockResolvedValue(response([]));

    await knowledgeApi.search("marker & genes");
    await knowledgeApi.search("marker", 40, ["biology", "lab/notes"]);

    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/search?q=marker%20%26%20genes&limit=20");
    expect(fetchMock.mock.calls[1]?.[0]).toBe("/api/search?q=marker&limit=40&knowledgeBaseId=biology&knowledgeBaseId=lab%2Fnotes");
  });

  it("encodes resource identifiers instead of allowing path injection", async () => {
    fetchMock.mockResolvedValue(response([]));

    await knowledgeApi.sources("biology/team?draft=true");
    await knowledgeApi.source("source/../../other");

    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/knowledge-bases/biology%2Fteam%3Fdraft%3Dtrue/sources");
    expect(fetchMock.mock.calls[1]?.[0]).toBe("/api/sources/source%2F..%2F..%2Fother");
  });

  it("sends filing and review decisions with their exact methods and JSON bodies", async () => {
    fetchMock.mockResolvedValue(response({}));

    await knowledgeApi.fileSource("source/1", "base-2");
    await knowledgeApi.updateSourceAssertionStatuses("source/1", {
      status: "verified",
      reason: "Reviewed against the original transcript",
    });

    const [fileUrl, fileInit] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(fileUrl).toBe("/api/sources/source%2F1/file");
    expect(fileInit.method).toBe("POST");
    expect(JSON.parse(fileInit.body as string)).toEqual({ knowledgeBaseId: "base-2" });

    const [reviewUrl, reviewInit] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(reviewUrl).toBe("/api/sources/source%2F1/assertions/status");
    expect(reviewInit.method).toBe("PATCH");
    expect(JSON.parse(reviewInit.body as string)).toEqual({
      status: "verified",
      reason: "Reviewed against the original transcript",
    });
  });

  it("validates and sends immutable web capture requests through the dedicated endpoint", async () => {
    fetchMock.mockResolvedValueOnce(response({
      asset: {},
      importResult: {},
      snapshot: {},
      idempotentReplay: false,
    }, 201));

    await knowledgeApi.captureWeb({
      url: "https://example.org/article",
      title: "Primary article",
      knowledgeBaseId: "biology",
      clientCaptureId: "capture_web_0001",
    }, "wsp_primary");

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/captures/web");
    expect(init.method).toBe("POST");
    expect(new Headers(init.headers).get("X-Gunther-Workspace-Id")).toBe("wsp_primary");
    expect(JSON.parse(init.body as string)).toEqual({
      url: "https://example.org/article",
      title: "Primary article",
      notes: "",
      knowledgeBaseId: "biology",
      clientCaptureId: "capture_web_0001",
    });
  });

  it("binds original file uploads to the workspace captured by the UI", async () => {
    fetchMock.mockResolvedValueOnce(response({ asset: {}, importResult: {} }, 201));
    const file = new File(["preserved bytes"], "paper.txt", { type: "text/plain" });

    await knowledgeApi.captureAsset(
      file,
      "Primary paper",
      "file",
      null,
      "Read the methods",
      "wsp_primary",
    );

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/captures/assets?");
    expect(new Headers(init.headers).get("X-Gunther-Workspace-Id")).toBe("wsp_primary");
    expect(init.body).toBe(file);
  });

  it("binds durable recording session creation to its originating workspace", async () => {
    fetchMock.mockResolvedValueOnce(response({ id: "rec_1" }, 201));

    await knowledgeApi.startRecordingSession(
      "Lecture recording",
      "audio/webm",
      "wsp_primary",
    );

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/recordings/sessions?");
    expect(init.method).toBe("POST");
    expect(new Headers(init.headers).get("X-Gunther-Workspace-Id")).toBe("wsp_primary");
  });

  it("reuses the verified durable checksum when retrying a recording chunk", async () => {
    fetchMock.mockResolvedValueOnce(response({ id: "rec_1", nextExpectedSequence: 4 }));
    const checksum = "ab".repeat(32);
    const chunk = new Blob(["durable chunk"], { type: "audio/webm" });

    await knowledgeApi.appendRecordingChunk("rec_1", chunk, 3, "wsp_primary", checksum);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/recordings/rec_1/chunks?sequence=3");
    expect(init.method).toBe("PUT");
    expect(init.body).toBe(chunk);
    expect(new Headers(init.headers).get("X-Chunk-SHA256")).toBe(checksum);
    expect(new Headers(init.headers).get("X-Gunther-Workspace-Id")).toBe("wsp_primary");
  });

  it("keeps output routes inside both workspace and library boundaries", async () => {
    fetchMock.mockResolvedValue(response({ id: "art_1" }));
    const workspace = (call: number) => new Headers((fetchMock.mock.calls[call]?.[1] as RequestInit).headers).get("X-Gunther-Workspace-Id");

    await knowledgeApi.artifacts("biology/team", "wsp_primary");
    await knowledgeApi.artifact("biology/team", "art/1", "wsp_primary");
    await knowledgeApi.editOutput("biology/team", "art/1", { clientRequestId: "edit_request_1", content: "# Typed" }, "wsp_primary");
    await knowledgeApi.checkOutput("biology/team", "art/1", "wsp_primary");
    await knowledgeApi.stopOutputBuild("biology/team", "wsp_primary");

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/knowledge-bases/biology%2Fteam/artifacts",
      "/api/knowledge-bases/biology%2Fteam/artifacts/art%2F1",
      "/api/knowledge-bases/biology%2Fteam/artifacts/art%2F1/edits",
      "/api/knowledge-bases/biology%2Fteam/artifacts/art%2F1/check",
      "/api/knowledge-bases/biology%2Fteam/outputs/build/stop",
    ]);
    for (const call of [0, 1, 2, 3, 4]) expect(workspace(call)).toBe("wsp_primary");
    const edit = fetchMock.mock.calls[2]?.[1] as RequestInit;
    expect(edit.method).toBe("POST");
    expect(JSON.parse(edit.body as string)).toEqual({ clientRequestId: "edit_request_1", content: "# Typed" });
  });

  describe("an output being built", () => {
    const events = (...blocks: Array<[string, unknown]>) => new ReadableStream<Uint8Array>({
      start(controller) {
        const encoder = new TextEncoder();
        for (const [name, data] of blocks) controller.enqueue(encoder.encode(`event: ${name}\ndata: ${JSON.stringify(data)}\n\n`));
        controller.close();
      },
    });
    const stream = (body: ReadableStream<Uint8Array>, status = 200) => ({ ok: status < 300, status, body, json: vi.fn().mockResolvedValue({}) }) as unknown as Response;
    const request: BuildOutputRequest = { clientRequestId: "build_request_1", kind: "report", audience: "scientist", scope: { mode: "library", sourceIds: [], unitIds: [], sessionIds: [] } };

    it("hands over each event, then the saved version, to a workspace-bound request", async () => {
      fetchMock.mockResolvedValueOnce(stream(events(
        ["outline", { type: "outline", title: "Markers", sections: [{ heading: "T cells", goal: "" }] }],
        ["text", { type: "text", section: 0, text: "Hello" }],
        ["done", { id: "art_1", title: "Markers" }],
      )));
      const heard: unknown[] = [];

      const saved = await knowledgeApi.buildOutputStream("biology/team", request, "wsp_primary", (event) => heard.push(event));

      expect(saved).toEqual({ id: "art_1", title: "Markers" });
      expect(heard).toEqual([
        { type: "outline", title: "Markers", sections: [{ heading: "T cells", goal: "" }] },
        { type: "text", section: 0, text: "Hello" },
      ]);
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
      expect(url).toBe("/api/knowledge-bases/biology%2Fteam/outputs/build/stream");
      expect(init.method).toBe("POST");
      expect(JSON.parse(init.body as string)).toEqual(request);
      expect(new Headers(init.headers).get("X-Gunther-Workspace-Id")).toBe("wsp_primary");
    });

    it("revises a version by its own address", async () => {
      fetchMock.mockResolvedValueOnce(stream(events(["done", { id: "art_2" }])));
      await knowledgeApi.reviseOutputStream("biology", "art/1", { clientRequestId: "revise_request_1", instruction: "Shorter", sectionIndex: 1 }, "wsp_primary", () => undefined);
      expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/knowledge-bases/biology/artifacts/art%2F1/revise/stream");
    });

    it("says why nothing was saved, and tells a stop from a failure", async () => {
      fetchMock.mockResolvedValueOnce(stream(events(["error", { status: 502, detail: "DeepSeek did not accept the API key." }])));
      await expect(knowledgeApi.buildOutputStream("biology", request, "wsp_primary", () => undefined)).rejects.toThrow("DeepSeek did not accept the API key.");

      fetchMock.mockResolvedValueOnce(stream(events(["stopped", { type: "stopped" }])));
      await expect(knowledgeApi.buildOutputStream("biology", request, "wsp_primary", () => undefined)).rejects.toBeInstanceOf(OutputStoppedError);

      fetchMock.mockResolvedValueOnce(stream(events(["text", { type: "text", section: 0, text: "Half" }])));
      await expect(knowledgeApi.buildOutputStream("biology", request, "wsp_primary", () => undefined)).rejects.toThrow("The connection closed before the output was finished.");

      fetchMock.mockResolvedValueOnce({ ok: false, status: 409, body: null, json: vi.fn().mockResolvedValue({ detail: "Gunther is still building an output here." }) } as unknown as Response);
      await expect(knowledgeApi.buildOutputStream("biology", request, "wsp_primary", () => undefined)).rejects.toThrow("Gunther is still building an output here.");
    });

    it("follows a build that is running, and gets nothing when none is", async () => {
      fetchMock.mockResolvedValueOnce({ ok: true, status: 204, body: null, json: vi.fn() } as unknown as Response);
      expect(await knowledgeApi.followOutputBuild("biology", "wsp_primary", () => undefined)).toBeNull();
      expect((fetchMock.mock.calls[0]?.[1] as RequestInit).method).toBe("GET");

      fetchMock.mockResolvedValueOnce(stream(events(["resumed", { type: "resumed", question: "Cover B cells", startedAt: 1 }], ["done", { id: "art_1" }])));
      const heard: unknown[] = [];
      expect(await knowledgeApi.followOutputBuild("biology", "wsp_primary", (event) => heard.push(event))).toEqual({ id: "art_1" });
      expect(heard).toEqual([{ type: "resumed", question: "Cover B cells", startedAt: 1 }]);
    });
  });

  it("preserves backend error detail and provides a status fallback", async () => {
    fetchMock.mockResolvedValueOnce(response({ detail: "Knowledge base no longer exists" }, 404));
    await expect(knowledgeApi.knowledgeBases()).rejects.toThrow("Knowledge base no longer exists");

    fetchMock.mockResolvedValueOnce(response({}, 503));
    await expect(knowledgeApi.sources()).rejects.toThrow("Request failed with status 503");
  });

  it("builds encoded web asset and live recording routes without touching port 8787", () => {
    expect(recordingSocketUrl("lecture & lab")).toBe("ws://localhost:3000/api/recordings/live?context=lecture%20%26%20lab");
    expect(recordingAssetUrl("rec/one")).toBe("/api/recordings/rec%2Fone");
    expect(sourceAssetUrl("asset/one")).toBe("/api/assets/asset%2Fone");
  });
});
