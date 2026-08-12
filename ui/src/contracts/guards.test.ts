/**
 * The guards, held against the checked-in contract examples.
 *
 * The examples are read from contracts/ in place. A copy would be a second source of truth, and
 * the first time it went stale this suite would pass against a contract that no longer exists.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { parseServerMessage } from "./guards";

const CONTRACTS_DIR = join(import.meta.dirname, "..", "..", "..", "contracts");
const EXAMPLES_DIR = join(CONTRACTS_DIR, "examples");
const REJECTED_DIR = join(EXAMPLES_DIR, "rejected");

/** Documentation-only keys, stripped so a rejected example fails for its real reason. */
function stripAnnotations(message: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(message).filter(([key]) => !key.startsWith("_")));
}

function load(path: string): string {
  return JSON.stringify(stripAnnotations(JSON.parse(readFileSync(path, "utf-8"))));
}

function examplesIn(dir: string, prefix: string): string[] {
  return readdirSync(dir).filter((name) => name.startsWith(prefix) && name.endsWith(".json"));
}

describe("contract examples", () => {
  const valid = examplesIn(EXAMPLES_DIR, "ws.");
  const rejected = examplesIn(REJECTED_DIR, "ws.");

  it("finds examples to check", () => {
    // A glob that silently matches nothing would make every case below vacuously pass.
    expect(valid.length).toBeGreaterThan(0);
    expect(rejected.length).toBeGreaterThan(0);
  });

  // Messages the UI *sends*. parseServerMessage validates inbound frames, so accepting one of these
  // would mean the guards are wider than the direction of the contract - the UI would be willing to
  // receive a decision it is supposed to be the one making.
  const uiSent = [
    "ws.client-hello.json",
    "ws.approval-decision.json",
    "ws.trust-set.json",
    "ws.provider-select.json",
    // The one in this list that matters most: a UI willing to *receive* a credential.set would be a
    // UI that could be handed a key by anything that reached the socket.
    "ws.credential-set.json",
    "ws.memory-reindex.json",
    // A UI willing to *receive* a turn.request would take prose aimed at the planner from whatever
    // reached the socket, and the planner is the one component that must only ever see what the
    // user typed.
    "ws.turn-request.json",
  ];

  it.each(valid.filter((name) => !uiSent.includes(name)))("accepts %s", (name) => {
    const result = parseServerMessage(load(join(EXAMPLES_DIR, name)));

    expect(result.ok, result.ok ? "" : result.reason).toBe(true);
  });

  it.each(uiSent)("refuses %s, which the UI sends rather than receives", (name) => {
    const result = parseServerMessage(load(join(EXAMPLES_DIR, name)));

    expect(result.ok).toBe(false);
  });

  it.each(rejected)("refuses %s", (name) => {
    const result = parseServerMessage(load(join(REJECTED_DIR, name)));

    expect(result.ok).toBe(false);
  });
});

describe("envelope validation", () => {
  const valid = load(join(EXAMPLES_DIR, "ws.telemetry-sample.json"));

  it("refuses a frame that is not JSON", () => {
    expect(parseServerMessage("not json at all").ok).toBe(false);
  });

  it("refuses a frame that is not an object", () => {
    expect(parseServerMessage("[1,2,3]").ok).toBe(false);
  });

  it("refuses an unimplemented contract version without parsing it further", () => {
    const frame = { ...JSON.parse(valid), v: 99 };

    const result = parseServerMessage(JSON.stringify(frame));

    expect(result.ok).toBe(false);
    expect(result.ok === false && result.reason).toContain("99");
  });

  it("refuses an envelope carrying an unknown field", () => {
    // additionalProperties is false everywhere. That is what stops a field being smuggled past one
    // layer in the hope a later one reads it.
    const frame = { ...JSON.parse(valid), exec: "calc.exe" };

    expect(parseServerMessage(JSON.stringify(frame)).ok).toBe(false);
  });

  it("refuses an unknown message type", () => {
    const frame = { ...JSON.parse(valid), type: "capability.invoke" };

    expect(parseServerMessage(JSON.stringify(frame)).ok).toBe(false);
  });
});

describe("telemetry payload validation", () => {
  const sample = JSON.parse(load(join(EXAMPLES_DIR, "ws.telemetry-sample.json")));

  function withCpu(cpu: Record<string, unknown>): string {
    return JSON.stringify({ ...sample, payload: { ...sample.payload, cpu: { ...sample.payload.cpu, ...cpu } } });
  }

  it("accepts a parked core as null in its own slot", () => {
    const result = parseServerMessage(withCpu({ per_core_percent: [12.4, null, 22.7] }));

    expect(result.ok).toBe(true);
  });

  it("accepts per_core_percent being absent entirely", () => {
    expect(parseServerMessage(withCpu({ per_core_percent: null })).ok).toBe(true);
  });

  it("refuses a core reading outside the percentage range", () => {
    // Allowing nulls must not have loosened the range check on entries that do carry a value.
    expect(parseServerMessage(withCpu({ per_core_percent: [12.4, null, 140] })).ok).toBe(false);
  });

  it("refuses a core reading that is not a number", () => {
    expect(parseServerMessage(withCpu({ per_core_percent: [12.4, "idle"] })).ok).toBe(false);
  });

  it("refuses a total load outside the percentage range", () => {
    expect(parseServerMessage(withCpu({ total_percent: 140 })).ok).toBe(false);
  });

  it("accepts a permanently unavailable temperature as null", () => {
    expect(parseServerMessage(withCpu({ temperature_c: null })).ok).toBe(true);
  });
});

describe("sensor declaration validation", () => {
  const hello = JSON.parse(load(join(EXAMPLES_DIR, "ws.server-hello.json")));

  function withSensors(sensors: unknown): string {
    return JSON.stringify({ ...hello, payload: { ...hello.payload, sensors } });
  }

  it("accepts an empty declaration, which means the system layer has not handshaked", () => {
    expect(parseServerMessage(withSensors([])).ok).toBe(true);
  });

  it("refuses an unavailable sensor with no reason", () => {
    // A silent gap is a rejected message: the UI would have nothing to show in place of the value.
    const sensors = [{ field: "cpu.temperature_c", available: false, source: "none", unavailable_reason: null }];

    expect(parseServerMessage(withSensors(sensors)).ok).toBe(false);
  });

  it("refuses an unavailable sensor that still claims a source", () => {
    const sensors = [
      { field: "cpu.temperature_c", available: false, source: "pdh_english", unavailable_reason: "no driver" },
    ];

    expect(parseServerMessage(withSensors(sensors)).ok).toBe(false);
  });

  it("refuses an unknown sensor source", () => {
    const sensors = [{ field: "cpu.total_percent", available: true, source: "telepathy", unavailable_reason: null }];

    expect(parseServerMessage(withSensors(sensors)).ok).toBe(false);
  });
});
