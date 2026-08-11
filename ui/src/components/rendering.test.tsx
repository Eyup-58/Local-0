/**
 * What the panel actually puts on screen for a value it does not have.
 *
 * The M1 exit criterion is that an unavailable sensor arrives as null with a populated reason and
 * "the UI renders a labelled gap rather than a zero". These are the tests for that sentence.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CoreGrid } from "./CoreGrid";
import { DeclaredGaps } from "./DeclaredGaps";
import { Metric } from "./Metric";
import { SensorGap } from "./SensorGap";

describe("a missing measurement", () => {
  it("shows the reason instead of a number", () => {
    render(
      <Metric
        label="Package temperature"
        value={null}
        unit="°C"
        unavailableReason="requires kernel driver - not installed"
      />,
    );

    expect(screen.getByText("requires kernel driver - not installed")).toBeInTheDocument();
  });

  it("never renders a zero in place of a missing value", () => {
    // The failure this guards against is a panel that shows 0 °C for a sensor it cannot read - a
    // number the user would act on. See CLAUDE.md invariant 10.
    const { container } = render(
      <Metric label="Package temperature" value={null} unit="°C" unavailableReason="requires kernel driver" />,
    );

    expect(container.textContent).not.toMatch(/\b0\b/);
  });

  it("shows the number when there is one", () => {
    render(<Metric label="Total load" value="12.4" unit="%" unavailableReason="unused" />);

    expect(screen.getByText("12.4")).toBeInTheDocument();
  });
});

describe("markup in a reason", () => {
  const injected = '<img src=x onerror="alert(1)"> [click](javascript:alert(2))';

  it("renders as literal text, not as elements", () => {
    // The reason travels from the system layer as prose. If it could become markup, a string
    // chosen by something upstream would control what the user sees.
    const { container } = render(<SensorGap label="GPU temperature" reason={injected} />);

    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("a")).toBeNull();
    expect(screen.getByText(injected)).toBeInTheDocument();
  });

  it("renders as literal text in the declared list too", () => {
    const sensors = [
      { field: "gpu.temperature_c", available: false, source: "none" as const, unavailable_reason: injected },
    ];

    const { container } = render(<DeclaredGaps sensors={sensors} />);

    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText(injected)).toBeInTheDocument();
  });
});

describe("the core grid", () => {
  it("draws one cell per logical processor, parked or not", () => {
    const { container } = render(<CoreGrid cores={[10, null, 30, null, 50]} unavailableReason="unused" />);

    expect(container.querySelectorAll(".core")).toHaveLength(5);
  });

  it("marks a parked core rather than drawing it as idle", () => {
    // An empty bar and a parked core are different facts, and only one of them is a measurement.
    const { container } = render(<CoreGrid cores={[10, null, 30]} unavailableReason="unused" />);

    expect(container.querySelectorAll(".core--parked")).toHaveLength(1);
  });

  it("keeps a parked core in its own position", () => {
    // Position is the core's identity. If the grid compacted the array, core 2's load would be
    // drawn in core 1's cell.
    const { container } = render(<CoreGrid cores={[10, null, 30]} unavailableReason="unused" />);
    const cells = container.querySelectorAll(".core");

    expect(cells[1]?.className).toContain("core--parked");
    expect(cells[2]?.getAttribute("title")).toContain("Core 2");
  });

  it("says how many cores are parked", () => {
    render(<CoreGrid cores={[10, null, 30, null]} unavailableReason="unused" />);

    expect(screen.getByText(/parked — 2 of 4/)).toBeInTheDocument();
  });

  it("falls back to a labelled gap when the whole array is unavailable", () => {
    render(<CoreGrid cores={null} unavailableReason="the instance set was incomplete" />);

    expect(screen.getByText("the instance set was incomplete")).toBeInTheDocument();
  });
});

describe("the declared gaps list", () => {
  const sensors = [
    { field: "cpu.total_percent", available: true, source: "pdh_english" as const, unavailable_reason: null },
    {
      field: "cpu.temperature_c",
      available: false,
      source: "none" as const,
      unavailable_reason: "requires kernel driver - not installed",
    },
  ];

  it("lists only what is unavailable, with its reason", () => {
    render(<DeclaredGaps sensors={sensors} />);

    expect(screen.getByText("cpu.temperature_c")).toBeInTheDocument();
    expect(screen.getByText("requires kernel driver - not installed")).toBeInTheDocument();
    expect(screen.queryByText("cpu.total_percent")).not.toBeInTheDocument();
  });

  it("renders nothing when every sensor is available", () => {
    const { container } = render(<DeclaredGaps sensors={[sensors[0]!]} />);

    expect(container).toBeEmptyDOMElement();
  });
});
