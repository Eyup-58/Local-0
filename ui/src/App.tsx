/**
 * The orchestration centre: one core in the middle, chrome pinned to the edges, and a dock that
 * slides in over the right.
 *
 * The arrangement is the one the designer mocked up. The rule underneath it is the panel's own and
 * it did not change: every value on this page comes from the sidecar's declaration, from its
 * samples, or from a turn the brain reported - and where a value is missing, the reason shown is
 * the source's own words.
 *
 * What this layout deliberately does *not* borrow from the mockup: the demo sequence, the
 * simulated waveform, the jittered telemetry and the typewriter caption. Each of those invents
 * something. The demo advances a turn the brain never reported; the jitter draws load nobody
 * measured; the typewriter shows sentences in an order the brain never sent. The mockup used them
 * to look alive with no backend attached, which is the right call for a mockup and the wrong one
 * here.
 *
 * The rail is likewise the design's shape but not its contents. The mockup's Browser and Media
 * entries have no capability behind them in this build, and a rail whose doors open onto "not
 * built yet" is the rail Hud.tsx warns about. Every entry here opens something real.
 */

import { useState, type ReactNode } from "react";

import { ApprovalDialog } from "./components/ApprovalDialog";
import { Counters } from "./components/Counters";
import { CoreGrid } from "./components/CoreGrid";
import { DeclaredGaps } from "./components/DeclaredGaps";
import { Gauge } from "./components/Gauge";
import { AskBox, Caption, Dock, Rail, ReadingStrip, ToolLogList, TopBar } from "./components/Hud";
import type { LinkTone, RailItem, Reading } from "./components/Hud";
import { LinkStatus } from "./components/LinkStatus";
import { MemoryControl } from "./components/MemoryControl";
import { Metric } from "./components/Metric";
import { Orb } from "./components/Orb";
import type { OrbMood } from "./components/Orb";
import { ProviderControl } from "./components/ProviderControl";
import { TrustControl } from "./components/TrustControl";
import { formatGibibytes, formatMegahertz, formatPercent, formatUptime, fraction } from "./format";
import type { SensorCapability, TelemetryPayload, TurnStateName } from "./contracts/types";
import { isStale } from "./ws/reducer";
import type { TelemetryState } from "./ws/reducer";
import { useTelemetry } from "./ws/useTelemetry";

/**
 * Used only when the sidecar has told us nothing at all about a field - before the first
 * handshake, or for a field its declaration did not mention. Anything the sidecar did explain is
 * shown in its words, not these.
 */
const NO_DECLARATION = "The system layer has not reported on this sensor.";

/** One 19px line icon, in the mockup's drawing style: 1.3 stroke, no fill, 24-unit box. */
function icon(path: ReactNode) {
  return (
    <svg
      width="19"
      height="19"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.3"
      aria-hidden="true"
    >
      {path}
    </svg>
  );
}

const PANELS: readonly RailItem[] = [
  { id: "tools", title: "Tool log", icon: icon(<path d="M4 5h16v14H4zM7.5 10l2.2 2.2-2.2 2.2M12.5 14.6h4" />) },
  { id: "processor", title: "Processor", icon: icon(<path d="M3 12h3.5l2-5.5 3 11 2.5-6.5 1.8 3.5H21" />) },
  {
    id: "graphics",
    title: "Graphics",
    icon: icon(
      <>
        <rect x="3.5" y="6.5" width="17" height="11" rx="1.5" />
        <path d="M7 10.5h3.5M7 13.5h6" />
      </>,
    ),
  },
  {
    id: "memory",
    title: "Memory",
    icon: icon(
      <>
        <rect x="4" y="8" width="16" height="8" rx="1" />
        <path d="M8 8v8M12 8v8M16 8v8" />
      </>,
    ),
  },
  {
    id: "cores",
    title: "Cores",
    icon: icon(
      <>
        <rect x="7" y="7" width="10" height="10" rx="1" />
        <path d="M10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4" />
      </>,
    ),
  },
  {
    id: "model",
    title: "Model",
    icon: icon(
      <>
        <circle cx="12" cy="12" r="8.5" />
        <path d="M12 3.5a8.5 8.5 0 0 1 0 17z" fill="currentColor" stroke="none" />
      </>,
    ),
  },
  {
    id: "vault",
    title: "Vault",
    icon: icon(
      <>
        <rect x="3.5" y="5.5" width="17" height="13" rx="1.5" />
        <circle cx="12" cy="12" r="3" />
        <path d="M12 9V7M12 17v-2" />
      </>,
    ),
  },
  {
    id: "guard",
    title: "Guard",
    icon: icon(
      <>
        <path d="M12 3l7 3v5.5c0 4.3-3 7.7-7 9.5-4-1.8-7-5.2-7-9.5V6z" />
        <path d="M9 12l2.2 2.2L15.5 10" />
      </>,
    ),
  },
  { id: "link", title: "Link", icon: icon(<path d="M4 9h13l-3-3M20 15H7l3 3" />) },
];

/** The five reported turns, in the order the control track shows them. */
const TURN_TRACK: readonly { readonly id: TurnStateName; readonly label: string }[] = [
  { id: "idle", label: "STANDBY" },
  { id: "listening", label: "LISTENING" },
  { id: "thinking", label: "THINKING" },
  { id: "tool_running", label: "TOOL" },
  { id: "speaking", label: "SPEAKING" },
];

function reasonFor(sensors: readonly SensorCapability[], field: string): string {
  const declared = sensors.find((sensor) => sensor.field === field);
  return declared?.unavailable_reason ?? NO_DECLARATION;
}

function linkTone(state: TelemetryState, stale: boolean): LinkTone {
  if (!state.systemConnected) return "waiting";
  return stale ? "stale" : "live";
}

/**
 * What the orb is doing, decided from state the brain reported rather than from elapsed time.
 *
 * Order matters: something waiting for the user outranks how the machine happens to be loaded, and
 * approval being switched off outranks both - it is the one state where the panel is showing you
 * less than it otherwise would.
 */
function orbMood(state: TelemetryState, tone: LinkTone): OrbMood {
  if (state.pendingApproval !== null) return "attention";
  if (state.trustEnabled) return "unguarded";
  return tone === "live" ? "idle" : "offline";
}

/**
 * The orb's motion, from the busier of the two live loads.
 *
 * Null when neither was measured, which is what keeps the shell still rather than breathing at a
 * zero nobody reported.
 */
function orbEnergy(sample: TelemetryPayload | null): number | null {
  const cpu = sample?.cpu?.total_percent ?? null;
  const gpu = sample?.gpu?.utilization_percent ?? null;
  if (cpu === null && gpu === null) return null;

  return Math.min(1, Math.max(cpu ?? 0, gpu ?? 0) / 100);
}

/**
 * What the *panel* needs to say, in priority order, or null when it has nothing.
 *
 * This is the panel's own voice and it only ever states fact - what is waiting, what failed, what
 * is not being measured. Null is the important return: it means the panel steps aside and the
 * brain's caption shows through. Returning a cheerful "all sensors reporting" here instead would
 * mean the panel talked over every turn the brain ever took.
 */
function noticeFor(
  state: TelemetryState,
  stale: boolean,
): { text: string; tone: LinkTone | "alarm" } | null {
  if (state.pendingApproval !== null) {
    return {
      text: `${state.pendingApproval.capability} is waiting for your decision.`,
      tone: "alarm",
    };
  }

  if (state.lastError !== null) return { text: state.lastError, tone: "alarm" };
  if (!state.systemConnected) {
    return { text: state.reason ?? "Waiting for the system layer.", tone: "waiting" };
  }

  if (stale) {
    return { text: "The last sample is late. Nothing here is being estimated.", tone: "stale" };
  }
  if (state.trustEnabled) {
    return {
      text: "Approval is switched off. The guard still runs; nothing stops to ask.",
      tone: "alarm",
    };
  }

  return null;
}

export function App() {
  const { state, now, decide, setTrust, selectProvider, setKey, reindexMemory, ask } = useTelemetry();
  const [panel, setPanel] = useState("tools");
  // Closed until asked for. The core is the page; a dock that opened itself would take half the
  // stage before the user had decided they wanted it.
  const [dockOpen, setDockOpen] = useState(false);

  const stale = isStale(state, now);
  const sample = state.sample;
  const sensors = state.sensors;

  const cpu = sample?.cpu ?? null;
  const memory = sample?.memory ?? null;
  const gpu = sample?.gpu ?? null;

  const tone = linkTone(state, stale);
  const notice = noticeFor(state, stale);

  const openPanel = (id: string) => {
    // Clicking the panel already showing closes the dock, which is what makes the rail read as a
    // set of toggles rather than a set of one-way doors.
    setDockOpen(id !== panel || !dockOpen);
    setPanel(id);
  };

  const readings: readonly Reading[] = [
    {
      label: "CPU",
      value: formatPercent(cpu?.total_percent ?? null),
      unit: "%",
      reason: reasonFor(sensors, "cpu.total_percent"),
    },
    {
      label: "GPU",
      value: formatPercent(gpu?.utilization_percent ?? null),
      unit: "%",
      reason: reasonFor(sensors, "gpu.utilization_percent"),
    },
    {
      label: "MEM",
      value: formatGibibytes(memory?.used_bytes ?? null),
      unit: "GiB",
      reason: reasonFor(sensors, "memory.used_bytes"),
    },
    // Counted from frames the brain sent, not sampled: a tool call is a thing that was reported,
    // so this is a tally rather than a measurement and it has no unavailable state.
    { label: "TOOLS", value: String(state.toolCalls), reason: NO_DECLARATION },
    { label: "TURNS", value: String(state.turnCount), reason: NO_DECLARATION, roomy: true },
    {
      label: "VRAM",
      value: formatGibibytes(gpu?.vram_used_bytes ?? null),
      unit: "GiB",
      reason: reasonFor(sensors, "gpu.vram_used_bytes"),
      roomy: true,
    },
    {
      label: "CLK",
      value: formatMegahertz(cpu?.frequency_mhz ?? null),
      unit: "MHz",
      reason: reasonFor(sensors, "cpu.frequency_mhz"),
      roomy: true,
    },
    {
      label: "UP",
      value: formatUptime(sample?.uptime_seconds ?? null),
      reason: NO_DECLARATION,
      roomy: true,
    },
  ];

  const shell = ["hud", stale ? "hud--stale" : "", dockOpen ? "hud--dock-open" : ""]
    .filter(Boolean)
    .join(" ");

  return (
    <main className={shell} data-turn={state.turn}>
      <div className="hud__grid" aria-hidden="true" />
      <div className="hud__vignette" aria-hidden="true" />

      <div className="hud__stage">
        <Orb
          energy={orbEnergy(sample)}
          turn={state.turn}
          mood={orbMood(state, tone)}
          dockOpen={dockOpen}
          hasCaption={notice !== null || state.caption !== null}
          label={`System load ${formatPercent(cpu?.total_percent ?? null) ?? "unavailable"}`}
        />
      </div>

      <TopBar
        turn={state.turn}
        tone={tone}
        trustEnabled={state.trustEnabled}
        clock={new Date(now).toLocaleTimeString()}
      />

      <ReadingStrip readings={readings} />

      <Rail
        items={PANELS}
        active={panel}
        dockOpen={dockOpen}
        onSelect={openPanel}
        onClose={() => setDockOpen(false)}
      />

      <Caption turn={state.turn} caption={state.caption} detail={state.turnDetail} notice={notice} />

      {/*
        A read-out, not a control panel. The turn is reported by the brain and there is nothing in
        this contract a tab may send to change it, so these are lit rather than clickable. Buttons
        that looked like they drove the core while doing nothing would be worse than no buttons -
        and buttons that actually drove it would be the UI deciding what the brain was doing.
      */}
      <div className="controls">
        <AskBox onAsk={ask} connected={state.link === "open"} />

        <div className="controls__group" role="group" aria-label="Reported turn">
          {TURN_TRACK.map((entry) => (
            <span
              key={entry.id}
              className={
                state.turn === entry.id && tone === "live"
                  ? "controls__chip controls__chip--on"
                  : "controls__chip"
              }
              aria-current={state.turn === entry.id ? "true" : undefined}
            >
              {entry.label}
            </span>
          ))}
          <span className="controls__divider" aria-hidden="true" />
          <span className="controls__reading">{state.providerMode.toUpperCase()}</span>
          <span className="controls__reading">{state.providerModel || "NO MODEL"}</span>
        </div>
      </div>

      <Dock
        title={PANELS.find((item) => item.id === panel)?.title ?? ""}
        open={dockOpen}
        onClose={() => setDockOpen(false)}
      >
        {panel === "tools" && <ToolLogList lines={state.toolLog} />}

        {panel === "processor" && (
          <>
            <Metric
              label="Total load"
              value={formatPercent(cpu?.total_percent ?? null)}
              unit="%"
              emphasis
              unavailableReason={reasonFor(sensors, "cpu.total_percent")}
            />
            <Gauge value={cpu ? fraction(cpu.total_percent, 100) : null} label="Total CPU load" />

            <Metric
              label="Frequency"
              value={formatMegahertz(cpu?.frequency_mhz ?? null)}
              unit="MHz"
              unavailableReason={reasonFor(sensors, "cpu.frequency_mhz")}
            />

            <Metric
              label="Package temperature"
              value={formatPercent(cpu?.temperature_c ?? null)}
              unit="°C"
              unavailableReason={reasonFor(sensors, "cpu.temperature_c")}
            />
          </>
        )}

        {panel === "graphics" && (
          <>
            <Metric
              label="Utilization"
              value={formatPercent(gpu?.utilization_percent ?? null)}
              unit="%"
              emphasis
              unavailableReason={reasonFor(sensors, "gpu.utilization_percent")}
            />
            <Gauge
              value={gpu ? fraction(gpu.utilization_percent, 100) : null}
              label="GPU utilization"
            />

            <Metric
              label="Video memory"
              value={formatGibibytes(gpu?.vram_used_bytes ?? null)}
              unit="GiB"
              detail={
                gpu?.vram_total_bytes != null
                  ? `of ${formatGibibytes(gpu.vram_total_bytes)} GiB dedicated`
                  : null
              }
              unavailableReason={reasonFor(sensors, "gpu.vram_used_bytes")}
            />
            <Gauge
              value={gpu ? fraction(gpu.vram_used_bytes, gpu.vram_total_bytes) : null}
              label="Video memory in use"
              tone="cool"
            />

            <Metric
              label="Temperature"
              value={formatPercent(gpu?.temperature_c ?? null)}
              unit="°C"
              unavailableReason={reasonFor(sensors, "gpu.temperature_c")}
            />
          </>
        )}

        {panel === "memory" && (
          <>
            <Metric
              label="In use"
              value={formatGibibytes(memory?.used_bytes ?? null)}
              unit="GiB"
              detail={
                memory?.total_bytes != null
                  ? `of ${formatGibibytes(memory.total_bytes)} GiB installed`
                  : null
              }
              emphasis
              unavailableReason={reasonFor(sensors, "memory.used_bytes")}
            />
            <Gauge
              value={memory ? fraction(memory.used_bytes, memory.total_bytes) : null}
              label="Physical memory in use"
            />

            <Metric
              label="Committed"
              value={formatGibibytes(memory?.commit_used_bytes ?? null)}
              unit="GiB"
              detail={
                memory?.commit_limit_bytes != null
                  ? `of ${formatGibibytes(memory.commit_limit_bytes)} GiB limit`
                  : null
              }
              unavailableReason={reasonFor(sensors, "memory.commit_used_bytes")}
            />
            <Gauge
              value={memory ? fraction(memory.commit_used_bytes, memory.commit_limit_bytes) : null}
              label="Commit charge"
              tone="cool"
            />
          </>
        )}

        {panel === "cores" && (
          <CoreGrid
            cores={cpu?.per_core_percent ?? null}
            unavailableReason={reasonFor(sensors, "cpu.per_core_percent")}
          />
        )}

        {panel === "model" && (
          <ProviderControl
            mode={state.providerMode}
            model={state.providerModel}
            hasKey={state.hasKey}
            onSelect={selectProvider}
            onKey={setKey}
          />
        )}

        {panel === "vault" && <MemoryControl status={state.memory} onReindex={reindexMemory} />}

        {panel === "guard" && (
          <>
            <TrustControl enabled={state.trustEnabled} onChange={setTrust} />
            <DeclaredGaps sensors={sensors} />
          </>
        )}

        {panel === "link" && (
          <>
            <LinkStatus state={state} now={now} stale={stale} />
            <Counters
              droppedFrames={state.droppedFrames}
              missedSamples={state.missedSamples}
              lastError={state.lastError}
            />
          </>
        )}
      </Dock>

      {state.pendingApproval !== null && (
        <ApprovalDialog
          request={state.pendingApproval}
          onDecision={(approve) => decide(state.pendingApproval!.request_id, approve)}
        />
      )}
    </main>
  );
}
