/**
 * A proportion bar. Hatched when the proportion is unknown.
 *
 * An empty bar and an unknown bar look nothing alike here on purpose: an empty track reads as
 * "zero", which is a claim this panel is not allowed to make about a value it does not have.
 */

interface GaugeProps {
  /** 0 to 1, or null when either side of the ratio is unavailable. */
  readonly value: number | null;
  readonly label: string;
  readonly tone?: "signal" | "cool";
}

export function Gauge({ value, label, tone = "signal" }: GaugeProps) {
  if (value === null) {
    return <div className="gauge gauge--void" role="img" aria-label={`${label}: unavailable`} />;
  }

  const percent = Math.round(value * 100);

  return (
    <div
      className="gauge"
      role="meter"
      aria-label={label}
      aria-valuenow={percent}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div
        className={tone === "cool" ? "gauge__fill gauge__fill--cool" : "gauge__fill"}
        style={{ width: `${percent}%` }}
      />
    </div>
  );
}
