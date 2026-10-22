import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { type ComponentProps } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SlideFace } from "../SlideFace";
import { figureSize } from "./BigNumberSlide";
import { tight } from "./Rich";

const NUMBERS = [1, 2, 3, 4, 5, 6, 7, 8, 9];

type Props = Partial<ComponentProps<typeof SlideFace>>;
function draw(layout: string, body: string, props: Props = {}) {
  const view = render(<SlideFace body={body} title={false} layout={layout} numbers={NUMBERS} {...props} />);
  const face = view.container.querySelector<HTMLElement>(".outputs-slide-face")!;
  return { ...view, face, inside: within(face) };
}

/** What a node says, with the no-break space that holds a citation to its word taken for a space. */
const said = (node: Element | null | undefined) => (node?.textContent ?? "").replace(/\u00a0/g, " ");

/** Every number written on a slide is a number its text has: a figure is never made up (the sums of a chart are not drawn). */
function expectNoNumbersMadeUp(face: HTMLElement, body: string) {
  const written: string[] = [];
  const walker = document.createTreeWalker(face, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) written.push(...(node.textContent?.match(/\d+(?:[.,]\d+)*/g) ?? []));
  expect(written.length).toBeGreaterThan(0);
  for (const number of written) expect(body).toContain(number);
}

afterEach(cleanup);

describe("a title slide", () => {
  it("is the title as the heading, the message under it, and a short rule in the accent over both", () => {
    const { face, inside } = draw("title", "# Cool roofs [1]\n\nThree field trials", { accent: "blue", title: true });
    expect(inside.getByRole("heading", { level: 1 })).toHaveTextContent("Cool roofs");
    expect(face.querySelector(".outputs-s-rule")).not.toBeNull();
    expect(face.querySelector(".outputs-s-message")).toHaveTextContent("Three field trials");
    expect(face.style.getPropertyValue("--outputs-accent")).toBe("#2668b6");
  });

  it("does not need a message", () => {
    const { face } = draw("title", "# Just a title");
    expect(face.querySelector(".outputs-s-message")).toBeNull();
    expect(face).toHaveAttribute("data-drawn", "title");
  });
});

describe("a directed relationship diagram", () => {
  it("draws the cited edge as a directional connector and leaves one accessible source button", () => {
    const text = "## Materials determine the next step\n| From | Relationship | To |\n| --- | --- | --- |\n| Collect | complete [1] | Check |\n| Check | missing [2] | Request more |";
    const { face, inside } = draw("diagram", text, { onCite: vi.fn() });
    expect(face).toHaveAttribute("data-drawn", "diagram");
    expect(inside.getByLabelText("Directed relationship diagram")).toBeVisible();
    expect(inside.getAllByRole("button", { name: "Inspect citation 1" })).toHaveLength(1);
    expect(face.querySelectorAll(".outputs-s-diagram-node")).toHaveLength(3);
    expect(face.querySelectorAll(".outputs-s-diagram-lines path[marker-end]")).toHaveLength(2);
    expect(face.querySelectorAll(".outputs-sr-only button")).toHaveLength(0);
    expect(inside.getByRole("list", { name: "Diagram relationships" })).toHaveTextContent("Collect — complete — Check");
  });
});

describe("a big number", () => {
  it("draws the figure large, with its caption, and the caption's citation opens the evidence", async () => {
    const onCite = vi.fn();
    const { face, inside } = draw("big_number", "## Labour is the biggest cost\n**62%**\nShare of the three-year cost [3]", { onCite });
    expect(face.querySelector(".outputs-s-bignum")).toHaveTextContent("62%");
    expect(face.querySelector(".outputs-s-caption")).toHaveTextContent("Share of the three-year cost [3]");
    expect(inside.getByRole("heading", { level: 2 })).toHaveTextContent("Labour is the biggest cost");
    await userEvent.click(inside.getByRole("button", { name: "Inspect citation 3" }));
    expect(onCite).toHaveBeenCalledWith(2);
  });

  it("sets a long phrase smaller than a short number, so that it stays on a line", () => {
    expect(figureSize("62%")).toBe(200);
    expect(figureSize("3.5 °C")).toBe(200);
    expect(figureSize("12,500 users")).toBeLessThan(200);
    expect(figureSize("Every day for three months")).toBeLessThan(figureSize("12,500 users"));
    expect(figureSize("人工成本占总成本的大头")).toBeLessThan(figureSize("62%"));
    expect(figureSize("A phrase that is much too long to be a figure at any size at all, really")).toBe(64);
  });
});

describe("a table", () => {
  const body = "## A is cheaper over three years\nBoth sides are costed the same way [9].\n\n| | A | B |\n| :-- | :-: | --: |\n| Up front | 1.2m [2] | 0.8m [2] |\n| Running | 0.9m [4] | 2.3m [4] |\nA costs more first and less after [4].";

  it("compare is a grid: the respects down the side, the objects across, and the words around it kept", async () => {
    const onCite = vi.fn();
    const { face, inside } = draw("compare", body, { onCite });
    const table = inside.getByRole("table");
    expect(within(table).getAllByRole("columnheader").map(said)).toEqual(["", "A", "B"]);
    expect(within(table).getAllByRole("rowheader").map(said)).toEqual(["Up front", "Running"]);
    expect(within(table).getAllByRole("cell").map(said)).toEqual(["1.2m [2]", "0.8m [2]", "0.9m [4]", "2.3m [4]"]);
    // The alignment of the Markdown table is kept.
    expect(within(table).getAllByRole("cell")[0]).toHaveStyle({ textAlign: "center" });
    expect(within(table).getAllByRole("cell")[1]).toHaveStyle({ textAlign: "right" });
    expect(face.querySelector(".outputs-s-lead")).toHaveTextContent("Both sides are costed the same way [9].");
    expect(face.querySelector(".outputs-s-after")).toHaveTextContent("A costs more first and less after [4].");
    await userEvent.click(within(face.querySelector<HTMLElement>(".outputs-s-after")!).getByRole("button", { name: "Inspect citation 4" }));
    expect(onCite).toHaveBeenCalledWith(3);
    // The rule under the header is a row of its own, which a screen reader is not told about.
    expect(face.querySelector(".outputs-s-underline")).toHaveAttribute("aria-hidden", "true");
  });

  it("two_column is two panels, row for row", () => {
    const { face, inside } = draw("two_column", "## Better\n| Usual | Storm routine |\n| --- | --- |\n| Once a year [5] | After every storm [5] |\n| Eight leaks [5] | Seventeen leaks [5] |");
    expect(face.querySelector(".outputs-s-sides")).not.toBeNull();
    expect(inside.getAllByRole("columnheader").map(said)).toEqual(["Usual", "Storm routine"]);
    expect(inside.getAllByRole("row")).toHaveLength(3);
  });

  it("a table that is not two columns is drawn as the grid under two_column", () => {
    const { face } = draw("two_column", "## H\n| a | b | c |\n| --- | --- | --- |\n| 1 | 2 | 3 |");
    expect(face).toHaveAttribute("data-drawn", "compare");
  });
});

describe("a flow and a timeline", () => {
  it("number the steps of a flow and keep them across the slide up to four", () => {
    const { face, inside } = draw("flow", "## Four steps\n1. Survey [2]\n2. Clean [2]\n3. Coat [3]\n4. Measure [1]");
    const list = face.querySelector<HTMLElement>(".outputs-s-steps")!;
    expect(list).not.toHaveClass("is-down");
    expect(list.style.getPropertyValue("--n")).toBe("4");
    expect(within(list).getAllByRole("listitem").map(said)).toEqual(["1Survey [2]", "2Clean [2]", "3Coat [3]", "4Measure [1]"]);
    expect(face.querySelectorAll(".outputs-s-step-dot[aria-hidden=true]")).toHaveLength(4);
    expect(inside.getByRole("list")).toBe(list);
  });

  it("separates a bold flow step name from its explanation without losing punctuation or citations", async () => {
    const onCite = vi.fn();
    const { face, inside } = draw(
      "flow",
      "## Review the evidence\n1. **检查材料**：保留所有限定条件 [3]\n2. **Confirm** — keep the complete citation [2]",
      { onCite },
    );
    const titles = Array.from(face.querySelectorAll(".outputs-s-step-title"));
    const details = Array.from(face.querySelectorAll(".outputs-s-step-detail"));
    expect(titles.map(said)).toEqual(["检查材料：", "Confirm —"]);
    expect(details.map(said)).toEqual(["保留所有限定条件 [3]", "keep the complete citation [2]"]);
    await userEvent.click(inside.getByRole("button", { name: "Inspect citation 3" }));
    expect(onCite).toHaveBeenCalledWith(2);
  });

  it("keeps detail-only and citation-only steps in a named horizontal flow", () => {
    const { face } = draw(
      "flow",
      "## Keep the whole sequence\n1. **Record**: keep the measure [1]\n2. Compare both values [2]\n3. **Confirm** [3]",
      { onCite: vi.fn() },
    );
    expect(face.querySelector(".outputs-s-steps")).toHaveClass("has-named-steps");
    expect(face.querySelectorAll(".outputs-s-step-title")).toHaveLength(3);
    expect(face.querySelector(".outputs-s-step-title.is-empty")).toBeInTheDocument();
    expect(face.querySelector(".outputs-s-step-title")).toHaveTextContent("Record");
    expect(face.querySelectorAll("button[aria-label^='Inspect citation']")).toHaveLength(3);
    expect(face).toHaveTextContent("Confirm [3]");
  });

  it("keeps negative signs with flow explanations and their citations", async () => {
    const onCite = vi.fn();
    const { face, inside } = draw(
      "flow",
      "## Negative amounts\n1. **Cut** -10% [1]\n2. **Change** –10 [2]\n3. **Subtract** −10% [3]\n4. **Cost** -  ￥10 [4]",
      { onCite },
    );
    expect(Array.from(face.querySelectorAll(".outputs-s-step-title")).map(said)).toEqual([
      "Cut", "Change", "Subtract", "Cost",
    ]);
    expect(Array.from(face.querySelectorAll(".outputs-s-step-detail")).map(said)).toEqual([
      "-10% [1]", "–10 [2]", "−10% [3]", "-  ￥10 [4]",
    ]);
    await userEvent.click(inside.getByRole("button", { name: "Inspect citation 3" }));
    expect(onCite).toHaveBeenCalledWith(2);
  });

  it("renders spaced decimal and full-width currency signs as literal flow text", () => {
    const { face } = draw(
      "flow",
      "## Preserve the values\n1. **Decimal** - .5% [1]\n2. **Budget** - ￥ 10 [2]",
      { onCite: vi.fn() },
    );
    expect(Array.from(face.querySelectorAll(".outputs-s-step-detail")).map(said)).toEqual([
      "- .5% [1]", "- ￥ 10 [2]",
    ]);
    expect(face.querySelectorAll("button[aria-label^='Inspect citation']")).toHaveLength(2);
  });

  it("run down the slide from five steps, or five events, so that each has room for its words", () => {
    const five = (mark: string) => Array.from({ length: 5 }, (_, at) => mark === "." ? `${at + 1}. Step ${at + 1}` : `- **${2000 + at}** Event ${at + 1}`).join("\n");
    expect(draw("flow", `## Five\n${five(".")}`).face.querySelector(".outputs-s-steps")).toHaveClass("is-down");
    cleanup();
    expect(draw("timeline", `## Five\n${five("-")}`).face.querySelector(".outputs-s-events")).toHaveClass("is-down");
    cleanup();
    expect(draw("timeline", "## Three\n- **2019** A [1]\n- **2021** B [2]\n- **2024** C [3]").face.querySelector(".outputs-s-events")).not.toHaveClass("is-down");
  });

  it("put the time over each event and its sentence under it", () => {
    const { face } = draw("timeline", "## Adoption\n- **2019** Pilot on twelve buildings [1]\n- **2021**: The city pays [4]");
    const items = Array.from(face.querySelectorAll(".outputs-s-events > li"));
    expect(items.map((item) => said(item.querySelector(".outputs-s-when")))).toEqual(["2019", "2021"]);
    expect(items.map((item) => said(item.querySelector(".outputs-s-what")))).toEqual(["Pilot on twelve buildings [1]", "The city pays [4]"]);
    // The colon between the time and the sentence is not drawn.
    expect(face.textContent).not.toContain(":");
  });
});

describe("cards", () => {
  it("sit in two columns for two or four, three for the rest, and are larger when there are few", () => {
    const cards = (count: number) => `## Parts\n${Array.from({ length: count }, (_, at) => `- **Part ${at + 1}**: Words ${at + 1} [1]`).join("\n")}`;
    const columns = (count: number) => {
      const { face } = draw("cards", cards(count));
      const list = face.querySelector<HTMLElement>(".outputs-s-cards")!;
      const found = [list.style.getPropertyValue("--columns"), list.classList.contains("is-few")];
      cleanup();
      return found;
    };
    expect([2, 3, 4, 5, 6].map(columns)).toEqual([["2", true], ["3", true], ["2", false], ["3", false], ["3", false]]);
  });

  it("show each name over its sentence", () => {
    const { face } = draw("cards", "## Parts\n- **Surface prep**: Dirt causes failures [2]\n- **Quality**: Cheap acrylics fade [3]");
    expect(Array.from(face.querySelectorAll(".outputs-s-card-name")).map(said)).toEqual(["Surface prep", "Quality"]);
    expect(Array.from(face.querySelectorAll(".outputs-s-card-text")).map(said)).toEqual(["Dirt causes failures [2]", "Cheap acrylics fade [3]"]);
  });
});

describe("a quote, a figure, text and takeaways", () => {
  it("a quote is the words in a block with who said them under", () => {
    const { face } = draw("quote", "## Regulators\n> A system that cannot say why should not decide alone.\n— Guidance, 2023 [6]");
    expect(face.querySelector(".outputs-s-quote blockquote")).toHaveTextContent("A system that cannot say why should not decide alone.");
    expect(face.querySelector(".outputs-s-quote figcaption")).toHaveTextContent("— Guidance, 2023 [6]");
    // The heading is the slide's, outside the indent that hangs the mark.
    expect(face.querySelector(".outputs-s-quote")?.contains(face.querySelector("h2"))).toBe(false);
  });

  it("a figure is the managed image, with the line about it under it, and its caption in a frame when it cannot be had", () => {
    const { face, inside } = draw("figure", "## Groups part\n![Figure 2: glucose by group](gunther-asset:ast_3f9a1c)\nLook after week four [4]");
    const image = face.querySelector("img")!;
    expect(image).toHaveAttribute("src", "/api/assets/ast_3f9a1c");
    expect(image).toHaveAttribute("alt", "Figure 2: glucose by group");
    expect(face.querySelector(".outputs-s-after")).toHaveTextContent("Look after week four [4]");
    fireEvent.error(image);
    expect(face.querySelector("img")).toBeNull();
    expect(inside.getByRole("img", { name: "Figure 2: glucose by group" })).toHaveTextContent("Figure 2: glucose by group");
  });

  it("text keeps its list, in lines of a readable length", () => {
    const { face, inside } = draw("text", "## Not a free lunch\n\n- Winter heating rises [4]\n- Reflectance fades [2]\n\nSo wash it.");
    expect(face.querySelector(".outputs-s-body")).not.toBeNull();
    expect(inside.getAllByRole("listitem")).toHaveLength(2);
    expect(face).toHaveTextContent("So wash it.");
  });

  it("takeaways number what they list with a large numeral, and say so when there is only a sentence", () => {
    const { face } = draw("takeaways", "## Start here\n- Coat the dark roofs [2]\n- Wash every year [1]\n- Check winter heating [4]");
    expect(Array.from(face.querySelectorAll(".outputs-s-numeral")).map(said)).toEqual(["1", "2", "3"]);
    cleanup();
    const sentence = draw("takeaways", "## Start here\n\nOnly a sentence [2].");
    expect(sentence.face.querySelector(".outputs-s-takeaways")).toBeNull();
    expect(sentence.face.querySelector(".outputs-s-after")).toHaveTextContent("Only a sentence [2].");
  });
});

describe("a bar chart", () => {
  const bars = "## Labour is the largest\n| Item | Share (%) |\n| --- | --- |\n| **Labour** | 62 [3] |\n| Equipment | 18 [3] |\n| Other | 20 [7] |\nLabour outweighs the rest [3].";

  it("is a bar for each row, as long as its value, with the value at its tip and the unit from the header", () => {
    const { face, inside } = draw("bar_chart", bars);
    const marks = Array.from(face.querySelectorAll<HTMLElement>(".outputs-s-bar"));
    expect(marks).toHaveLength(3);
    expect(marks.map((mark) => Math.round(parseFloat(mark.style.width)))).toEqual([100, 29, 32]);
    expect(Array.from(face.querySelectorAll(".outputs-s-bar-value")).map(said)).toEqual(["62% [3]", "18% [3]", "20% [7]"]);
    expect(face.querySelector(".outputs-s-measure")).toHaveTextContent("Share (%)");
    expect(inside.getByText("Equipment")).toBeInTheDocument();
    // No legend: there is one series.
    expect(face.querySelector(".outputs-s-legend, [role=legend]")).toBeNull();
    expectNoNumbersMadeUp(face, bars);
  });

  it("is in ink, but the row the table makes bold is in the accent and the others in grey", () => {
    const tone = (face: HTMLElement) => Array.from(face.querySelectorAll(".outputs-s-bar")).map((mark) => ["is-accent", "is-grey"].filter((name) => mark.classList.contains(name)).join(""));
    expect(tone(draw("bar_chart", bars).face)).toEqual(["is-accent", "is-grey", "is-grey"]);
    cleanup();
    // Nothing bold: every bar is ink, the default mark, without a class of its own.
    expect(tone(draw("bar_chart", bars.replace("**Labour**", "Labour")).face)).toEqual(["", "", ""]);
  });

  it("shows each value's citation, and opens it", async () => {
    const onCite = vi.fn();
    const { face } = draw("bar_chart", bars, { onCite });
    const buttons = within(face.querySelector<HTMLElement>(".outputs-s-bars")!).getAllByRole("button", { name: /Inspect citation/ });
    expect(buttons.map((button) => button.getAttribute("aria-label"))).toEqual(["Inspect citation 3", "Inspect citation 3", "Inspect citation 7"]);
    await userEvent.click(buttons[2]!);
    expect(onCite).toHaveBeenCalledWith(6);
  });

  it("grows to the left of zero for a negative value, with the room for its label", () => {
    const { face } = draw("bar_chart", "## Change\n| Region | Sales (%) |\n| --- | --- |\n| North | 12 [1] |\n| South | -8 [1] |");
    expect(face.querySelector(".outputs-s-bars")).toHaveClass("has-negative");
    const [north, south] = Array.from(face.querySelectorAll<HTMLElement>(".outputs-s-bar"));
    expect(north).not.toHaveClass("is-negative");
    expect(south).toHaveClass("is-negative");
    // Zero is 40% of the way along (8 below, 12 above): the north bar starts there and the south bar ends there.
    expect(parseFloat(north!.style.left)).toBeCloseTo(40, 5);
    expect(parseFloat(south!.style.left) + parseFloat(south!.style.width)).toBeCloseTo(40, 5);
  });

  it("is drawn as the table it is when a value is not a number, and so never draws a bar for it", () => {
    const { face } = draw("bar_chart", "## Verdict\n| Group | Verdict |\n| --- | --- |\n| Treated | high [4] |\n| Control | low [4] |");
    expect(face).toHaveAttribute("data-drawn", "compare");
    expect(face.querySelector(".outputs-s-bar")).toBeNull();
    expect(face.querySelector(".outputs-s-table")).not.toBeNull();
  });

  it("shows a mark's value and its source when it is pointed at or focused, but only in the deck on the page", async () => {
    const user = userEvent.setup();
    const sources = ["Field survey · table 1", "Price list · p. 2", "Harlow trial · table 2"];
    const { face } = draw("bar_chart", bars, { onCite: vi.fn(), sources });
    const tracks = face.querySelectorAll<HTMLElement>(".outputs-s-track");
    expect(screen.queryByRole("tooltip")).toBeNull();
    await user.hover(tracks[0]!);
    const tip = screen.getByRole("tooltip");
    expect(tip).toHaveTextContent("62%");
    expect(tip).toHaveTextContent("Labour");
    expect(tip).toHaveTextContent("[3] Harlow trial · table 2");
    await user.unhover(tracks[0]!);
    expect(screen.queryByRole("tooltip")).toBeNull();
    // The keyboard gets the same.
    act(() => tracks[1]!.focus());
    expect(screen.getByRole("tooltip")).toHaveTextContent("18%");
    cleanup();
    // Present, the strip and the printed page cannot be pointed at: no tooltip, and nothing to tab to.
    const still = draw("bar_chart", bars, { sources });
    const track = still.face.querySelector<HTMLElement>(".outputs-s-track")!;
    expect(track).not.toHaveAttribute("tabindex");
    await user.hover(track);
    expect(screen.queryByRole("tooltip")).toBeNull();
  });
});

describe("a line chart", () => {
  const line = "## More than half by 2023\n| Year | Treated roofs (%) |\n| --- | --- |\n| 2019 | 4 [1] |\n| 2020 | 11 [1] |\n| 2021 | 24 [4] |\n| 2023 | 51 [6] |\nGrowth slows [6].";

  it("is one line and a dot on each point, and the value is written at the end only", () => {
    const { face } = draw("line_chart", line);
    expect(face.querySelectorAll("svg path.outputs-s-line")).toHaveLength(1);
    const dots = Array.from(face.querySelectorAll("circle.outputs-s-dot"));
    expect(dots).toHaveLength(4);
    // The end marker is at least 8 px across.
    expect(Number(dots[3]!.getAttribute("r"))).toBeGreaterThanOrEqual(4);
    expect(Array.from(face.querySelectorAll(".outputs-s-end")).map(said)).toEqual(["51%"]);
    expect(face.querySelector(".outputs-s-measure")).toHaveTextContent("Treated roofs (%)");
  });

  it("puts each point's citation under its label, and no number on the scale", async () => {
    const onCite = vi.fn();
    const { face, inside } = draw("line_chart", line, { onCite });
    expect(Array.from(face.querySelectorAll(".outputs-s-tick")).map(said)).toEqual(["2019 [1]", "2020 [1]", "2021 [4]", "2023 [6]"]);
    await userEvent.click(within(face.querySelectorAll<HTMLElement>(".outputs-s-tick")[2]!).getByRole("button", { name: "Inspect citation 4" }));
    expect(onCite).toHaveBeenCalledWith(3);
    expectNoNumbersMadeUp(face, line);
    // Hairlines, with no label of their own: the lines are SVG, and the words are all HTML.
    expect(face.querySelectorAll("svg text")).toHaveLength(0);
    // Nothing to point at where nothing can be pointed at: Present, the strip and the page that is printed.
    expect(face.querySelectorAll(".outputs-s-hit")).toHaveLength(4);
    expect(draw("line_chart", line).face.querySelectorAll(".outputs-s-hit")).toHaveLength(0);
    expect(inside.queryByText("0")).toBeNull();
  });

  it("spaces the points by their times when the labels are numbers", () => {
    const { face } = draw("line_chart", line);
    const xs = Array.from(face.querySelectorAll("circle.outputs-s-dot")).map((dot) => Number(dot.getAttribute("cx")));
    // 2019, 2020, 2021, 2023: the last gap is twice the others.
    const gaps = xs.slice(1).map((x, at) => x - xs[at]!);
    expect(gaps[1]).toBeCloseTo(gaps[0]!, 5);
    expect(gaps[2]).toBeCloseTo(2 * gaps[0]!, 5);
    cleanup();
    const named = draw("line_chart", "## Quarters\n| Quarter | Users |\n| --- | --- |\n| Q1 | 4 |\n| Q2 | 5 |\n| Q9 | 6 |");
    const spaced = Array.from(named.face.querySelectorAll("circle.outputs-s-dot")).map((dot) => Number(dot.getAttribute("cx")));
    expect(spaced[1]! - spaced[0]!).toBeCloseTo(spaced[2]! - spaced[1]!, 5);
  });

  it("has a table of its values for a screen reader, and a tooltip with the source of a dot", async () => {
    const user = userEvent.setup();
    const { face } = draw("line_chart", line, { onCite: vi.fn(), sources: ["a", "b", "c", "Winter study · figure 3"] });
    const table = within(face).getByRole("table", { hidden: true });
    expect(within(table).getAllByRole("row", { hidden: true }).map(said)).toEqual(["YearTreated roofs (%)", "20194%", "202011%", "202124%", "202351%"]);
    await user.hover(face.querySelectorAll<SVGElement>(".outputs-s-hit")[2]!);
    expect(screen.getByRole("tooltip")).toHaveTextContent("24%");
    expect(screen.getByRole("tooltip")).toHaveTextContent("[4] Winter study · figure 3");
  });

  it("starts at zero, so that a slope is true, and shows a negative value below it", () => {
    const { face } = draw("line_chart", "## Profit\n| Quarter | Profit |\n| --- | --- |\n| Q1 | -4 |\n| Q2 | 6 |");
    const [first, second] = Array.from(face.querySelectorAll("circle.outputs-s-dot")).map((dot) => Number(dot.getAttribute("cy")));
    // Lower on the slide is a larger y: Q1 is the lowest point of the chart.
    expect(first!).toBeGreaterThan(second!);
    expect(face.querySelectorAll("line.outputs-s-axis")).toHaveLength(1);
    cleanup();
    expect(draw("line_chart", "## Users\n| Year | Users |\n| --- | --- |\n| 2020 | 12 |\n| 2021 | 30 |").face.querySelectorAll("line.outputs-s-axis")).toHaveLength(1);
  });

  it("marks the row the table makes bold in the accent, and labels it", () => {
    const { face } = draw("line_chart", "## Peak\n| Quarter | Users |\n| --- | --- |\n| Q1 | 4 |\n| **Q2** | 9 |\n| Q3 | 6 |", { accent: "red" });
    expect(Array.from(face.querySelectorAll("circle.outputs-s-dot")).map((dot) => dot.classList.contains("is-accent"))).toEqual([false, true, false]);
    expect(Array.from(face.querySelectorAll(".outputs-s-flag")).map(said)).toEqual(["9"]);
  });
});

describe("the words of a slide", () => {
  it("keep a citation with the word before it", () => {
    expect(tight("A claim [3] and another [1][2].")).toBe("A claim\u00a0[3] and another\u00a0[1][2].");
    expect(tight("Not sure [?], partly [p:3], inferred [i:3] and two [1, 2]")).toBe("Not sure\u00a0[?], partly\u00a0[p:3], inferred\u00a0[i:3] and two\u00a0[1, 2]");
    expect(tight("No citation here, only a [bracket] and [text].")).toBe("No citation here, only a [bracket] and [text].");
    expect(tight("A  \t [4]")).toBe("A\u00a0[4]");
  });

  it("are drawn by AnswerBody, so a citation the deck does not have stays as written", () => {
    const { face } = draw("text", "## Heading\n\nSee [12] for more.", { numbers: [1, 2, 3] });
    expect(within(face).queryByRole("button", { name: /Inspect citation/ })).toBeNull();
    expect(face).toHaveTextContent("See [12] for more.");
  });
});
