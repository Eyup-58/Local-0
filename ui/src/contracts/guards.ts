/**
 * Validation for inbound WebSocket frames.
 *
 * The rule from docs/CONTRACTS.md section 2 is that a frame is validated **before any field of it
 * is read**. A frame that fails validation has no readable fields, so nothing here reaches into a
 * message to explain what was wrong with it beyond naming the field.
 *
 * Hand-written rather than generated or schema-driven at runtime: the UI ships no validator
 * dependency, and every rule that matters is visible in one file that a reviewer can read against
 * contracts/ws.schema.json.
 */

import {
  CONTRACT_VERSION,
  type ApprovalOutcome,
  type CpuPayload,
  type GpuPayload,
  type MemoryPayload,
  type Origin,
  type ProviderMode,
  type ResolvedArgument,
  type SensorCapability,
  type SensorSource,
  type ServerMessage,
  type SideEffect,
  type TelemetryPayload,
  type ToolLogStatus,
  type TurnStateName,
  type WsErrorCode,
} from "./types";

export type ValidationResult =
  | { readonly ok: true; readonly message: ServerMessage }
  | { readonly ok: false; readonly reason: string };

const SENSOR_SOURCES: readonly SensorSource[] = ["pdh_english", "win32_api", "wmi", "adlx", "none"];

const ERROR_CODES: readonly WsErrorCode[] = [
  "schema_violation",
  "unsupported_version",
  "handshake_required",
  "system_unavailable",
  "provider_unavailable",
  "internal_error",
];

const PROVIDER_MODES: readonly ProviderMode[] = ["local", "cloud"];

const SIDE_EFFECTS: readonly SideEffect[] = ["read", "write", "destructive"];

const ORIGINS: readonly Origin[] = ["user_direct", "untrusted_content"];

const APPROVAL_OUTCOMES: readonly ApprovalOutcome[] = ["approved", "rejected", "expired", "auto_approved"];

const TURN_STATES: readonly TurnStateName[] = [
  "idle",
  "listening",
  "thinking",
  "tool_running",
  "speaking",
];

const TOOL_LOG_STATUSES: readonly ToolLogStatus[] = ["running", "ok", "failed"];

const ENVELOPE_FIELDS = ["v", "id", "ts", "type", "payload"] as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNullableNumber(value: unknown): value is number | null {
  return value === null || (typeof value === "number" && Number.isFinite(value));
}

function isPercent(value: unknown): value is number | null {
  return value === null || (typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 100);
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}

/** Prose that is either absent or actually says something. `""` is neither, so it is refused. */
function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isNullableProse(value: unknown): value is string | null {
  return value === null || (typeof value === "string" && value.length > 0);
}

/** Requires exactly the five envelope fields: no fewer, and crucially no more. */
function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(value);
  return actual.length === keys.length && keys.every((key) => key in value);
}

function isResolvedArgs(value: unknown): value is Record<string, ResolvedArgument> {
  if (!isRecord(value)) return false;

  return Object.values(value).every(
    (entry) =>
      entry === null || typeof entry === "string" || typeof entry === "boolean" || typeof entry === "number",
  );
}

function isSensorCapability(value: unknown): value is SensorCapability {
  if (!isRecord(value)) return false;
  if (!hasExactKeys(value, ["field", "available", "source", "unavailable_reason"])) return false;
  if (!isString(value.field) || value.field.length === 0) return false;
  if (typeof value.available !== "boolean") return false;
  if (!isString(value.source) || !SENSOR_SOURCES.includes(value.source as SensorSource)) return false;
  if (value.unavailable_reason !== null && !isString(value.unavailable_reason)) return false;

  // The pairing the schema enforces: a sensor that will never carry a value has to say why, or the
  // UI has nothing to show the user in place of it.
  if (!value.available && (value.unavailable_reason === null || value.source !== "none")) return false;

  return true;
}

function isSensorList(value: unknown): value is SensorCapability[] {
  return Array.isArray(value) && value.every(isSensorCapability);
}

function isCpu(value: unknown): value is CpuPayload {
  if (!isRecord(value)) return false;
  if (!hasExactKeys(value, ["total_percent", "per_core_percent", "frequency_mhz", "temperature_c"])) return false;
  if (!isPercent(value.total_percent)) return false;
  if (!isNullableNumber(value.frequency_mhz)) return false;
  if (!isNullableNumber(value.temperature_c)) return false;

  const cores = value.per_core_percent;
  if (cores !== null && !(Array.isArray(cores) && cores.every(isPercent))) return false;

  return true;
}

function isMemory(value: unknown): value is MemoryPayload {
  if (!isRecord(value)) return false;
  if (!hasExactKeys(value, ["used_bytes", "total_bytes", "commit_used_bytes", "commit_limit_bytes"])) return false;
  return (
    isNullableNumber(value.used_bytes) &&
    isNullableNumber(value.total_bytes) &&
    isNullableNumber(value.commit_used_bytes) &&
    isNullableNumber(value.commit_limit_bytes)
  );
}

function isGpu(value: unknown): value is GpuPayload {
  if (!isRecord(value)) return false;
  if (!hasExactKeys(value, ["utilization_percent", "vram_used_bytes", "vram_total_bytes", "temperature_c"])) {
    return false;
  }
  return (
    isPercent(value.utilization_percent) &&
    isNullableNumber(value.vram_used_bytes) &&
    isNullableNumber(value.vram_total_bytes) &&
    isNullableNumber(value.temperature_c)
  );
}

function isTelemetryPayload(value: unknown): value is TelemetryPayload {
  if (!isRecord(value)) return false;
  if (!hasExactKeys(value, ["seq", "sampled_at", "cpu", "memory", "gpu", "uptime_seconds"])) return false;
  if (typeof value.seq !== "number" || !Number.isInteger(value.seq) || value.seq < 0) return false;
  if (!isString(value.sampled_at)) return false;
  if (!isNullableNumber(value.uptime_seconds)) return false;

  return isCpu(value.cpu) && isMemory(value.memory) && isGpu(value.gpu);
}

function validatePayload(type: string, payload: unknown): string | null {
  switch (type) {
    case "server.hello": {
      if (!isRecord(payload)) return "payload is not an object";
      if (!hasExactKeys(payload, ["component", "app_version", "poll_interval_ms", "system_connected", "sensors"])) {
        return "server.hello payload has unexpected fields";
      }
      if (payload.component !== "brain") return "server.hello component is not 'brain'";
      if (!isString(payload.app_version)) return "server.hello app_version is not a string";
      if (typeof payload.poll_interval_ms !== "number") return "server.hello poll_interval_ms is not a number";
      if (typeof payload.system_connected !== "boolean") return "server.hello system_connected is not a boolean";
      if (!isSensorList(payload.sensors)) return "server.hello sensors is not a valid declaration";
      return null;
    }

    case "system.status": {
      if (!isRecord(payload)) return "payload is not an object";
      if (!hasExactKeys(payload, ["connected", "since", "reason", "sensors"])) {
        return "system.status payload has unexpected fields";
      }
      if (typeof payload.connected !== "boolean") return "system.status connected is not a boolean";
      if (!isString(payload.since)) return "system.status since is not a string";
      if (payload.reason !== null && !isString(payload.reason)) return "system.status reason is not a string or null";
      if (!isSensorList(payload.sensors)) return "system.status sensors is not a valid declaration";
      return null;
    }

    case "telemetry.sample":
      return isTelemetryPayload(payload) ? null : "telemetry.sample payload does not match the contract";

    case "error": {
      if (!isRecord(payload)) return "payload is not an object";
      if (!hasExactKeys(payload, ["code", "message", "in_reply_to"])) return "error payload has unexpected fields";
      if (!isString(payload.code) || !ERROR_CODES.includes(payload.code as WsErrorCode)) return "error code is unknown";
      if (!isString(payload.message) || payload.message.length === 0) return "error message is empty";
      if (payload.in_reply_to !== null && !isString(payload.in_reply_to)) return "error in_reply_to is malformed";
      return null;
    }

    case "approval.request": {
      if (!isRecord(payload)) return "payload is not an object";
      if (
        !hasExactKeys(payload, [
          "request_id",
          "capability",
          "resolved_args",
          "affected_paths",
          "side_effect",
          "origin",
        ])
      ) {
        return "approval.request payload has unexpected fields";
      }
      if (!isString(payload.request_id)) return "approval.request request_id is not a string";
      if (!isString(payload.capability) || payload.capability.length === 0) {
        return "approval.request capability is empty";
      }
      if (!isResolvedArgs(payload.resolved_args)) {
        // Scalars only. A nested value would have to be rendered by some rule the dialog invents,
        // and an invented rendering rule on the approval path is how markup gets back in.
        return "approval.request resolved_args contains a non-scalar value";
      }
      if (!Array.isArray(payload.affected_paths) || !payload.affected_paths.every(isString)) {
        return "approval.request affected_paths is not a list of strings";
      }
      if (!isString(payload.side_effect) || !SIDE_EFFECTS.includes(payload.side_effect as SideEffect)) {
        return "approval.request side_effect is unknown";
      }
      if (!isString(payload.origin) || !ORIGINS.includes(payload.origin as Origin)) {
        return "approval.request origin is unknown";
      }
      return null;
    }

    case "approval.resolved": {
      if (!isRecord(payload)) return "payload is not an object";
      if (!hasExactKeys(payload, ["request_id", "outcome"])) return "approval.resolved payload has unexpected fields";
      if (!isString(payload.request_id)) return "approval.resolved request_id is not a string";
      if (!isString(payload.outcome) || !APPROVAL_OUTCOMES.includes(payload.outcome as ApprovalOutcome)) {
        return "approval.resolved outcome is unknown";
      }
      return null;
    }

    case "trust.status": {
      if (!isRecord(payload)) return "payload is not an object";
      if (!hasExactKeys(payload, ["enabled", "since"])) return "trust.status payload has unexpected fields";
      if (typeof payload.enabled !== "boolean") return "trust.status enabled is not a boolean";
      if (!isString(payload.since)) return "trust.status since is not a string";
      return null;
    }

    case "provider.status": {
      if (!isRecord(payload)) return "payload is not an object";
      if (!hasExactKeys(payload, ["mode", "model", "has_key", "since"])) {
        // Exact keys is what keeps a key from ever riding in here. A future field carrying one
        // would be dropped by this build rather than rendered.
        return "provider.status payload has unexpected fields";
      }
      if (!isString(payload.mode) || !PROVIDER_MODES.includes(payload.mode as ProviderMode)) {
        return "provider.status mode is unknown";
      }
      if (!isString(payload.model) || payload.model.length === 0) return "provider.status model is empty";
      if (typeof payload.has_key !== "boolean") return "provider.status has_key is not a boolean";
      if (!isString(payload.since)) return "provider.status since is not a string";
      return null;
    }

    case "memory.status": {
      if (!isRecord(payload)) return "payload is not an object";
      if (
        !hasExactKeys(payload, [
          "enabled",
          "vault",
          "notes",
          "chunks",
          "embedded_chunks",
          "last_indexed_at",
          "embeddings_available",
        ])
      ) {
        return "memory.status payload has unexpected fields";
      }
      if (typeof payload.enabled !== "boolean") return "memory.status enabled is not a boolean";
      if (payload.vault !== null && !isString(payload.vault)) return "memory.status vault is malformed";
      for (const field of ["notes", "chunks", "embedded_chunks"] as const) {
        const value = payload[field];
        if (typeof value !== "number" || !Number.isInteger(value) || value < 0) {
          return `memory.status ${field} is not a count`;
        }
      }
      if (payload.last_indexed_at !== null && !isString(payload.last_indexed_at)) {
        return "memory.status last_indexed_at is malformed";
      }
      if (typeof payload.embeddings_available !== "boolean") {
        return "memory.status embeddings_available is not a boolean";
      }
      return null;
    }

    case "turn.state": {
      if (!isRecord(payload)) return "payload is not an object";
      if (!hasExactKeys(payload, ["state", "since", "caption", "detail"])) {
        return "turn.state payload has unexpected fields";
      }
      if (!isString(payload.state) || !TURN_STATES.includes(payload.state as TurnStateName)) {
        return "turn.state state is unknown";
      }
      if (!isString(payload.since)) return "turn.state since is not a string";
      // Empty prose is refused rather than coerced to null. The contract has one spelling for
      // silence, and accepting a second one here would let a blank caption render as a blank line
      // the user cannot tell apart from a caption that failed to arrive.
      if (!isNullableProse(payload.caption)) return "turn.state caption is malformed";
      if (!isNullableProse(payload.detail)) return "turn.state detail is malformed";
      return null;
    }

    case "tool.log": {
      if (!isRecord(payload)) return "payload is not an object";
      if (!hasExactKeys(payload, ["at", "capability", "message", "status"])) {
        return "tool.log payload has unexpected fields";
      }
      if (!isString(payload.at)) return "tool.log at is not a string";
      if (!isString(payload.capability) || payload.capability.length === 0) {
        return "tool.log capability is empty";
      }
      if (!isString(payload.message) || payload.message.length === 0) return "tool.log message is empty";
      if (!isString(payload.status) || !TOOL_LOG_STATUSES.includes(payload.status as ToolLogStatus)) {
        // Not bucketed into the nearest-looking status: the nearest-looking one is "ok", and that
        // would paint a failed call green.
        return "tool.log status is unknown";
      }
      return null;
    }

    case "capability.result": {
      if (!isRecord(payload)) return "payload is not an object";
      if (!hasExactKeys(payload, ["at", "capability", "columns", "rows", "truncated"])) {
        return "capability.result payload has unexpected fields";
      }
      if (!isString(payload.at)) return "capability.result at is not a string";
      if (!isString(payload.capability) || payload.capability.length === 0) {
        return "capability.result capability is empty";
      }
      if (!isStringArray(payload.columns) || payload.columns.length === 0) {
        return "capability.result columns are malformed";
      }
      if (!Array.isArray(payload.rows)) return "capability.result rows are not an array";

      // Checked here as well as in the brain. A row that does not match the header draws every
      // cell after it under the wrong heading, and a pid shown as a memory figure is worse than no
      // table at all - so a malformed frame is refused rather than rendered crookedly.
      for (const row of payload.rows) {
        if (!isStringArray(row)) return "capability.result contains a row that is not strings";
        if (row.length !== payload.columns.length) {
          return "capability.result has a row whose width does not match its header";
        }
      }

      if (typeof payload.truncated !== "boolean") return "capability.result truncated is not a boolean";
      return null;
    }

    default:
      return `unknown message type '${type}'`;
  }
}

/**
 * Parses and validates one frame.
 *
 * Returns a reason rather than throwing, because a bad frame is dropped and counted - it does not
 * tear down the connection, and it does not become an exception the render tree has to survive.
 */
export function parseServerMessage(raw: string): ValidationResult {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return { ok: false, reason: "frame is not valid JSON" };
  }

  if (!isRecord(value)) return { ok: false, reason: "frame is not an object" };
  if (!hasExactKeys(value, ENVELOPE_FIELDS)) return { ok: false, reason: "envelope fields do not match the contract" };
  if (typeof value.v !== "number") return { ok: false, reason: "v is not a number" };

  // Unknown version fails closed. No best-effort parsing of a shape this build does not implement.
  if (value.v !== CONTRACT_VERSION) {
    return { ok: false, reason: `v is ${value.v}, this build implements ${CONTRACT_VERSION}` };
  }

  if (!isString(value.id)) return { ok: false, reason: "id is not a string" };
  if (!isString(value.ts)) return { ok: false, reason: "ts is not a string" };
  if (!isString(value.type)) return { ok: false, reason: "type is not a string" };

  const failure = validatePayload(value.type, value.payload);
  if (failure !== null) return { ok: false, reason: failure };

  return { ok: true, message: value as unknown as ServerMessage };
}
