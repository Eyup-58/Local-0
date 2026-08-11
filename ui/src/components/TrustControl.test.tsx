/**
 * The trust button, and the banner that must be impossible to miss while it is on.
 *
 * The user chose this deliberately: it bypasses approval for every invocation regardless of
 * side_effect or origin, and it persists across restarts. These tests are not about narrowing that.
 * They are about the two things that make it safe to *have*:
 *
 * * while it is on, the page says so loudly and continuously, because the cost of not noticing is
 *   every operation running unattended;
 * * arming it takes a deliberate second action, while disarming is immediate. Making the safe
 *   direction slower would be an argument for the dangerous one.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TrustControl } from "./TrustControl";

describe("while approval is on (trust off)", () => {
  it("shows no banner", () => {
    const { container } = render(<TrustControl enabled={false} onChange={() => {}} />);

    expect(container.querySelector(".trust-banner")).toBeNull();
  });

  it("offers a control to turn approval off", () => {
    render(<TrustControl enabled={false} onChange={() => {}} />);

    expect(screen.getByRole("button", { name: /turn approval off/i })).toBeInTheDocument();
  });

  it("does not disable approval on the first click", () => {
    // Arming asks once. One stray click must not remove every prompt on the machine.
    const onChange = vi.fn();
    render(<TrustControl enabled={false} onChange={onChange} />);

    fireEvent.click(screen.getByRole("button", { name: /turn approval off/i }));

    expect(onChange).not.toHaveBeenCalled();
  });

  it("states the consequence before confirming", () => {
    render(<TrustControl enabled={false} onChange={() => {}} />);

    fireEvent.click(screen.getByRole("button", { name: /turn approval off/i }));

    expect(screen.getByText(/without asking/i)).toBeInTheDocument();
  });

  it("disables approval once confirmed", () => {
    const onChange = vi.fn();
    render(<TrustControl enabled={false} onChange={onChange} />);

    fireEvent.click(screen.getByRole("button", { name: /turn approval off/i }));
    fireEvent.click(screen.getByRole("button", { name: /^yes, turn it off$/i }));

    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("changes nothing if the confirmation is dismissed", () => {
    const onChange = vi.fn();
    render(<TrustControl enabled={false} onChange={onChange} />);

    fireEvent.click(screen.getByRole("button", { name: /turn approval off/i }));
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));

    expect(onChange).not.toHaveBeenCalled();
  });
});

describe("while approval is off (trust on)", () => {
  it("shows a banner saying so", () => {
    const { container } = render(<TrustControl enabled={true} onChange={() => {}} />);

    expect(container.querySelector(".trust-banner")).not.toBeNull();
  });

  it("says plainly what is happening, not just that a mode is active", () => {
    render(<TrustControl enabled={true} onChange={() => {}} />);

    expect(screen.getByText(/running without asking/i)).toBeInTheDocument();
  });

  it("turns approval back on immediately, with no confirmation", () => {
    const onChange = vi.fn();
    render(<TrustControl enabled={true} onChange={onChange} />);

    fireEvent.click(screen.getByRole("button", { name: /turn approval back on/i }));

    expect(onChange).toHaveBeenCalledWith(false);
  });
});
