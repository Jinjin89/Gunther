import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { WebSnapshotCard } from "./WebSnapshotCard";

const snapshot = {
  originalUrl: "https://example.org/start",
  finalUrl: "https://example.org/articles/grounded?view=full",
  capturedAt: "2026-08-30T01:02:03Z",
  status: 200,
  contentType: "text/html; charset=utf-8",
  contentHash: "4a".repeat(32),
  assetId: "ast_snapshot_01",
};

describe("WebSnapshotCard", () => {
  it("shows redirect, response, hash, live page, and preserved snapshot provenance", () => {
    render(<WebSnapshotCard snapshot={snapshot} />);

    expect(screen.getByRole("region", { name: "Web snapshot provenance" })).toBeInTheDocument();
    expect(screen.getByText("HTTP 200 · text/html; charset=utf-8")).toBeInTheDocument();
    expect(screen.getByText(snapshot.contentHash)).toHaveAttribute("title", snapshot.contentHash);
    expect(screen.getByRole("link", { name: "Open current page" })).toHaveAttribute("href", snapshot.finalUrl);
    expect(screen.getByRole("link", { name: "Download preserved snapshot" })).toHaveAttribute("href", expect.stringContaining(snapshot.assetId));
    expect(screen.getByText("Resolved to")).toBeInTheDocument();
  });

  it.each([
    "file:///private/secret",
    "data:text/html,secret",
    "javascript:alert(1)",
    "https://user:secret@example.org/private",
  ])("does not expose unsafe final URL %s as an external link", (finalUrl) => {
    render(<WebSnapshotCard snapshot={{ ...snapshot, finalUrl }} />);

    expect(screen.queryByRole("link", { name: "Open current page" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Download preserved snapshot" })).toBeInTheDocument();
  });
});
