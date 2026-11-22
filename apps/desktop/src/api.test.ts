import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AnswerStoppedError, AnswerStreamError, ApiRequestError, knowledgeApi, OutputStoppedError, recordingAssetUrl, recordingSocketUrl, ServiceUnavailableError, sourceAssetUrl } from "./api";
import { ACTIVITY_CHANGED_EVENT } from "./activity/events";
import { RunStreamError } from "./sse";

const response = (data: unknown, status = 200): Response => ({
  ok: status >= 200 && status < 300,
  status,
  json: vi.fn().mockResolvedValue(data),
} as unknown as Response);

const events = (...blocks: Array<[string, unknown]>) => new ReadableStream<Uint8Array>({
  start(controller) {
    const encoder = new TextEncoder();
    for (const [name, data] of blocks) controller.enqueue(encoder.encode(`event: ${name}\ndata: ${JSON.stringify(data)}\n\n`));
    controller.close();
  },
});
const stream = (body: ReadableStream<Uint8Array>) => new Response(body);

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

  it("can save sources before model analysis and cancel optional transcript requests", async () => {
    const capture = { title: "Recording", content: "Saved transcript", kind: "recording" as const };
    await knowledgeApi.importSource(capture, "wsp-primary", { deferAnalysis: true });
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/sources?deferAnalysis=true");
    expect(new Headers(fetchMock.mock.calls[0]?.[1].headers).get("X-Gunther-Workspace-Id")).toBe("wsp-primary");

    const controller = new AbortController();
    await knowledgeApi.relistenRecording("recording", {
      startMs: 0, endMs: 60_000, pass: "minute", context: { terms: [], before: "" },
    }, controller.signal);
    await knowledgeApi.groupParagraphs("recording", { sentences: ["Saved transcript"], before: "", pass: "live" }, controller.signal);
    expect(fetchMock.mock.calls[1]?.[1].signal).toBe(controller.signal);
    expect(fetchMock.mock.calls[2]?.[1].signal).toBe(controller.signal);
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
    const receipt = {
      asset: { id: "ast_file" },
      importResult: { source: { id: "src_file", processing: { state: "queued" } }, duplicate: false },
    };
    fetchMock.mockResolvedValueOnce(response(receipt, 201));
    const file = new File(["preserved bytes"], "paper.txt", { type: "text/plain" });

    const saved = await knowledgeApi.captureAsset(
      file,
      "Primary paper",
      "file",
      null,
      "Read the methods",
      "wsp_primary",
    );

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/captures/assets?");
    expect(new URL(url, "http://localhost").searchParams.has("deferProcessing")).toBe(false);
    expect(new Headers(init.headers).get("X-Gunther-Workspace-Id")).toBe("wsp_primary");
    expect(init.body).toBe(file);
    expect(saved).toEqual(receipt);
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

  it("uses the installed connection and token with the requested workspace", async () => {
    const install = vi.fn().mockResolvedValue({ baseUrl: "http://127.0.0.1:28789", authToken: "desktop-token" });
    vi.stubGlobal("__TAURI_INTERNALS__", { invoke: install });
    vi.resetModules();
    const { knowledgeApi: installedApi } = await import("./api");
    fetchMock.mockResolvedValueOnce(response({ status: "ok", extractionMode: "local" }));
    fetchMock.mockResolvedValueOnce(response([]));
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));

    await installedApi.artifacts("biology", "wsp_primary");
    await installedApi.followOutputBuild("biology", "wsp_primary", () => undefined);

    expect(install).toHaveBeenCalledOnce();
    expect(install.mock.calls[0]?.[0]).toBe("backend_connection");
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "http://127.0.0.1:28789/api/health",
      "http://127.0.0.1:28789/api/knowledge-bases/biology/artifacts",
      "http://127.0.0.1:28789/api/knowledge-bases/biology/outputs/build/stream",
    ]);
    for (const [, init] of fetchMock.mock.calls.slice(1)) {
      const headers = new Headers(init.headers);
      expect(headers.get("X-Gunther-Workspace-Id")).toBe("wsp_primary");
      expect(headers.get("X-Gunther-Token")).toBe("desktop-token");
    }
  });

  describe("an output being built", () => {
    const request = { clientRequestId: "review_request_1" };

    it("hands over each event, then the saved version, to a workspace-bound request", async () => {
      fetchMock.mockResolvedValueOnce(stream(events(
        ["outline", { type: "outline", title: "Markers", sections: [{ heading: "T cells", goal: "" }] }],
        ["text", { type: "text", section: 0, text: "Hello" }],
        ["done", { id: "art_1", title: "Markers" }],
      )));
      const heard: unknown[] = [];

      const saved = await knowledgeApi.reviewSlidesStream("biology/team", "deck/1", request, "wsp_primary", (event) => heard.push(event));

      expect(saved).toEqual({ id: "art_1", title: "Markers" });
      expect(heard).toEqual([
        { type: "outline", title: "Markers", sections: [{ heading: "T cells", goal: "" }] },
        { type: "text", section: 0, text: "Hello" },
      ]);
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
      expect(url).toBe("/api/knowledge-bases/biology%2Fteam/artifacts/deck%2F1/visual-review/stream");
      expect(init.method).toBe("POST");
      expect(JSON.parse(init.body as string)).toEqual(request);
      expect(new Headers(init.headers).get("X-Gunther-Workspace-Id")).toBe("wsp_primary");
    });

    it("accepts a saved version when the terminal CRLF crosses chunks", async () => {
      fetchMock.mockResolvedValueOnce(stream(new ReadableStream({
        start(controller) {
          const encoder = new TextEncoder();
          controller.enqueue(encoder.encode('event: done\r\ndata: {"id":"art_1"}\r\n\r'));
          controller.enqueue(encoder.encode("\n"));
          controller.close();
        },
      })));

      await expect(knowledgeApi.reviewSlidesStream("biology", "deck", request, "wsp_primary", () => undefined))
        .resolves.toEqual({ id: "art_1" });
    });

    it("sends a message to the Artifacts agent and hands over its events, then how the turn ended", async () => {
      fetchMock.mockResolvedValueOnce(stream(events(
        ["turn", { type: "turn", conversationId: "talk", messageId: "acm_1" }],
        ["action", { type: "action", id: "a1", state: "done", label: "Wrote slide 2", note: "" }],
        ["done", { outcome: "replied", reply: "Done.", question: null, artifact: null, changes: [], message: { id: "acm_2" } }],
      )));
      const heard: unknown[] = [];

      const ended = await knowledgeApi.artifactTurnStream("biology/team", "talk/1", { clientRequestId: "turn_request_1", text: "Shorten 2", focus: { section: 2, selected: false } }, "wsp_primary", (event) => heard.push(event));

      expect(ended.outcome).toBe("replied");
      expect(heard).toEqual([
        { type: "turn", conversationId: "talk", messageId: "acm_1" },
        { type: "action", id: "a1", state: "done", label: "Wrote slide 2", note: "" },
      ]);
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
      expect(url).toBe("/api/knowledge-bases/biology%2Fteam/artifact-conversations/talk%2F1/messages/stream");
      expect(init.method).toBe("POST");
      expect(new Headers(init.headers).get("X-Gunther-Workspace-Id")).toBe("wsp_primary");

      fetchMock.mockResolvedValueOnce(stream(events(["stopped", { type: "stopped" }])));
      await expect(knowledgeApi.artifactTurnStream("biology", "talk", { clientRequestId: "turn_request_2", text: "Go" }, "wsp_primary", () => undefined)).rejects.toBeInstanceOf(OutputStoppedError);
      fetchMock.mockResolvedValueOnce(stream(events(["error", { status: 502, detail: "The model is down." }])));
      await expect(knowledgeApi.artifactTurnStream("biology", "talk", { clientRequestId: "turn_request_3", text: "Go" }, "wsp_primary", () => undefined)).rejects.toThrow("The model is down.");
      fetchMock.mockResolvedValueOnce(stream(events(["error", { status: 502 }])));
      await expect(knowledgeApi.artifactTurnStream("biology", "talk", { clientRequestId: "turn_request_4", text: "Go" }, "wsp_primary", () => undefined))
        .rejects.toMatchObject({ message: "The message could not be handled.", status: 502 });
    });

    it("says why nothing was saved, and tells a stop from a failure", async () => {
      fetchMock.mockResolvedValueOnce(stream(events(["error", { status: 502, detail: "DeepSeek did not accept the API key." }])));
      await expect(knowledgeApi.reviewSlidesStream("biology", "deck", request, "wsp_primary", () => undefined))
        .rejects.toMatchObject({ name: RunStreamError.name, message: "DeepSeek did not accept the API key.", status: 502 });

      fetchMock.mockResolvedValueOnce(stream(events(["stopped", { type: "stopped" }])));
      await expect(knowledgeApi.reviewSlidesStream("biology", "deck", request, "wsp_primary", () => undefined)).rejects.toBeInstanceOf(OutputStoppedError);

      fetchMock.mockResolvedValueOnce(stream(events(["text", { type: "text", section: 0, text: "Half" }])));
      await expect(knowledgeApi.reviewSlidesStream("biology", "deck", request, "wsp_primary", () => undefined)).rejects.toThrow("The connection closed before the output was finished.");

      fetchMock.mockResolvedValueOnce({ ok: false, status: 409, body: null, json: vi.fn().mockResolvedValue({ detail: "Gunther is still building an output here." }) } as unknown as Response);
      await expect(knowledgeApi.reviewSlidesStream("biology", "deck", request, "wsp_primary", () => undefined))
        .rejects.toMatchObject({ message: "Gunther is still building an output here.", status: 409 });
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

  it("keeps Ask progress, the completed turn and Start's activity notification", async () => {
    const progress = { type: "text", text: "Line\u2028separator\u2029next" };
    const turn = { userMessage: { id: "msg_1" }, assistantMessage: { id: "msg_2", content: progress.text } };
    const heard = vi.fn();
    const activity = vi.fn();
    window.addEventListener(ACTIVITY_CHANGED_EVENT, activity);
    try {
      fetchMock.mockResolvedValueOnce(stream(events(["text", progress], ["done", turn])));
      await expect(knowledgeApi.sendMessageStream("session/1", { content: "What changed?" }, heard)).resolves.toEqual(turn);
      expect(heard.mock.calls).toEqual([[progress]]);
      expect(activity).toHaveBeenCalledOnce();

      const replay = { type: "resumed", question: "What changed?", startedAt: 1 };
      fetchMock.mockResolvedValueOnce(stream(events(["resumed", replay], ["done", turn])));
      await expect(knowledgeApi.followAnswer("session/1", heard)).resolves.toEqual(turn);
      expect(heard.mock.calls).toEqual([[progress], [replay]]);
      expect(activity).toHaveBeenCalledOnce();
    } finally {
      window.removeEventListener(ACTIVITY_CHANGED_EVENT, activity);
    }
  });

  it("keeps Ask's saved question ID and distinguishes Stop from failure on replay", async () => {
    const follow = () => knowledgeApi.followAnswer("session", vi.fn());
    fetchMock.mockResolvedValueOnce(stream(events(["error", { status: 502, detail: "The model is down.", questionId: "msg_1" }])));
    await expect(follow()).rejects.toMatchObject({
      name: AnswerStreamError.name, status: 502, message: "The model is down.", questionId: "msg_1",
    });
    fetchMock.mockResolvedValueOnce(stream(events(["stopped", { type: "stopped" }])));
    await expect(follow()).rejects.toBeInstanceOf(AnswerStoppedError);
    fetchMock.mockResolvedValueOnce(stream(events(["text", { type: "text", text: "Partial" }])));
    await expect(follow()).rejects.toThrow("The connection closed before the answer was finished.");
  });

  it("keeps a no-content follow distinct from a server failure or a bodyless start", async () => {
    const heard = vi.fn();
    const empty = new Response(null, { status: 204 });
    const json = vi.spyOn(empty, "json");
    fetchMock.mockResolvedValueOnce(empty).mockResolvedValueOnce(empty);
    await expect(knowledgeApi.stopAnswer("session")).resolves.toBeUndefined();
    await expect(knowledgeApi.followAnswer("session", heard)).resolves.toBeNull();
    expect(json).not.toHaveBeenCalled();

    fetchMock.mockResolvedValueOnce(new Response(null, { status: 200 }));
    await expect(knowledgeApi.followAnswer("session", heard)).resolves.toBeNull();
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 200 }));
    await expect(knowledgeApi.sendMessageStream("session", { content: "Ask" }, heard))
      .rejects.toMatchObject({ status: 200 });
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 503 }));
    await expect(knowledgeApi.followAnswer("session", heard)).rejects.toMatchObject({ status: 503 });
    expect(heard).not.toHaveBeenCalled();
  });

  it("preserves the abort reason when stopping a follow listener", async () => {
    const controller = new AbortController();
    const reason = new TypeError("Left the conversation");
    fetchMock.mockImplementationOnce((_url, init: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init.signal?.addEventListener("abort", () => reject(init.signal?.reason), { once: true });
    }));
    const pending = knowledgeApi.followAnswer("session", vi.fn(), controller.signal);
    await vi.waitFor(() => expect(fetchMock.mock.calls[0]?.[1].signal).toBe(controller.signal));
    controller.abort(reason);
    await expect(pending).rejects.toBe(reason);
  });

  it("preserves backend error detail and provides a status fallback", async () => {
    fetchMock.mockResolvedValueOnce(response({ detail: "Knowledge base no longer exists" }, 404));
    await expect(knowledgeApi.knowledgeBases()).rejects.toEqual(new ApiRequestError("Knowledge base no longer exists", 404));

    fetchMock.mockResolvedValueOnce(response({}, 503));
    await expect(knowledgeApi.sources()).rejects.toMatchObject({ status: 503, message: "Request failed with status 503" });

    fetchMock.mockResolvedValueOnce(response({ detail: [{ msg: "Field required" }] }, 422));
    await expect(knowledgeApi.speechPart("job", 0)).rejects.toMatchObject({ status: 422, message: "Request failed with status 422" });
  });

  it("starts readings and fetches their parts, scripts, and kept audio", async () => {
    const job = { clip: null, jobId: "job/one", parts: 2 };
    const audio = () => new Response("audio", { headers: { "Content-Type": "audio/wav" } });
    fetchMock.mockResolvedValueOnce(response(job))
      .mockResolvedValueOnce(audio())
      .mockResolvedValueOnce(response({ script: "Job script" }))
      .mockResolvedValueOnce(response({ script: "Kept script" }))
      .mockResolvedValueOnce(audio());
    await expect(knowledgeApi.beginSpeech("ses/one", "msg/one", true)).resolves.toEqual(job);
    await expect(knowledgeApi.speechPart("job/one", 0)).resolves.toMatchObject({ type: "audio/wav" });
    await expect(knowledgeApi.speechScript({ jobId: "job/one" })).resolves.toEqual({ script: "Job script" });
    await expect(knowledgeApi.speechScript({ clipId: "clip/one" })).resolves.toEqual({ script: "Kept script" });
    await expect(knowledgeApi.speechAudio("clip/one")).resolves.toMatchObject({ type: "audio/wav" });
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/sessions/ses%2Fone/messages/msg%2Fone/speech/begin?fresh=true",
      "/api/speech/jobs/job%2Fone/parts/0",
      "/api/speech/jobs/job%2Fone/script",
      "/api/speech/clips/clip%2Fone/script",
      "/api/speech/clips/clip%2Fone/audio",
    ]);
    expect(fetchMock.mock.calls[0]?.[1].method).toBe("POST");
  });

  it("builds encoded web asset and live recording routes without touching port 8787", () => {
    expect(recordingSocketUrl("lecture & lab")).toBe("ws://localhost:3000/api/recordings/live?context=lecture%20%26%20lab&protocol=2&sample_rate=16000&offset_ms=0");
    expect(recordingSocketUrl("lab", "recording", { offsetMs: 61_230.4, recordingId: "rec_1" })).toBe("ws://localhost:3000/api/recordings/live?context=lab&protocol=2&sample_rate=16000&offset_ms=61230&recording_id=rec_1");
    expect(recordingSocketUrl("", "dictation")).toBe("ws://localhost:3000/api/recordings/live?context=&purpose=dictation&protocol=2&sample_rate=16000&offset_ms=0&stream=true");
    expect(recordingAssetUrl("rec/one")).toBe("/api/recordings/rec%2Fone");
    expect(sourceAssetUrl("asset/one")).toBe("/api/assets/asset%2Fone");
  });
});
