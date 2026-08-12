/**
 * The memory panel.
 *
 * Three claims have to be checkable here, because each is something the rest of the product asserts
 * and cannot demonstrate: that memory is on at all, which vault it is reading, and whether search is
 * ranking by meaning or has quietly fallen back to keywords.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { MemoryStatus } from "../contracts/types";
import { MemoryControl } from "./MemoryControl";

const ON: MemoryStatus["payload"] = {
  enabled: true,
  vault: "E:\\Obsidian\\CaseLocal0\\Local 0",
  notes: 12,
  chunks: 40,
  embedded_chunks: 40,
  last_indexed_at: "2026-08-12T19:01:58.220Z",
  embeddings_available: true,
};

const OFF: MemoryStatus["payload"] = {
  enabled: false,
  vault: null,
  notes: 0,
  chunks: 0,
  embedded_chunks: 0,
  last_indexed_at: null,
  embeddings_available: false,
};

function noop() {}

describe("when no vault is configured", () => {
  it("says memory is off rather than showing an empty vault", () => {
    // Zero notes because there is no vault, and zero notes because the vault is empty, are
    // different facts. Only one of them is fixed by writing a note.
    render(<MemoryControl status={OFF} onReindex={noop} />);

    expect(screen.getByRole("status")).toHaveTextContent(/memory off/i);
    expect(screen.getByRole("status")).toHaveTextContent(/no vault is configured/i);
  });

  it("says the rest of the product is unaffected", () => {
    render(<MemoryControl status={OFF} onReindex={noop} />);

    expect(screen.getByRole("status")).toHaveTextContent(/everything else works without one/i);
  });

  it("offers no rescan, because there is nothing to scan", () => {
    render(<MemoryControl status={OFF} onReindex={noop} />);

    expect(screen.queryByRole("button", { name: /rescan/i })).toBeNull();
  });
});

describe("when a vault is loaded", () => {
  it("reports the counts and the vault in full", () => {
    render(<MemoryControl status={ON} onReindex={noop} />);

    expect(screen.getByRole("status")).toHaveTextContent(/12 notes/);
    expect(screen.getByText(ON.vault!)).toBeInTheDocument();
  });

  it("names the degraded search mode rather than hiding it", () => {
    render(<MemoryControl status={{ ...ON, embeddings_available: false }} onReindex={noop} />);

    expect(screen.getByText(/keyword only/i)).toBeInTheDocument();
  });

  it("says when search is ranking by meaning", () => {
    render(<MemoryControl status={ON} onReindex={noop} />);

    expect(screen.getByText(/by meaning and keyword/i)).toBeInTheDocument();
  });

  it("asks for a rescan without naming a vault", () => {
    const onReindex = vi.fn();
    render(<MemoryControl status={ON} onReindex={onReindex} />);

    fireEvent.click(screen.getByRole("button", { name: /rescan/i }));

    expect(onReindex).toHaveBeenCalledWith();
  });

  it("does not claim a scan that has not happened", () => {
    render(<MemoryControl status={{ ...ON, last_indexed_at: null }} onReindex={noop} />);

    expect(screen.getByRole("status")).toHaveTextContent(/not scanned yet/i);
  });
});
