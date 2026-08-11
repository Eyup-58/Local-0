/**
 * Value formatting.
 *
 * Every function here takes a nullable and returns null for null. Nothing invents a display value
 * for a missing one - that decision belongs to the component, which renders a labelled gap.
 */

const BYTES_PER_GIB = 1024 ** 3;

export function formatPercent(value: number | null): string | null {
  return value === null ? null : value.toFixed(1);
}

export function formatGibibytes(bytes: number | null): string | null {
  return bytes === null ? null : (bytes / BYTES_PER_GIB).toFixed(1);
}

export function formatMegahertz(value: number | null): string | null {
  return value === null ? null : Math.round(value).toLocaleString("en-US");
}

/** Uptime as a coarse duration. Seconds are noise at this scale. */
export function formatUptime(seconds: number | null): string | null {
  if (seconds === null) return null;

  const days = Math.floor(seconds / 86_400);
  const hours = Math.floor((seconds % 86_400) / 3_600);
  const minutes = Math.floor((seconds % 3_600) / 60);

  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

/** How long ago a sample arrived, for the liveness readout. */
export function formatAge(receivedAt: number | null, now: number): string {
  if (receivedAt === null) return "never";

  const seconds = Math.max(0, Math.round((now - receivedAt) / 1000));
  if (seconds < 1) return "just now";
  if (seconds < 60) return `${seconds}s ago`;

  return `${Math.floor(seconds / 60)}m ago`;
}

/** The share of a total, as a 0-1 fraction, or null when either side is unavailable. */
export function fraction(used: number | null, total: number | null): number | null {
  if (used === null || total === null || total <= 0) return null;
  return Math.min(1, Math.max(0, used / total));
}
