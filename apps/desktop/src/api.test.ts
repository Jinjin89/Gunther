import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { knowledgeApi, recordingAssetUrl, recordingSocketUrl, sourceAssetUrl } from "./api";

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

    await knowledgeApi.inbox({ state: "needs_review", itemType: "knowledge_suggestion" });

    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/inbox?state=needs_review&itemType=knowledge_suggestion");
    expect(url).not.toContain("token=");
    expect(init.method).toBeUndefined();
    expect(new Headers(init.headers).get("Content-Type")).toBe("application/json");
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

  it("keeps artifact history routes inside both workspace and knowledge-base boundaries", async () => {
    fetchMock
      .mockResolvedValueOnce(response([]))
      .mockResolvedValueOnce(response({ id: "art_1" }, 201))
      .mockResolvedValueOnce(response({ id: "art_1" }));

    await knowledgeApi.artifacts("biology/team", "wsp_primary");
    await knowledgeApi.createArtifact("biology/team", {
      clientRequestId: "artifact_request_1",
      format: "field_guide",
      audience: "scientist",
      acceptedUnitIds: ["unt_1"],
    }, "wsp_primary");
    await knowledgeApi.artifact("biology/team", "art/1", "wsp_primary");

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/knowledge-bases/biology%2Fteam/artifacts",
    );
    const createInit = fetchMock.mock.calls[1]?.[1] as RequestInit;
    expect(createInit.method).toBe("POST");
    expect(new Headers(createInit.headers).get("X-Gunther-Workspace-Id")).toBe("wsp_primary");
    expect(JSON.parse(createInit.body as string)).toEqual({
      clientRequestId: "artifact_request_1",
      format: "field_guide",
      audience: "scientist",
      acceptedUnitIds: ["unt_1"],
    });
    expect(fetchMock.mock.calls[2]?.[0]).toBe(
      "/api/knowledge-bases/biology%2Fteam/artifacts/art%2F1",
    );
    expect(new Headers((fetchMock.mock.calls[2]?.[1] as RequestInit).headers)
      .get("X-Gunther-Workspace-Id")).toBe("wsp_primary");
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
