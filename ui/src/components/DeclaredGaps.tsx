/**
 * Everything the system layer said up front it will never be able to read, with its reasons.
 *
 * Most panels omit what they cannot measure, which leaves the user to wonder whether a number is
 * missing or the feature is. Publishing the list turns the contract's honesty mechanism into
 * something visible: CPU temperature is absent because no kernel driver is loaded, and that is a
 * decision worth showing rather than a hole worth hiding.
 */

import type { SensorCapability } from "../contracts/types";

interface DeclaredGapsProps {
  readonly sensors: readonly SensorCapability[];
}

export function DeclaredGaps({ sensors }: DeclaredGapsProps) {
  const unavailable = sensors.filter((sensor) => !sensor.available);

  if (unavailable.length === 0) {
    return null;
  }

  return (
    <section className="declared">
      <div className="panel__head">
        <p className="eyebrow">Declared unavailable — {unavailable.length}</p>
        <p className="masthead__note">Reported once by the system layer at connect time</p>
      </div>

      <ul className="declared__list">
        {unavailable.map((sensor) => (
          <li className="declared__item" key={sensor.field}>
            <span className="declared__field">{sensor.field}</span>
            <p className="declared__reason">{sensor.unavailable_reason}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}
