/**
 * The one input on the page, and the only thing this tab can put into a language model.
 *
 * What these hold is mostly about not lying to the user: a box that clears itself has told them the
 * request went, so it may only clear when it actually did.
 *
 * `fireEvent` rather than `user-event`: typing and submitting is all this needs, and that is already
 * in the testing library the project ships. A second dependency to press Enter would not earn itself.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AskBox } from "./Hud";

function type(value: string) {
  const input = screen.getByLabelText("Ask Zero");
  fireEvent.change(input, { target: { value } });
  return input;
}

function submit() {
  fireEvent.click(screen.getByLabelText("Send request"));
}

describe("the ask box", () => {
  it("sends what was typed", () => {
    const onAsk = vi.fn(() => true);
    render(<AskBox onAsk={onAsk} connected />);

    type("how many notes?");
    submit();

    expect(onAsk).toHaveBeenCalledWith("how many notes?");
  });

  it("clears once the text has actually gone", () => {
    render(<AskBox onAsk={() => true} connected />);

    const input = type("hello");
    submit();

    expect(input).toHaveValue("");
  });

  it("keeps the text when it could not be sent", () => {
    // The decisive one. Clearing here would look identical to a successful send, and the user would
    // sit waiting on a turn that never started - with what they wrote already gone.
    render(<AskBox onAsk={() => false} connected />);

    const input = type("hello");
    submit();

    expect(input).toHaveValue("hello");
  });

  it("hands whitespace over rather than filtering it here", () => {
    // Trimming lives in one place, in the hook, so the contract and the box cannot disagree about
    // what counts as empty. What matters here is that the box does not empty itself.
    const onAsk = vi.fn(() => false);
    render(<AskBox onAsk={onAsk} connected />);

    const input = type("   ");
    submit();

    expect(onAsk).toHaveBeenCalledWith("   ");
    expect(input).toHaveValue("   ");
  });

  it("says it is waiting rather than accepting text into a closed socket", () => {
    render(<AskBox onAsk={() => true} connected={false} />);

    const input = screen.getByLabelText("Ask Zero");
    expect(input).toBeDisabled();
    expect(input).toHaveAttribute("placeholder", "Waiting for the brain");
  });
});
