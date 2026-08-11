/**
 * Owns the WebSocket: connects, handshakes, reconnects with backoff, and feeds the reducer.
 *
 * Nothing here decides what the user sees. That is reducer.ts, deliberately, so the rules can be
 * tested without a socket.
 */

import { useEffect, useReducer, useRef, useState } from "react";
import { CONTRACT_VERSION } from "../contracts/types";
import { initialState, telemetryReducer, type TelemetryState } from "./reducer";

const UI_VERSION = "0.1.0";

/** Loopback only, matching the brain. See docs/ARCHITECTURE.md section 4. */
const DEFAULT_URL = "ws://127.0.0.1:8765/ws";

const FIRST_RETRY_MS = 500;
const MAX_RETRY_MS = 10_000;

/** How often the staleness clock re-renders. Independent of the poll interval. */
const STALENESS_TICK_MS = 500;

function envelope(type: string, payload: object): string {
  return JSON.stringify({
    v: CONTRACT_VERSION,
    id: crypto.randomUUID(),
    ts: new Date().toISOString().replace(/(\.\d{3})\d*Z$/, "$1Z"),
    type,
    payload,
  });
}

function clientHello(): string {
  return envelope("client.hello", { component: "ui", app_version: UI_VERSION });
}

export interface Telemetry {
  readonly state: TelemetryState;
  /** Re-read every tick so staleness is a live judgement, not one frozen at the last frame. */
  readonly now: number;
  /**
   * Answers one pending approval.
   *
   * The request_id is echoed from what the brain sent; the UI never invents one. An id the brain
   * did not issue identifies nothing, and is refused at the other end.
   */
  readonly decide: (requestId: string, approve: boolean) => void;
  /** Turns approval off or back on. The only message that changes trust state. */
  readonly setTrust: (enabled: boolean) => void;
}

export function useTelemetry(url: string = DEFAULT_URL): Telemetry {
  const [state, dispatch] = useReducer(telemetryReducer, initialState);
  const [now, setNow] = useState(() => Date.now());
  const retryRef = useRef(FIRST_RETRY_MS);
  const socketRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const clock = window.setInterval(() => setNow(Date.now()), STALENESS_TICK_MS);
    return () => window.clearInterval(clock);
  }, []);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let retryTimer: number | undefined;
    let disposed = false;

    const open = () => {
      if (disposed) return;

      socket = new WebSocket(url);
      socketRef.current = socket;

      socket.onopen = () => {
        retryRef.current = FIRST_RETRY_MS;
        dispatch({ kind: "socket-opened" });
        socket?.send(clientHello());
      };

      socket.onmessage = (event) => {
        if (typeof event.data !== "string") return;
        dispatch({ kind: "frame", raw: event.data, now: Date.now() });
      };

      socket.onclose = () => {
        dispatch({ kind: "socket-closed" });
        if (disposed) return;

        retryTimer = window.setTimeout(open, retryRef.current);
        retryRef.current = Math.min(retryRef.current * 2, MAX_RETRY_MS);
      };

      // A socket error is always followed by a close, which is where reconnection is handled.
      socket.onerror = () => socket?.close();
    };

    open();

    return () => {
      disposed = true;
      window.clearTimeout(retryTimer);
      socket?.close();
      socketRef.current = null;
    };
  }, [url]);

  const send = (type: string, payload: object): void => {
    const socket = socketRef.current;
    // A decision sent into a closed socket is a decision nobody receives. Dropping it is right: the
    // brain still holds the request, and it will be re-offered when the UI reconnects. Pretending it
    // was delivered is the one thing that must not happen.
    if (socket?.readyState !== WebSocket.OPEN) return;

    socket.send(envelope(type, payload));
  };

  return {
    state,
    now,
    decide: (requestId, approve) =>
      send("approval.decision", { request_id: requestId, decision: approve ? "approve" : "reject" }),
    setTrust: (enabled) => send("trust.set", { enabled }),
  };
}
