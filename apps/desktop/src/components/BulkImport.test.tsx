import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { BulkImport } from "./BulkImport";

const api = vi.hoisted(() => ({ captureAsset: vi.fn() }));
vi.mock("../api", () => ({ knowledgeApi: api }));

describe("BulkImport", () => {
  beforeEach(() => { api.captureAsset.mockReset(); });

  it("imports the readable files into the library and reports what happened", async () => {
    api.captureAsset.mockImplementation(async (file: File) => {
      if (file.name === "broken.pdf") throw new Error("File upload failed with status 507");
      return { importResult: { duplicate: file.name === "again.pdf" } };
    });
    const updated = vi.fn();
    window.addEventListener("gunther:sources-updated", updated);
    render(<BulkImport knowledgeBaseId="lib" />);
    const files = ["a.pdf", "again.pdf", "broken.pdf", ".DS_Store"].map((name) => new File(["%PDF"], name, { type: "application/pdf" }));
    await userEvent.upload(screen.getByLabelText("Files to import"), files);
    expect(await screen.findByText(/Imported · 1 added · 1 already here · 1 failed · 1 skipped/)).toBeVisible();
    expect(api.captureAsset).toHaveBeenCalledWith(expect.any(File), "a", "paper", "lib", "");
    await userEvent.click(screen.getByText("Show failures"));
    expect(screen.getByText("broken.pdf")).toBeVisible();
    expect(updated).toHaveBeenCalled();
    window.removeEventListener("gunther:sources-updated", updated);
  });
});
