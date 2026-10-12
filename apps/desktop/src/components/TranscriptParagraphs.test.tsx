import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { applyGrouping, applyPiece, confirmLive, emptyTranscript, groupingRequest, type TranscriptState } from "../services/liveTranscript";
import { TranscriptParagraphs } from "./TranscriptParagraphs";

describe("grouping failure on screen", () => {
  it("shows a small Retry under the newest paragraph and sends the same sentences again", () => {
    let state: TranscriptState = { ...emptyTranscript(), grouping: { state: "idle" } };
    state = confirmLive(applyPiece(state, {
      id: "a", start_ms: 0, end_ms: 2_000, text: "One. Two.", final: true,
      sentences: [{ start_ms: 0, end_ms: 1_000, text: "One." }, { start_ms: 1_000, end_ms: 2_000, text: "Two." }],
    }), { closing: true });
    state = applyGrouping(state, groupingRequest(state)!, { error: "down" });
    const retry = vi.fn();
    render(<TranscriptParagraphs state={state} active onEdit={() => undefined} onRetryGrouping={retry} />);
    expect(screen.getByText(/Couldn't group into paragraphs/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(retry).toHaveBeenCalledTimes(1);
  });
});
