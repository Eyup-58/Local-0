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
  /**
   * The selected model layer cannot be used — today, only "cloud was chosen and no key is stored".
   * Distinct from system_unavailable, which is about the sidecar: a user who confuses "the sensors
   * are down" with "your key is missing" goes looking in the wrong place.
   */
  | "provider_unavailable"
  | "internal_error";

export type WsError = Envelope<
  "error",
  {
    readonly code: WsErrorCode;
    readonly message: string;
    readonly in_reply_to: string | null;
  }
>;

export type SideEffect = "read" | "write" | "destructive";

export type Origin = "user_direct" | "untrusted_content";

export type ApprovalOutcome = "approved" | "rejected" | "expired" | "auto_approved";

/**
 * One resolved argument as it arrives for display.
 *
 * Scalars only, and the schema holds the same line. A nested structure would be a rendering
 * decision, and rendering decisions are where markup gets back into a payload the user is reading
 * in order to decide something.
 */
export type ResolvedArgument = string | number | boolean | null;

export type ApprovalRequest = Envelope<
  "approval.request",
  {
    readonly request_id: string;
    readonly capability: string;
    /** Post-validation, post-canonicalisation: what will run, not what was asked for. */
    readonly resolved_args: Readonly<Record<string, ResolvedArgument>>;
    /** Computed by the brain. An empty array means nothing is touched, not that it is unknown. */
    readonly affected_paths: readonly string[];
    readonly side_effect: SideEffect;
    readonly origin: Origin;
  }
>;

export type ApprovalResolved = Envelope<
  "approval.resolved",
  {
    readonly request_id: string;
    readonly outcome: ApprovalOutcome;
  }
>;

export type TrustStatus = Envelope<
  "trust.status",
  {
    /**
     * When true, approval is bypassed for every invocation regardless of side_effect or origin.
     * The guard's other steps still run: trust mode skips the approval gate, not containment.
     */
    readonly enabled: boolean;
    readonly since: string;
  }
>;

export type ProviderMode = "local" | "cloud";

export type ProviderStatus = Envelope<
  "provider.status",
  {
    /** local sends nothing off this machine; cloud additionally permits outbound. */
    readonly mode: ProviderMode;
    readonly model: string;
    /**
     * Whether a key is stored — never the key, never a prefix of it, never its length. There is a
     * rejected contract example holding that line.
     */
    readonly has_key: boolean;
    readonly since: string;
  }
>;

export type MemoryStatus = Envelope<
  "memory.status",
  {
    /** False when no vault is configured, or the configured one is not there. An ordinary state. */
    readonly enabled: boolean;
    readonly vault: string | null;
    readonly notes: number;
    readonly chunks: number;
    readonly embedded_chunks: number;
    readonly last_indexed_at: string | null;
    /**
     * False means search is ranking by keyword alone because no embedding model answered.
     * Reported rather than inferred — search that quietly gets worse is the failure nobody notices.
     */
    readonly embeddings_available: boolean;
  }
>;

/** Everything the brain may send to the UI. */
export type ServerMessage =
  | ServerHello
  | SystemStatus
  | TelemetrySample
  | WsError
  | ApprovalRequest
  | ApprovalResolved
  | TrustStatus
  | ProviderStatus
  | MemoryStatus;
