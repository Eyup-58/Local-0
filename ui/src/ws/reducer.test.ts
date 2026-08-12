/**
 * The rules that decide what the user sees: liveness, staleness, and what gets counted.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { initialState, isStale, telemetryReducer, type TelemetryState } from "./reducer";

const EXAMPLES = join(import.meta.dirname, "..", "..", "..", "contracts", "examples");

function example(name: string): Record<string, unknown> {
  return JSON.parse(readFileSync(join(EXAMPLES, name), "utf-8"));
}

function frame(state: TelemetryState, message: unknown, now = 1_000): TelemetryState {
  return telemetryReducer(state, { kind: "frame", raw: JSON.stringify(message), now });
}

const serverHello = example("ws.server-hello.json");
const systemStatus = example("ws.system-status.json");
const telemetrySample = example("ws.telemetry-sample.json");

function connected(state = initialState): TelemetryState {
  const opened = telemetryReducer(state, { kind: "socket-opened" });
  return frame(opened, { ...serverHello, payload: { ...(serverHello.payload as object), system_connected: true } });
}

describe("handshake", () => {
  it("takes the sensor declaration from server.hello", () => {
    const state = connected();

    expect(state.sensors.length).toBeGreaterThan(0);
  });

  it("takes the poll interval from the brain rather than assuming one", () => {
    const state = connected();

    expect(state.pollIntervalMs).toBe((serverHello.payload as { poll_interval_ms: number }).poll_interval_ms);
  });
});

describe("dropped frames", () => {
  it("counts a frame that is not valid JSON", () => {
    const state = telemetryReducer(initialState, { kind: "frame", raw: "not json", now: 0 });

    expect(state.droppedFrames).toBe(1);
  });

  it("counts a frame carrying an unimplemented contract version", () => {
    const state = frame(initialState, { ...telemetrySample, v: 99 });

    expect(state.droppedFrames).toBe(1);
  });

  it("does not apply a dropped frame", () => {
    const state = frame(connected(), { ...telemetrySample, v: 99 });

    expect(state.sample).toBeNull();
  });

  it("survives a bad frame and keeps working", () => {
    // One bad message is not a reason to tear anything down.
    const afterBad = telemetryReducer(connected(), { kind: "frame", raw: "not json", now: 0 });
    const state = frame(afterBad, telemetrySample);

    expect(state.sample).not.toBeNull();
    expect(state.droppedFrames).toBe(1);
  });
});

describe("missed samples", () => {
  function sampleWithSeq(seq: number) {
    return { ...telemetrySample, payload: { ...(telemetrySample.payload as object), seq } };
  }

  it("counts the gap when sequence numbers skip", () => {
    // A gap in seq is the contract's way of saying samples were dropped, and the consumer can say
    // so rather than presenting a discontinuity as continuous data.
    const first = frame(connected(), sampleWithSeq(4));
    const state = frame(first, sampleWithSeq(9));

    expect(state.missedSamples).toBe(4);
  });

  it("counts nothing for consecutive samples", () => {
    const first = frame(connected(), sampleWithSeq(4));
    const state = frame(first, sampleWithSeq(5));

    expect(state.missedSamples).toBe(0);
  });

  it("does not count a reconnect as missed samples", () => {
    // seq restarts at 0 on every pipe connection.
    const running = frame(connected(), sampleWithSeq(40));
    const reconnected = frame(running, systemStatus);
    const state = frame(reconnected, sampleWithSeq(0));

    expect(state.missedSamples).toBe(0);
  });
});

describe("system status", () => {
  it("marks the system disconnected and keeps the reason", () => {
    const state = frame(connected(), systemStatus);

    expect(state.systemConnected).toBe(false);
    expect(state.reason).toBe((systemStatus.payload as { reason: string }).reason);
  });

  it("keeps the last sensor declaration when the sidecar goes away", () => {
    // The empty list means the sidecar is gone, not that it declared nothing. Keeping the last
    // declaration is what lets every gap on screen still say why it is a gap.
    const state = frame(connected(), systemStatus);

    expect(state.sensors.length).toBeGreaterThan(0);
  });
});

describe("staleness", () => {
  it("is stale before any sample has arrived", () => {
    expect(isStale(connected(), 0)).toBe(true);
  });

  it("is live immediately after a sample", () => {
    const state = frame(connected(), telemetrySample, 5_000);

    expect(isStale(state, 5_000)).toBe(false);
  });

  it("tolerates one late tick", () => {
    const state = frame(connected(), telemetrySample, 5_000);

    expect(isStale(state, 6_400)).toBe(false);
  });

  it("goes stale once samples stop arriving", () => {
    const state = frame(connected(), telemetrySample, 5_000);

    expect(isStale(state, 9_000)).toBe(true);
  });

  it("is stale whenever the system layer is disconnected, however recent the sample", () => {
    // The decisive case: a dead sidecar and an idle machine look identical if you only watch the
    // numbers. The last sample must not be presented as live.
    const withSample = frame(connected(), telemetrySample, 5_000);
    const state = frame(withSample, systemStatus, 5_000);

    expect(isStale(state, 5_000)).toBe(true);
  });

  it("is stale when the socket closes", () => {
    const withSample = frame(connected(), telemetrySample, 5_000);
    const state = telemetryReducer(withSample, { kind: "socket-closed" });

    expect(isStale(state, 5_000)).toBe(true);
  });

  it("keeps the last values on screen when the socket closes", () => {
    // Kept, but marked. Flashing the panel empty would lose information the user was reading; the
    // staleness marker is what stops those numbers being mistaken for current.
    const withSample = frame(connected(), telemetrySample, 5_000);
    const state = telemetryReducer(withSample, { kind: "socket-closed" });

    expect(state.sample).not.toBeNull();
  });
});

describe("the network boundary", () => {
  const providerStatus = example("ws.provider-status.json");

  it("starts local, before the brain has said anything", () => {
    // The state before anything is known has to be the one that sends nothing.
    expect(initialState.providerMode).toBe("local");
    expect(initialState.hasKey).toBe(false);
  });

  it("takes the mode from the brain rather than assuming it", () => {
    const state = frame(connected(), {
      ...providerStatus,
      payload: { ...(providerStatus.payload as object), mode: "cloud", has_key: true },
    });

    expect(state.providerMode).toBe("cloud");
    expect(state.hasKey).toBe(true);
  });

  it("records the model the brain reported", () => {
    const state = frame(connected(), providerStatus);

    expect(state.providerModel).toBe((providerStatus.payload as { model: string }).model);
  });

  it("drops a status frame carrying anything resembling a key, rather than rendering it", () => {
    const before = connected();

    const state = frame(before, {
      ...providerStatus,
      // Not shaped like a vendor key. A fixture wearing a real prefix is a fixture every secret
      // scanner flags for the life of the repository, and an allowlist entry is a worse answer than
      // a string that could never be mistaken for a key in the first place.
      payload: { ...(providerStatus.payload as object), key: "fake-key-should-never-arrive" },
    });

    expect(state.providerMode).toBe(before.providerMode);
    expect(state.droppedFrames).toBe(before.droppedFrames + 1);
  });
});
