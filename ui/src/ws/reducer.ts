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
}

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
