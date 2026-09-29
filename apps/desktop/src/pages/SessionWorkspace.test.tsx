import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Composer } from "./SessionWorkspace";

const renderComposer = (ready: boolean) => {
  const onChange = vi.fn();
  const onSend = vi.fn();
  render(
    <Composer
      value="What does this source support?"
      sending={false}
      sourceCount={0}
      chapterTitle={undefined}
      onChange={onChange}
      onSend={onSend}
      onStop={vi.fn()}
      onSources={vi.fn()}
      readOnly={false}
      ready={ready}
    />,
  );
  return { onChange, onSend };
};

describe("Session composer durability boundary", () => {
  it("does not accept a question until its durable session is ready", async () => {
    const user = userEvent.setup();
    const { onSend } = renderComposer(false);

    expect(screen.getByRole("textbox", { name: "Message Gunther" })).toBeDisabled();
    expect(screen.getByText("Preparing a durable conversation before accepting questions…")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Send message" }));
    expect(onSend).not.toHaveBeenCalled();
  });

  it("enables sending after the durable session is ready", async () => {
    const user = userEvent.setup();
    const { onSend } = renderComposer(true);

    expect(screen.getByRole("textbox", { name: "Message Gunther" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Send message" }));
    expect(onSend).toHaveBeenCalledOnce();
  });

  it("has a Web switch that reflects whether web search is set up", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const { rerender } = render(
      <Composer value="" sending={false} sourceCount={0} chapterTitle={undefined} onChange={vi.fn()} onSend={vi.fn()} onStop={vi.fn()} onSources={vi.fn()} readOnly={false} ready web={{ available: false, enabled: false, onChange }} />,
    );
    expect(screen.getByRole("button", { name: "Web" })).toBeDisabled();
    rerender(
      <Composer value="" sending={false} sourceCount={0} chapterTitle={undefined} onChange={vi.fn()} onSend={vi.fn()} onStop={vi.fn()} onSources={vi.fn()} readOnly={false} ready web={{ available: true, enabled: false, onChange }} />,
    );
    await user.click(screen.getByRole("button", { name: "Web" }));
    expect(onChange).toHaveBeenCalledWith(true);
  });
});
