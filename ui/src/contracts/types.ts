/**
 * TypeScript mirror of contracts/ws.schema.json.
 *
 * The schema is the single source of truth; this is a mirror of it, and where the two disagree the
 * schema is right and this file is a bug. `guards.ts` holds them together by running every
 * checked-in example through the guards.
 */

export const CONTRACT_VERSION = 1;

export type SensorSource = "pdh_english" | "win32_api" | "wmi" | "adlx" | "none";

/**
 * The honesty mechanism. Declared once by the system layer and forwarded to the UI, this is what
 * lets a null value be rendered as "requires kernel driver - not installed" rather than as an
 * unexplained blank.
 */
export interface SensorCapability {
  readonly field: string;
  readonly available: boolean;
  readonly source: SensorSource;
  readonly unavailable_reason: string | null;
}

export interface CpuPayload {
  readonly total_percent: number | null;
  /**
   * One entry per logical processor. Position IS the core's identity, and a null entry is a core
   * Windows had parked. Never compacted - see docs/CONTRACTS.md section 3.
   */
  readonly per_core_percent: readonly (number | null)[] | null;
  readonly frequency_mhz: number | null;
  readonly temperature_c: number | null;
}

export interface MemoryPayload {
  readonly used_bytes: number | null;
  readonly total_bytes: number | null;
  readonly commit_used_bytes: number | null;
  readonly commit_limit_bytes: number | null;
}

export interface GpuPayload {
  readonly utilization_percent: number | null;
  readonly vram_used_bytes: number | null;
  readonly vram_total_bytes: number | null;
  readonly temperature_c: number | null;
}

export interface TelemetryPayload {
  readonly seq: number;
  readonly sampled_at: string;
  readonly cpu: CpuPayload;
  readonly memory: MemoryPayload;
  readonly gpu: GpuPayload;
  readonly uptime_seconds: number | null;
}

interface Envelope<TType extends string, TPayload> {
  readonly v: number;
  readonly id: string;
  readonly ts: string;
  readonly type: TType;
  readonly payload: TPayload;
}

export type ServerHello = Envelope<
  "server.hello",
  {
    readonly component: "brain";
    readonly app_version: string;
    readonly poll_interval_ms: number;
    readonly system_connected: boolean;
    readonly sensors: readonly SensorCapability[];
  }
>;

export type SystemStatus = Envelope<
  "system.status",
  {
    readonly connected: boolean;
    readonly since: string;
    readonly reason: string | null;
    readonly sensors: readonly SensorCapability[];
  }
>;

export type TelemetrySample = Envelope<"telemetry.sample", TelemetryPayload>;

export type WsErrorCode =
  | "schema_violation"
  | "unsupported_version"
  | "handshake_required"
  | "system_unavailable"
  | "internal_error";

export type WsError = Envelope<
  "error",
  {
    readonly code: WsErrorCode;
    readonly message: string;
    readonly in_reply_to: string | null;
  }
>;

/** Everything the brain may send to the UI. */
export type ServerMessage = ServerHello | SystemStatus | TelemetrySample | WsError;
