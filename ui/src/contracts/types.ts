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

/**
 * What the brain is doing in the current conversational turn.
 *
 * Reported, never inferred. There is no timer in this UI that advances it and no elapsed-time
 * heuristic that guesses the next one — a panel that decided for itself the brain was "probably
 * speaking by now" would be narrating, which is the one thing this HUD does not do.
 */
export type TurnStateName = "idle" | "listening" | "thinking" | "tool_running" | "speaking";

export type TurnState = Envelope<
  "turn.state",
  {
    readonly state: TurnStateName;
    readonly since: string;
    /**
     * What the brain is saying, in its own words, or null when it has nothing to say. Null is a
     * gap and it renders as one: no greeting, no status line, no filler substituted for silence.
     */
    readonly caption: string | null;
    /** A short label for what the state is about, or null. Same rule — null renders as nothing. */
    readonly detail: string | null;
  }
>;

/** `running` is not terminal. Nothing may treat it as a finished call. */
export type ToolLogStatus = "running" | "ok" | "failed";

export type ToolLog = Envelope<
  "tool.log",
  {
    readonly at: string;
    readonly capability: string;
    /**
     * May paraphrase content the brain fetched, so this is untrusted text by docs/SECURITY.md
     * section 2. It is safe to display because React renders it as text and this repository
     * imports no markdown or HTML renderer — `bans.test.ts` is what keeps that true.
     */
    readonly message: string;
    readonly status: ToolLogStatus;
  }
>;

/**
 * What a capability that reads something found.
 *
 * `tool.log` says a capability finished; this carries what it produced, because a process list or a
 * game library is a table and paraphrasing one into a log line throws it away.
 *
 * **Every cell is a string and every cell is untrusted.** These values come from outside — a
 * process name, a game title, a path someone else chose — so they are untrusted text by
 * docs/SECURITY.md section 2. They are safe to display for exactly one reason: React renders them
 * as text nodes and this repository imports no markdown or HTML renderer, which `bans.test.ts`
 * keeps true. Nothing may treat a cell as an instruction, and nothing sends one back to the brain.
 */
export type CapabilityResult = Envelope<
  "capability.result",
  {
    readonly at: string;
    readonly capability: string;
    readonly columns: readonly string[];
    /** Every row is exactly `columns.length` long; the brain refuses a ragged table. */
    readonly rows: readonly (readonly string[])[];
    /** True when rows were dropped to fit. Reported, never inferred from `rows.length`. */
    readonly truncated: boolean;
  }
>;

/**
 * Something the user typed, on its way to the planner.
 *
 * The one message this tab sends whose payload reaches a language model, and the trusted half of
 * SECURITY.md §2 — the planner may see what the user typed and nothing else. Nothing that arrived
 * over this socket may be sent back out as one of these.
 */
export type TurnRequest = Envelope<"turn.request", { readonly text: string }>;

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
  | MemoryStatus
  | TurnState
  | ToolLog
  | CapabilityResult;
