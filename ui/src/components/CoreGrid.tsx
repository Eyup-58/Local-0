/**
 * One cell per logical processor, in core order.
 *
 * Position is the core's identity: cell 20 is core 20, always. A parked core keeps its cell and
 * shows the hatch rather than an empty bar, because "asleep" and "idle" are different facts and
 * only one of them is a measurement. Windows parks the E-cores of this machine constantly, so this
 * is the normal state of the grid rather than an edge case.
 *
 * The array is never compacted upstream either - see docs/CONTRACTS.md section 3.
 */

import { SensorGap } from "./SensorGap";

interface CoreGridProps {
  readonly cores: readonly (number | null)[] | null;
  readonly unavailableReason: string;
}

export function CoreGrid({ cores, unavailableReason }: CoreGridProps) {
  if (cores === null) {
    return <SensorGap label="Per-core load" reason={unavailableReason} />;
  }

  const parked = cores.filter((core) => core === null).length;

  return (
    <div className="metric">
      <span className="eyebrow">Per-core load — {cores.length} logical processors</span>

      <div className="cores">
        {cores.map((core, index) => (
          <CoreCell key={index} core={index} percent={core} />
        ))}
      </div>

      <p className="cores__legend">
        <span className="legend">
          <span className="legend__swatch" aria-hidden="true" />
          reporting
        </span>
        <span className="legend">
          <span className="legend__swatch legend__swatch--parked" aria-hidden="true" />
          parked — {parked} of {cores.length}
        </span>
      </p>
    </div>
  );
}

function CoreCell({ core, percent }: { readonly core: number; readonly percent: number | null }) {
  if (percent === null) {
    return <div className="core core--parked" title={`Core ${core}: parked, no utilization reported`} />;
  }

  return (
    <div className="core" title={`Core ${core}: ${percent.toFixed(1)}%`}>
      <div className="core__fill" style={{ height: `${percent}%` }} />
    </div>
  );
}
