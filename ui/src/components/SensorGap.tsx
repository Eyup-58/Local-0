/**
 * A reading that is not there, rendered as deliberately as one that is.
 *
 * The reason comes from the system layer's own declaration and is shown verbatim, as text. It is
 * interpolated into JSX, which escapes it - no markdown renderer is imported on this path and
 * dangerouslySetInnerHTML is banned repository-wide. That is a security control, not a styling
 * choice: see docs/SECURITY.md.
 */

interface SensorGapProps {
  /** What the user was looking for, e.g. "CPU temperature". */
  readonly label: string;
  /** The sidecar's words for why it is unavailable. */
  readonly reason: string;
  readonly inline?: boolean;
}

export function SensorGap({ label, reason, inline = false }: SensorGapProps) {
  return (
    <div className={inline ? "void void--inline" : "void"} role="note">
      <span className="void__label">{label} — unavailable</span>
      <p className="void__reason">{reason}</p>
    </div>
  );
}
