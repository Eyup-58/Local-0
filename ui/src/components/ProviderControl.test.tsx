/**
 * The network boundary control.
 *
 * Two things are being protected here, and neither is a feature:
 *
 * * **the user can always tell which boundary is in force** — Local means nothing leaves the
 *   machine, and that claim is worthless if the screen can be wrong about it;
 * * **the key is never on screen and never in the DOM after it is sent.** A password input hides it
 *   from a shoulder; clearing the field is what keeps it out of a screenshot, a screen share, and
 *   the accessibility tree.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ProviderControl } from "./ProviderControl";

// Not shaped like a vendor-issued key: the pre-commit hook matches real prefixes and is right to.
// These tests need a distinctive string, not a plausible one.
const KEY = "local-zero-test-value-2222222222222222";

function noop() {}

describe("what the screen says about the boundary", () => {
  it("names local mode and says nothing leaves the machine", () => {
    render(
      <ProviderControl mode="local" model="gemma4:26b" hasKey={false} onSelect={noop} onKey={noop} />,
    );

    expect(screen.getByRole("status")).toHaveTextContent(/local/i);
    expect(screen.getByRole("status")).toHaveTextContent(/nothing leaves this machine/i);
  });

  it("names cloud mode and says requests go to the provider", () => {
    render(<ProviderControl mode="cloud" model="gemini-2.0-flash" hasKey onSelect={noop} onKey={noop} />);

    expect(screen.getByRole("status")).toHaveTextContent(/cloud/i);
    expect(screen.getByRole("status")).toHaveTextContent(/go to the provider/i);
  });

  it("shows the model it was told about rather than one it assumed", () => {
    render(<ProviderControl mode="local" model="" hasKey={false} onSelect={noop} onKey={noop} />);

    // An empty model name is "the brain has not said yet", not a default worth inventing.
    expect(screen.getByRole("status")).toHaveTextContent(/not reported/i);
  });
});

describe("selecting a mode", () => {
  it("cannot offer cloud when no key is stored", () => {
    render(
      <ProviderControl mode="local" model="gemma4:26b" hasKey={false} onSelect={noop} onKey={noop} />,
    );

    expect(screen.getByRole("button", { name: /use cloud/i })).toBeDisabled();
  });

  it("offers cloud once a key is stored", () => {
    // Offered, then confirmed. The second click is not ceremony: cloud mode starts sending recalled
    // vault notes off the machine, so the switch is a decision rather than a click. The
    // confirmation itself is covered in "moving to cloud" below.
    const onSelect = vi.fn();
    render(<ProviderControl mode="local" model="gemma4:26b" hasKey onSelect={onSelect} onKey={noop} />);

    fireEvent.click(screen.getByRole("button", { name: /use cloud/i }));
    fireEvent.click(screen.getByRole("button", { name: /Send my notes to Google/i }));

    expect(onSelect).toHaveBeenCalledWith("cloud");
  });

  it("always allows the way back to local", () => {
    const onSelect = vi.fn();
    render(<ProviderControl mode="cloud" model="gemini-2.0-flash" hasKey onSelect={onSelect} onKey={noop} />);

    fireEvent.click(screen.getByRole("button", { name: /use local/i }));

    expect(onSelect).toHaveBeenCalledWith("local");
  });
});

describe("the key", () => {
  it("is entered in a field that does not display it", () => {
    render(
      <ProviderControl mode="local" model="gemma4:26b" hasKey={false} onSelect={noop} onKey={noop} />,
    );

    expect(screen.getByLabelText(/no key is stored/i)).toHaveAttribute("type", "password");
  });

  it("is sent once and cleared from the field immediately", () => {
    const onKey = vi.fn();
    const { container } = render(
      <ProviderControl mode="local" model="gemma4:26b" hasKey={false} onSelect={noop} onKey={onKey} />,
    );
    const input = screen.getByLabelText(/no key is stored/i) as HTMLInputElement;

    fireEvent.change(input, { target: { value: KEY } });
    fireEvent.click(screen.getByRole("button", { name: /store key/i }));

    expect(onKey).toHaveBeenCalledWith(KEY);
    expect(input.value).toBe("");
    // Not merely blanked in the field: absent from the rendered document entirely.
    expect(container.innerHTML).not.toContain(KEY);
  });

  it("is not sent when the field is empty or whitespace", () => {
    const onKey = vi.fn();
    render(
      <ProviderControl mode="local" model="gemma4:26b" hasKey={false} onSelect={noop} onKey={onKey} />,
    );

    fireEvent.change(screen.getByLabelText(/no key is stored/i), { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: /store key/i }));

    expect(onKey).not.toHaveBeenCalled();
  });

  it("says a key is stored without showing any part of it", () => {
    const { container } = render(
      <ProviderControl mode="cloud" model="gemini-2.0-flash" hasKey onSelect={noop} onKey={noop} />,
    );

    expect(screen.getByText(/a key is stored/i)).toBeInTheDocument();
    // There is nothing to leak: the component is never given the value in the first place.
    expect(container.innerHTML).not.toContain(KEY);
  });
});

describe("moving to cloud", () => {
  function renderWithKey(onSelect: (mode: "local" | "cloud") => void) {
    render(
      <ProviderControl mode="local" model="gemma4:26b" hasKey onSelect={onSelect} onKey={noop} />,
    );
  }

  it("does not move the boundary on the first click", () => {
    // The decisive one. Recalled vault notes travel in cloud mode (SECURITY.md §11), so the switch
    // that starts that has to be a decision rather than a click.
    const onSelect = vi.fn();
    renderWithKey(onSelect);

    fireEvent.click(screen.getByRole("button", { name: "Use cloud" }));

    expect(onSelect).not.toHaveBeenCalled();
  });

  it("says the vault is what leaves, not just 'data'", () => {
    renderWithKey(noop);

    fireEvent.click(screen.getByRole("button", { name: "Use cloud" }));

    expect(screen.getByText(/Notes from your vault/i)).toBeInTheDocument();
  });

  it("labels the confirm button with what it does rather than with OK", () => {
    // "OK" is what people click without reading.
    renderWithKey(noop);

    fireEvent.click(screen.getByRole("button", { name: "Use cloud" }));

    expect(screen.getByRole("button", { name: /Send my notes to Google/i })).toBeInTheDocument();
  });

  it("moves the boundary only once the confirmation is answered", () => {
    const onSelect = vi.fn();
    renderWithKey(onSelect);

    fireEvent.click(screen.getByRole("button", { name: "Use cloud" }));
    fireEvent.click(screen.getByRole("button", { name: /Send my notes to Google/i }));

    expect(onSelect).toHaveBeenCalledWith("cloud");
  });

  it("leaves the boundary alone when the user backs out", () => {
    const onSelect = vi.fn();
    renderWithKey(onSelect);

    fireEvent.click(screen.getByRole("button", { name: "Use cloud" }));
    fireEvent.click(screen.getByRole("button", { name: "Stay local" }));

    expect(onSelect).not.toHaveBeenCalled();
    expect(screen.queryByText(/Notes from your vault/i)).not.toBeInTheDocument();
  });

  it("moves back to local without asking", () => {
    // Asymmetric on purpose: this direction narrows what leaves the machine, and nobody needs
    // talking out of that.
    const onSelect = vi.fn();
    render(
      <ProviderControl mode="cloud" model="gemini" hasKey onSelect={onSelect} onKey={noop} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Use local" }));

    expect(onSelect).toHaveBeenCalledWith("local");
  });
});
