/**
 * The button that turns approval off, and the banner that says so while it is off.
 *
 * The user asked for this and chose its scope explicitly: no exceptions, and remembered across
 * restarts. This component does not argue with that. What it does is make the state impossible to
 * be wrong about, because the cost of not noticing is every operation running unattended.
 *
 * Two asymmetries, both deliberate:
 *
 * * **Arming asks once; disarming does not.** Making the safe direction slower would be an argument
 *   for the dangerous one.
 * * **The on-state is a banner, not a badge.** It sits in the same visual register as the panel's
 *   "not live" marker, because both answer the same question: is what you are looking at what you
 *   think it is?
 */

import { useState } from "react";

interface TrustControlProps {
  readonly enabled: boolean;
  readonly onChange: (enabled: boolean) => void;
}

export function TrustControl({ enabled, onChange }: TrustControlProps) {
  const [confirming, setConfirming] = useState(false);

  if (enabled) {
    return (
      <section className="trust-banner" role="status">
        <div>
          <p className="trust-banner__title">Approval is off</p>
          <p className="trust-banner__note">
            Operations are running without asking you first — including ones that came from content
            Local Zero read rather than from you. Every one is recorded in the audit log.
          </p>
        </div>
        <button type="button" className="button button--reject" onClick={() => onChange(false)}>
          Turn approval back on
        </button>
      </section>
    );
  }

  if (confirming) {
    return (
      <section className="trust-confirm">
        <p className="trust-confirm__note">
          Local Zero will carry out every operation without asking, including deletions and
          operations that came from content it read rather than from you. This stays off until you
          turn it back on, even after a restart.
        </p>
        <div className="trust-confirm__controls">
          <button
            type="button"
            className="button button--reject"
            onClick={() => setConfirming(false)}
          >
            Cancel
          </button>
          <button
            type="button"
            className="button"
            onClick={() => {
              setConfirming(false);
              onChange(true);
            }}
          >
            Yes, turn it off
          </button>
        </div>
      </section>
    );
  }

  return (
    <section className="trust-control">
      <button type="button" className="button" onClick={() => setConfirming(true)}>
        Turn approval off
      </button>
    </section>
  );
}
