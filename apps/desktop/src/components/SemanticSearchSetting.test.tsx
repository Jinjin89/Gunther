import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SemanticSearchSetting } from "./SemanticSearchSetting";

const api = vi.hoisted(() => ({ retrievalStatus: vi.fn() }));
vi.mock("../api", () => ({ knowledgeApi: api }));

const status = { semanticConfigured: true, semanticOffReason: null, model: "e5-onnx:v1", warning: null, passages: 1200, embeddedBlocks: 1200, embeddingJobs: 0 };

describe("SemanticSearchSetting", () => {
  beforeEach(() => api.retrievalStatus.mockReset());

  it("shows that search by meaning is on", async () => {
    api.retrievalStatus.mockResolvedValue(status);
    render(<SemanticSearchSetting />);
    expect(await screen.findByText("On")).toBeInTheDocument();
    expect(screen.getByText(/Chinese and English.*1,200 passages indexed/)).toBeInTheDocument();
  });

  it("shows indexing progress", async () => {
    api.retrievalStatus.mockResolvedValue({ ...status, embeddedBlocks: 300, embeddingJobs: 4 });
    render(<SemanticSearchSetting />);
    expect(await screen.findByText("Indexing")).toBeInTheDocument();
    expect(screen.getByText(/Indexing 300 of 1,200 passages/)).toBeInTheDocument();
  });

  it("says why it is off", async () => {
    api.retrievalStatus.mockResolvedValue({ ...status, semanticConfigured: false, semanticOffReason: "The embedding model is not installed. Run npm run models:fetch." });
    render(<SemanticSearchSetting />);
    expect(await screen.findByText("Off")).toBeInTheDocument();
    expect(screen.getByText(/Keyword search only\. The embedding model is not installed/)).toBeInTheDocument();
  });
});
