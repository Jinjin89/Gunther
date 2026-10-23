import { afterEach, describe, expect, it, vi } from "vitest";
import { knowledgeApi } from "../api";

describe("Usage API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sends the range and offset, unwraps recent requests, and deletes history with a 204 response", async () => {
    const recent = [{ id: "request", input: null, cached: null, output: null, reasoning: null }];
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ totals: { requests: 1 }, byDay: [], byModel: [], byJob: [] })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ requests: recent })))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    await knowledgeApi.usageSummary("30d", -420);
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/usage/summary?range=30d&tzOffset=-420");
    expect(await knowledgeApi.usageRecent()).toEqual(recent);
    expect(fetchMock.mock.calls[1]?.[0]).toBe("/api/usage/recent?limit=50");
    await expect(knowledgeApi.clearUsage()).resolves.toBeUndefined();
    expect(fetchMock.mock.calls[2]?.[0]).toBe("/api/usage");
    expect(fetchMock.mock.calls[2]?.[1]).toMatchObject({ method: "DELETE" });
  });
});
