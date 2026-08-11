/**
 * The hero: whether anything on this page can be believed right now.
 *
 * Deliberately not the biggest number on the panel. A telemetry view whose numbers stop changing
 * looks identical whether the machine is idle or the sidecar is dead, and resolving that ambiguity
 * is the single job this product claims to do. So it leads, it is wide, and when the answer is no
 * the whole rail takes the hatch treatment used for every other absent value.
 */

import { formatAge } from "../format";
import type { TelemetryState } from "../ws/reducer";

interface LinkStatusProps {
  readonly state: TelemetryState;
  readonly now: number;
  readonly stale: boolean;
}

export function LinkStatus({ state, now, stale }: LinkStatusProps) {
  return (
    <section className={stale ? "rail rail--stale" : "rail"} aria-live="polite">
      <p className="rail__verdict">{stale ? "Not live" : "Live"}</p>

      <div className="rail__facts">
        <Fact label="Last sample" value={formatAge(state.receivedAt, now)} />
        <Fact label="Sequence" value={state.lastSeq === null ? "—" : String(state.lastSeq)} />
        <Fact label="Interval" value={`${state.pollIntervalMs} ms`} />
        <Fact label="Socket" value={state.link} />
      </div>

      {stale ? <p className="rail__reason">{explain(state)}</p> : null}
    </section>
  );
}

function Fact({ label, value }: { readonly label: string; readonly value: string }) {
  return (
    <span className="rail__fact">
      <span className="eyebrow">{label}</span>
      <span className="rail__value">{value}</span>
    </span>
  );
}

/**
 * Says what is wrong and what it means for the numbers on screen.
 *
 * The brain's own prose is preferred wherever it exists, because it knows which of several
 * failures actually happened. The fallbacks cover the cases it has no opinion about - the socket
 * being down is something only this tab can see.
 */
function explain(state: TelemetryState): string {
  if (state.link !== "open") {
    return "The brain is not reachable on this machine. Values below are the last ones received and are no longer being updated.";
  }

  if (state.reason !== null) {
    return state.reason;
  }

  if (!state.systemConnected) {
    return "The system layer has not connected. No telemetry is available.";
  }

  return "Samples have stopped arriving. Values below are the last ones received and are no longer being updated.";
}
