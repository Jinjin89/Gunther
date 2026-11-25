import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import { CoverageMeter, StepTimeline } from "./EvidencePrimitives";

it("shows search counts including zero and shows errors for method steps", () => {
  render(<StepTimeline steps={[
    { tool: "search_library", label: "Searched the library", query: "", found: 2, error: null },
    { tool: "search_web", label: "Searched the web", query: "", found: 0, error: null },
    { tool: "check", label: "Checked the method", query: "", found: 4, error: null },
    { tool: "revise", label: "Revised the answer", query: "", found: 0, error: null },
    { tool: "frame", label: "Framed the question", query: "", found: 3, error: null },
    { tool: "revise", label: "Revision failed", query: "", found: 1, error: "The revision timed out" },
  ]} />);

  const timeline = within(screen.getByRole("list", { name: "How this was answered" }));
  expect(timeline.getByText("2").closest("li")).toHaveTextContent("Searched the library");
  expect(timeline.getByText("0").closest("li")).toHaveTextContent("Searched the web");
  for (const label of ["Checked the method", "Revised the answer", "Framed the question"]) {
    expect(timeline.getByText(label).closest("li")?.textContent).toBe(label);
  }
  const error = timeline.getByText("The revision timed out");
  expect(error).toHaveClass("is-error");
  expect(error.closest("li")).toHaveTextContent("Revision failed");
  expect(timeline.queryByText("1")).not.toBeInTheDocument();
});

it("shows the verified check alongside nonfactual segments in a single summary line", () => {
  const { container } = render(<CoverageMeter summary="2 claims verified" segments={[
    { tone: "verified", count: 2 }, { tone: "neutral", count: 1 },
  ]} />);

  expect(screen.getByText("2 claims verified")).toBeVisible();
  expect(container.querySelector(".evidence-coverage-line .lucide-check")).toBeInTheDocument();
  expect(container.querySelector("details")).not.toBeInTheDocument();
});

it("opens the flagged claims and leaves unflagged claims out of the disclosure", async () => {
  const user = userEvent.setup();
  const { container } = render(<CoverageMeter summary="1 of 2 claims verified" segments={[
    { tone: "verified", count: 1 }, { tone: "warning", count: 1 },
  ]} claims={[
    { id: "verified", n: 1, text: "A supported finding", refs: [1], verdict: "supported", reason: "" },
    { id: "partial", n: 2, text: "A qualified finding", refs: [2], verdict: "partly", reason: "Only one condition was measured" },
  ]} />);

  const summary = screen.getByText("1 of 2 claims verified");
  const reason = screen.getByText("Only one condition was measured");
  expect(summary.closest("details")).not.toHaveAttribute("open");
  expect(reason).not.toBeVisible();
  expect(container.querySelector(".evidence-coverage-line .lucide-check")).not.toBeInTheDocument();

  await user.click(summary);
  expect(screen.getByText("Partly supported")).toBeVisible();
  expect(screen.getByText(/A qualified finding/)).toBeVisible();
  expect(reason).toBeVisible();
  expect(screen.queryByText("A supported finding")).not.toBeInTheDocument();
});
