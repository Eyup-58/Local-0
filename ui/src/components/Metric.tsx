/**
 * One measurement, or the labelled gap where it would have been.
 *
 * A null value never becomes a zero, a dash that could be mistaken for one, or a dimmed last
 * reading. It becomes a hatched void carrying the reason - see CLAUDE.md invariant 10.
 */

import { SensorGap } from "./SensorGap";

interface MetricProps {
  readonly label: string;
  /** Pre-formatted, or null when the sensor had nothing to report. */
  readonly value: string | null;
  readonly unit?: string;
  /** Extra context under the number, e.g. "of 63.8 GiB". */
  readonly detail?: string | null;
  /** Why the value is missing. Shown verbatim when value is null. */
  readonly unavailableReason: string;
  /** Highlights the number in the instrument colour. Used for the headline metric of a panel. */
  readonly emphasis?: boolean;
}

export function Metric({ label, value, unit, detail, unavailableReason, emphasis = false }: MetricProps) {
  if (value === null) {
    return <SensorGap label={label} reason={unavailableReason} />;
  }

  return (
    <div className="metric">
      <span className="eyebrow">{label}</span>
      <span className="metric__line">
        <span className={emphasis ? "metric__value metric__value--signal" : "metric__value"}>{value}</span>
        {unit ? <span className="metric__unit">{unit}</span> : null}
      </span>
      {detail ? <span className="metric__sub">{detail}</span> : null}
    </div>
  );
}
