import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StartupError } from "./StartupError";

describe("StartupError", () => {
  it("says why the service did not start instead of guessing", () => {
    render(<StartupError reason="Gunther's knowledge service stopped (exit status: 1): port 28787 is already in use by another program" />);
    expect(screen.getByRole("alert")).toHaveTextContent("port 28787 is already in use by another program");
    expect(screen.queryByText(/Another app may be using/)).toBeNull();
    expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
  });
});
