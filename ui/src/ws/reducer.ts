/**
 * The UI's state machine, kept separate from the socket that drives it.
 *
 * Everything that decides what the user sees - whether data is live, whether a frame was dropped,
 * whether samples went missing - is a pure function here, so it can be tested without a WebSocket
 * and without waiting a second per tick.
 */

import { parseServerMessage } from "../contracts/guards";
import type {
  ApprovalRequest,
  MemoryStatus,
  ProviderMode,
  SensorCapability,
  TelemetryPayload,
  ToolLog,
  TurnStateName,
} from "../contracts/types";

export type LinkState = "connecting" | "open" | "closed";

export interface TelemetryState {
  readonly link: LinkState;
  /** Whether the brain currently holds a live pipe to the system layer. */
  readonly systemConnected: boolean;
  /** User-safe prose explaining a disconnect, or null while connected. */
  readonly reason: string | null;
  readonly sensors: readonly SensorCapability[];
  readonly sample: TelemetryPayload | null;
  /** When the last sample reached this tab, in ms since epoch. Drives the staleness marker. */
  readonly receivedAt: number | null;
  readonly pollIntervalMs: number;
  /** Frames refused by the guards. Dropped and counted, never partially applied. */
  readonly droppedFrames: number;
  /** Samples the brain or the sidecar discarded, counted from gaps in seq. */
  readonly missedSamples: number;
  readonly lastError: string | null;
  readonly lastSeq: number | null;
  /**
   * The approval currently awaiting an answer, or null.
   *
   * One at a time on purpose. A queue of dialogs is a queue of things to click through, and
   * clicking through is the failure mode the approval gate exists to prevent.
   */
  readonly pendingApproval: ApprovalRequest["payload"] | null;
  /**
   * Whether the user has turned approval off. Reported by the brain, never assumed by the UI - the
   * UI showing "off" while the brain has it on would be the more dangerous of the two mistakes.
   */
  readonly trustEnabled: boolean;
  /**
   * The network boundary, as reported by the brain. `local` sends nothing off this machine.
   *
   * Same rule as trustEnabled: never assumed by the UI. A tab showing `local` while the brain has
   * `cloud` in force would be the more dangerous of the two mistakes.
   */
  readonly providerMode: ProviderMode;
  readonly providerModel: string;
  /** Whether a cloud key is stored. The key itself never reaches this state, or any other. */
  readonly hasKey: boolean;
  /** What the brain has loaded from the vault. `enabled: false` means memory is off, not empty. */
  readonly memory: MemoryStatus["payload"];
  /**
   * What the brain is doing in the current turn, as the brain reported it.
   *
   * Nothing in this UI advances it. No timer, no "it has been 3 seconds so it must be speaking by
   * now", no transition inferred from a tool log arriving. If the brain stops sending turn.state
   * the core simply holds its last reported state, which is the honest thing for it to do.
   */
  readonly turn: TurnStateName;
  /** The brain's own words, or null when it has nothing to say. Null renders as nothing. */
  readonly caption: string | null;
  /** A short label for what the turn is about, or null. */
  readonly turnDetail: string | null;
  readonly turnSince: string | null;
  /** Turns the brain reported completing: a non-idle state that came back to idle. */
  readonly turnCount: number;
  /** Tool calls the brain reported starting. Counts running lines, not ok/failed closers. */
  readonly toolCalls: number;
  /** Newest first, capped at TOOL_LOG_LIMIT. Only ever what the brain actually reported running. */
  readonly toolLog: readonly ToolLog["payload"][];
}

/**
 * How many tool log lines the dock keeps.
 *
 * The audit log is the complete record; this is a viewport onto the recent end of it. Keeping every
 * line for the life of the tab would grow without bound in exactly the sessions that are already
 * busiest.
 */
export const TOOL_LOG_LIMIT = 40;

export const initialState: TelemetryState = {
  link: "connecting",
  systemConnected: false,
  reason: null,
  sensors: [],
  sample: null,
  receivedAt: null,
  pollIntervalMs: 1000,
  droppedFrames: 0,
  missedSamples: 0,
  lastError: null,
  lastSeq: null,
  pendingApproval: null,
  // Off until the brain says otherwise. Defaulting to on would mean a UI that had not yet heard
  // from the brain displayed the permissive state, which is the wrong way round to be wrong.
  trustEnabled: false,
  // Local until the brain says otherwise, for the same reason trust starts off: the state before
  // anything is known has to be the one that sends nothing.
  providerMode: "local",
  providerModel: "",
  hasKey: false,
  // Off until the brain says otherwise, like everything else here. A panel claiming memory is
  // loaded before hearing from the brain would be inventing the one number nobody can check.
  memory: {
    enabled: false,
    vault: null,
    notes: 0,
    chunks: 0,
    embedded_chunks: 0,
    last_indexed_at: null,
    embeddings_available: false,
  },
  // Idle and silent until the brain says otherwise, for the same reason trust starts off: the state
  // before anything is known has to be the one that claims the least. A core that opened in
  // "thinking" would be asserting the brain was working before it had heard from it at all.
  turn: "idle",
  caption: null,
  turnDetail: null,
  turnSince: null,
  turnCount: 0,
  toolCalls: 0,
  toolLog: [],
};

export type TelemetryAction =
  | { readonly kind: "socket-opened" }
  | { readonly kind: "socket-closed" }
  | { readonly kind: "frame"; readonly raw: string; readonly now: number };

export function telemetryReducer(state: TelemetryState, action: TelemetryAction): TelemetryState {
  switch (action.kind) {
    case "socket-opened":
      return { ...state, link: "open", lastError: null };

    case "socket-closed":
      // The socket going away says nothing new about the sidecar, but it does mean nothing on
      // screen is live any more. Values are kept so the panel does not flash empty; the staleness
      // marker is what tells the user not to trust them.
      return { ...state, link: "closed", systemConnected: false, lastSeq: null };

    case "frame":
      return applyFrame(state, action.raw, action.now);
  }
}

function applyFrame(state: TelemetryState, raw: string, now: number): TelemetryState {
  const result = parseServerMessage(raw);
  if (!result.ok) {
    return { ...state, droppedFrames: state.droppedFrames + 1, lastError: result.reason };
  }

  const message = result.message;

  switch (message.type) {
    case "server.hello":
      return {
        ...state,
        link: "open",
        systemConnected: message.payload.system_connected,
        pollIntervalMs: message.payload.poll_interval_ms,
        sensors: message.payload.sensors.length > 0 ? message.payload.sensors : state.sensors,
      };

    case "system.status":
      return {
        ...state,
        systemConnected: message.payload.connected,
        reason: message.payload.reason,
        // An empty list means the sidecar is gone, not that it declared nothing. The last
        // declaration is kept so every gap on screen can still say why it is a gap.
        sensors: message.payload.sensors.length > 0 ? message.payload.sensors : state.sensors,
        // seq restarts at 0 on each pipe connection, so a reconnect is not a missed sample.
        lastSeq: null,
      };

    case "telemetry.sample": {
      const seq = message.payload.seq;
      const missed = state.lastSeq === null || seq <= state.lastSeq ? 0 : seq - state.lastSeq - 1;

      return {
        ...state,
        sample: message.payload,
        receivedAt: now,
        lastSeq: seq,
        missedSamples: state.missedSamples + missed,
      };
    }

    case "error":
      return { ...state, lastError: message.payload.message };

    case "approval.request":
      return { ...state, pendingApproval: message.payload };

    case "approval.resolved":
      // Only clears the dialog it names. A resolution for some other request must not dismiss the
      // one the user is currently reading, or the thing they approve is not the thing they saw.
      return state.pendingApproval?.request_id === message.payload.request_id
        ? { ...state, pendingApproval: null }
        : state;

    case "trust.status":
      return { ...state, trustEnabled: message.payload.enabled };

    case "provider.status":
      return {
        ...state,
        providerMode: message.payload.mode,
        providerModel: message.payload.model,
        hasKey: message.payload.has_key,
      };

    case "memory.status":
      return { ...state, memory: message.payload };

    case "turn.state":
      return {
        ...state,
        turn: message.payload.state,
        caption: message.payload.caption,
        turnDetail: message.payload.detail,
        turnSince: message.payload.since,
        // A turn is complete when a reported non-idle state comes back to idle. Nothing here
        // decides a turn ended on its own; only the brain's own frames move the count.
        turnCount:
          state.turn !== "idle" && message.payload.state === "idle" ? state.turnCount + 1 : state.turnCount,
      };

    case "tool.log":
      // Newest first, and capped. The cap is what stops a long session from turning the dock into
      // an unbounded array the tab has to keep re-rendering; what fell off the end is in the audit
      // log, which is the record that is supposed to be complete.
      return {
        ...state,
        toolLog: [message.payload, ...state.toolLog].slice(0, TOOL_LOG_LIMIT),
        // The running line is the "a call started" one; ok and failed lines close it. Counting
        // starts keeps the count a count of calls rather than of log lines.
        toolCalls: message.payload.status === "running" ? state.toolCalls + 1 : state.toolCalls,
      };
  }
}

/**
 * True when the last sample is too old to be presented as current.
 *
 * A dead sidecar and an idle machine look identical if you only watch the numbers, so this is the
 * difference between a panel that degrades honestly and one that freezes on its last reading and
 * says nothing. Two missed ticks is the threshold: one late tick is scheduling noise.
 */
export function isStale(state: TelemetryState, now: number): boolean {
  if (!state.systemConnected || state.link !== "open") return true;
  if (state.receivedAt === null) return true;

  return now - state.receivedAt > state.pollIntervalMs * 2;
}
