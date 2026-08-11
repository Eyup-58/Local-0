/**
 * The approval dialog, held against the criteria in ROADMAP.md M3.
 *
 * Every one of them is about the dialog not lying to the person reading it. The user is deciding
 * whether to let something touch their machine, and the only thing standing between them and a
 * misleading render is this file.
 *
 * The injection case reads the checked-in contract fixture rather than a string written here.
 * ws.approval-request-untrusted.json is a *valid* message whose content argument carries
 * `<img src=x onerror=alert(1)>` and an instruction to approve itself - legal on the wire on
 * purpose, because filtering it in the contract would be the illusion of cleaning. Rendering it as
 * text is this component's job, so the fixture is the right input.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ApprovalRequest } from "../contracts/types";
import { ApprovalDialog, DESTRUCTIVE_ARMING_MS } from "./ApprovalDialog";

const EXAMPLES_DIR = join(import.meta.dirname, "..", "..", "..", "contracts", "examples");

function fixture(name: string): ApprovalRequest["payload"] {
  return JSON.parse(readFileSync(join(EXAMPLES_DIR, name), "utf-8")).payload;
}

function request(overrides: Partial<ApprovalRequest["payload"]> = {}): ApprovalRequest["payload"] {
  return { ...fixture("ws.approval-request.json"), ...overrides };
}

describe("what the dialog shows", () => {
  it("names the capability that will run", () => {
    render(<ApprovalDialog request={request()} onDecision={() => {}} />);

    expect(screen.getByText("delete_file")).toBeInTheDocument();
  });

  it("shows every resolved argument", () => {
    render(
      <ApprovalDialog
        request={request({ resolved_args: { path: "C:\\ws\\a.txt", overwrite: true } })}
        onDecision={() => {}}
      />,
    );

    expect(screen.getByText("C:\\ws\\a.txt")).toBeInTheDocument();
    expect(screen.getByText("true")).toBeInTheDocument();
  });

  it("shows the full affected-path list rather than a count", () => {
    render(
      <ApprovalDialog
        request={request({ affected_paths: ["C:\\ws\\a.txt", "C:\\ws\\b.txt"] })}
        onDecision={() => {}}
      />,
    );

    expect(screen.getByText("C:\\ws\\a.txt")).toBeInTheDocument();
    expect(screen.getByText("C:\\ws\\b.txt")).toBeInTheDocument();
  });

  it("says so when an operation touches no path at all", () => {
    // An empty list must not render as an absent section: "touches nothing" and "we did not work
    // out what it touches" are different facts and the user is entitled to know which one this is.
    render(<ApprovalDialog request={request({ affected_paths: [] })} onDecision={() => {}} />);

    expect(screen.getByText(/no files are affected/i)).toBeInTheDocument();
  });
});

describe("markup is rendered as text, never as markup", () => {
  it("does not execute or embed HTML from a resolved argument", () => {
    const { container } = render(
      <ApprovalDialog request={fixture("ws.approval-request-untrusted.json")} onDecision={() => {}} />,
    );

    // The element the payload is trying to create must not exist...
    expect(container.querySelector("img")).toBeNull();
    // ...and the text of it must be visible, so the user can see what they are being shown.
    expect(screen.getByText(/<img src=x onerror=alert\(1\)>/)).toBeInTheDocument();
  });

  it("does not turn a markdown link into an anchor", () => {
    const { container } = render(
      <ApprovalDialog
        request={request({ resolved_args: { note: "[click here](javascript:alert(1))" } })}
        onDecision={() => {}}
      />,
    );

    expect(container.querySelector("a")).toBeNull();
    expect(screen.getByText("[click here](javascript:alert(1))")).toBeInTheDocument();
  });

  it("renders a capability name containing markup as text", () => {
    const { container } = render(
      <ApprovalDialog request={request({ capability: "<b>delete_file</b>" })} onDecision={() => {}} />,
    );

    expect(container.querySelector("b")).toBeNull();
    expect(screen.getByText("<b>delete_file</b>")).toBeInTheDocument();
  });
});

describe("origin", () => {
  it("marks an untrusted-origin request as visually distinct", () => {
    const { container } = render(
      <ApprovalDialog request={request({ origin: "untrusted_content" })} onDecision={() => {}} />,
    );

    expect(container.querySelector(".approval--untrusted")).not.toBeNull();
  });

  it("defaults the selection to Reject when the origin is untrusted", () => {
    render(<ApprovalDialog request={request({ origin: "untrusted_content" })} onDecision={() => {}} />);

    expect(screen.getByRole("button", { name: /reject/i })).toHaveFocus();
  });

  it("does not focus Approve for a user-direct request either", () => {
    // Nothing dangerous is ever the focused default. A queued keystroke must not land on approve.
    render(<ApprovalDialog request={request({ origin: "user_direct" })} onDecision={() => {}} />);

    expect(screen.getByRole("button", { name: /approve/i })).not.toHaveFocus();
  });
});

describe("the controls", () => {
  it("does not approve when Enter is pressed", () => {
    // ROADMAP M3: Enter is not bound to approve. A reflexive keypress must not authorise anything.
    const onDecision = vi.fn();
    const { container } = render(
      <ApprovalDialog request={request({ side_effect: "write" })} onDecision={onDecision} />,
    );

    fireEvent.keyDown(container.firstChild as Element, { key: "Enter" });

    expect(onDecision).not.toHaveBeenCalled();
  });

  it("keeps approve disabled for a moment on a destructive operation", () => {
    vi.useFakeTimers();
    try {
      render(<ApprovalDialog request={request({ side_effect: "destructive" })} onDecision={() => {}} />);

      expect(screen.getByRole("button", { name: /approve/i })).toBeDisabled();

      act(() => void vi.advanceTimersByTime(DESTRUCTIVE_ARMING_MS));

      expect(screen.getByRole("button", { name: /approve/i })).toBeEnabled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("allows a non-destructive approval immediately", () => {
    render(<ApprovalDialog request={request({ side_effect: "write" })} onDecision={() => {}} />);

    expect(screen.getByRole("button", { name: /approve/i })).toBeEnabled();
  });

  it("reports an approval", () => {
    const onDecision = vi.fn();
    render(<ApprovalDialog request={request({ side_effect: "write" })} onDecision={onDecision} />);

    fireEvent.click(screen.getByRole("button", { name: /approve/i }));

    expect(onDecision).toHaveBeenCalledWith(true);
  });

  it("reports a rejection, and reject is never delayed", () => {
    // Rejecting is always available. Making the safe answer wait would be an argument for the
    // dangerous one.
    const onDecision = vi.fn();
    render(<ApprovalDialog request={request({ side_effect: "destructive" })} onDecision={onDecision} />);

    const reject = screen.getByRole("button", { name: /reject/i });
    expect(reject).toBeEnabled();

    fireEvent.click(reject);

    expect(onDecision).toHaveBeenCalledWith(false);
  });
});
