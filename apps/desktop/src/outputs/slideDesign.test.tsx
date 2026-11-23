import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OutlineEditor } from "./OutlineEditor";
import { SlideStylePicker } from "./SlideStylePicker";
import { parseSlides, withTheme } from "./sections";
import { SlideFace } from "./SlideFace";

afterEach(cleanup);

describe("slide planning and design persistence", () => {
  it("keeps old slides on the legacy recipe and carries the chosen recipe with the content", () => {
    const old = "<!-- layout: title; theme: editorial -->\n# A pilot\n\nA qualified observation [1].";
    expect(parseSlides(old)[0]?.designVersion).toBeUndefined();
    const chosen = withTheme(old, "editorial");
    expect(parseSlides(chosen)[0]?.designVersion).toBe(1);
    expect(parseSlides(chosen)[0]?.body).toBe(parseSlides(old)[0]?.body);
    const view = render(<SlideFace body="# A pilot\n\nA qualified observation [1]." title layout="title" theme="editorial" numbers={[1]}/>);
    expect(view.container.querySelector(".outputs-slide-face")?.getAttribute("data-design-version")).toBe("0");
  });

  it("shows the real cover and evidence with inert citations in every style choice", async () => {
    const select = vi.fn();
    const view = render(<SlideStylePicker value="classic" accent={null} disabled={false} designVersion={1} onSelect={select} numbers={[1]} samples={[
      { body: "# A pilot\n\nA qualified observation [1].", title: true, layout: "title" },
      { body: "## What remains unknown\n\n- Quality was not measured [1]", title: false, layout: "text" },
    ]}/>);
    await userEvent.click(screen.getByRole("button", { name: "Slide style" }));
    expect(document.querySelectorAll(".outputs-theme-real-samples .outputs-slide-face")).toHaveLength(8);
    expect(document.querySelectorAll(".outputs-theme-real-samples[inert]")).toHaveLength(4);
    expect(document.querySelectorAll('.outputs-theme-real-samples [data-design-version="0"]')).toHaveLength(0);
    expect(document.querySelectorAll(".outputs-theme-real-samples sup")).toHaveLength(8);
    await userEvent.click(screen.getByRole("button", { name: /Editorial/ }));
    expect(select).toHaveBeenCalledWith("editorial");
    expect(view.container.querySelector(".outputs-theme-picker")).toBeNull();
  });

  it("keeps saved slide identities when rebuilding after removing an old prerequisite", async () => {
    const rebuild = vi.fn();
    const outline = [
      { id: "cover", heading: "Pilot", goal: "Frame the question", layout: "title" },
      { id: "definition", heading: "The measured quantity", goal: "Explain the measure", message: "The time measure excludes quality", layout: "text" },
      { id: "result", heading: "A shorter wait", goal: "Interpret the result", message: "Quality remains unknown", question: "What does the result establish?", requires: ["definition"], layout: "takeaways" },
    ];
    render(<OutlineEditor outline={outline} label="slides" max={14} busy={false} onRebuild={rebuild}/>);
    const user = userEvent.setup();
    expect(screen.getByText("Quality remains unknown")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Edit outline" }));
    await user.click(screen.getByRole("button", { name: "Remove 2" }));
    await user.click(screen.getByRole("button", { name: "Rebuild with this outline" }));
    expect(rebuild).toHaveBeenCalledWith([
      { id: "cover", heading: "Pilot", goal: "Frame the question" },
      { id: "result", heading: "A shorter wait", goal: "Interpret the result" },
    ]);
  });
});
