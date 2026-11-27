import type { PlanPart } from "@gunther/contracts";
import { webcrypto } from "node:crypto";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { normalizeSavedOutline, OutlineEditor, partEdits } from "./OutlineEditor";
import { SlideStylePicker } from "./SlideStylePicker";
import { parseSlides, withTheme } from "./sections";
import { SlideFace } from "./SlideFace";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

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

  it("keeps saved slide IDs, layout, and notes when an inline edit removes a row", async () => {
    const rebuild = vi.fn();
    const outline: PlanPart[] = [
      { id: "cover", heading: "Pilot", goal: "Frame the question", layout: "title", notes: "" },
      { id: "definition", heading: "The measured quantity", goal: "Explain the measure", message: "The time measure excludes quality", layout: "text", notes: "" },
      { id: "result", heading: "A shorter wait", goal: "Interpret the result", message: "Quality remains unknown",
        question: "What does the result establish?", layout: "custom_saved", notes: "Read the measurement out loud." },
    ];
    function Editor() {
      const [value, setValue] = useState(outline);
      const [editing, edit] = useState(false);
      return <>
        <OutlineEditor value={value} onChange={setValue} unitLabel="slide" max={14} layouts={["title", "text", "takeaways"]}
          disabled={false} readOnly={!editing} />
        {editing ? <button type="button" onClick={() => rebuild(partEdits(value))}>Rebuild with this outline</button>
          : <button type="button" onClick={() => edit(true)}>Edit outline</button>}
      </>;
    }
    render(<Editor />);
    const user = userEvent.setup();
    expect(screen.getByText("Quality remains unknown")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Edit outline" }));
    expect(screen.getByRole("combobox", { name: "Layout 3" })).toHaveValue("custom_saved");
    await user.click(screen.getByRole("button", { name: "Remove slide 2" }));
    expect(screen.getByRole("textbox", { name: "Heading 2" })).toHaveFocus();
    await user.click(screen.getByRole("button", { name: "Rebuild with this outline" }));
    expect(rebuild).toHaveBeenCalledWith([
      { id: "cover", heading: "Pilot", goal: "Frame the question", layout: "title", notes: "" },
      { id: "result", heading: "A shorter wait", goal: "Interpret the result", layout: "custom_saved", notes: "Read the measurement out loud." },
    ]);
  });

  it("normalizes saved rows without IDs by version and source position", async () => {
    vi.stubGlobal("crypto", webcrypto);
    const old = [{ heading: "Pilot", goal: "", layout: "title" }, { heading: "Result", goal: "" }];
    const first = await normalizeSavedOutline("version_a", old);
    expect(first).toEqual(await normalizeSavedOutline("version_a", old));
    expect(first[0]?.id).toMatch(/^p_[a-f0-9]{62}$/);
    expect(first[0]?.id).not.toEqual(first[1]?.id);
    expect(first[0]?.id).not.toEqual((await normalizeSavedOutline("version_b", old))[0]?.id);
    const projected = await normalizeSavedOutline("version_a", old, [
      { position: 2, itemId: "p2", heading: "Result", layout: "text", state: "ok", why: "" },
    ]);
    expect(projected[1]?.id).toEqual("p2");
  });
});
