import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DictationStatus } from "./Dictation";

describe("DictationStatus", () => {
  it("says what voice input is doing, and nothing when it is off", () => {
    const { rerender, container } = render(<DictationStatus state="idle" level={0} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<DictationStatus state="starting" level={0} />);
    expect(screen.getByRole("status")).toHaveTextContent("Starting the microphone");
    rerender(<DictationStatus state="listening" level={0.5} />);
    expect(screen.getByRole("status")).toHaveTextContent("Listening — speak now");
    rerender(<DictationStatus state="finishing" level={0.5} />);
    expect(screen.getByRole("status")).toHaveTextContent("Finishing");
  });
});
