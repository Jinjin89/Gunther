import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { AnswerStyleSetting } from "./AnswerStyleSetting";
import { ANSWER_STYLE_KEY, readAnswerStyle, useAnswerStyle } from "./askModel";

function AskPreference() {
  const [style] = useAnswerStyle();
  return <output aria-label="Ask preference">{style}</output>;
}

describe("Answer style in Settings", () => {
  beforeEach(() => window.localStorage.clear());

  it("keeps a previous preference and applies a new one to Ask immediately and after reopening", async () => {
    window.localStorage.setItem(ANSWER_STYLE_KEY, "academic");
    const user = userEvent.setup();
    const view = render(<><AnswerStyleSetting /><AskPreference /></>);
    expect(screen.getByRole("combobox", { name: "Answer style" })).toHaveValue("academic");
    await user.selectOptions(screen.getByRole("combobox", { name: "Answer style" }), "concise");
    expect(screen.getByLabelText("Ask preference")).toHaveTextContent("concise");
    expect(readAnswerStyle()).toBe("concise");
    view.unmount();
    render(<AnswerStyleSetting />);
    expect(screen.getByRole("combobox", { name: "Answer style" })).toHaveValue("concise");
  });
});
