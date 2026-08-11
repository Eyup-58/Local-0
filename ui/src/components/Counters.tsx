/**
 * What the UI threw away.
 *
 * The contract requires a failed message to be dropped *and counted*: a silent drop is
 * indistinguishable from a peer that never sent anything. Showing the counts means a contract
 * mismatch surfaces as a number that climbs, rather than as telemetry that quietly thins out.
 */

interface CountersProps {
  readonly droppedFrames: number;
  readonly missedSamples: number;
  readonly lastError: string | null;
}

export function Counters({ droppedFrames, missedSamples, lastError }: CountersProps) {
  return (
    <section className="counters">
      <Counter label="Frames refused" value={droppedFrames} />
      <Counter label="Samples missed" value={missedSamples} />
      {lastError !== null ? (
        <span className="counter">
          Last error <span className="counter__value">{lastError}</span>
        </span>
      ) : null}
    </section>
  );
}

function Counter({ label, value }: { readonly label: string; readonly value: number }) {
  return (
    <span className="counter">
      {label}
      <span className={value > 0 ? "counter__value counter__value--alarm" : "counter__value"}>{value}</span>
    </span>
  );
}
