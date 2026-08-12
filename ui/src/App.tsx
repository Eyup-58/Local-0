/**
 * The panel, laid out as a heads-up display: one orb in the middle, readings around it.
 *
 * The arrangement changed in this pass; the rule underneath it did not. Every value on this page
 * still comes from the sidecar's declaration or its samples, and where a value is missing the
 * reason shown is the sidecar's own words. The orb obeys the same rule - it is driven by real load
 * and sits still and dim when there is none, rather than idling at a plausible-looking zero.
 *
 * What this layout deliberately does *not* borrow from the assistant HUDs it looks like: a caption
 * line that talks. This one reports state. It never says anything the panel did not do.
 */

import { useState } from "react";

import { ApprovalDialog } from "./components/ApprovalDialog";
import { Counters } from "./components/Counters";
import { CoreGrid } from "./components/CoreGrid";
import { DeclaredGaps } from "./components/DeclaredGaps";
import { Gauge } from "./components/Gauge";
import { Caption, Dock, Rail, ReadingStrip, TopBar } from "./components/Hud";
import type { LinkTone, RailItem, Reading } from "./components/Hud";
import { LinkStatus } from "./components/LinkStatus";
import { MemoryControl } from "./components/MemoryControl";
import { Metric } from "./components/Metric";
import { Orb } from "./components/Orb";
import type { OrbMood } from "./components/Orb";
import { ProviderControl } from "./components/ProviderControl";
import { TrustControl } from "./components/TrustControl";
import { formatGibibytes, formatMegahertz, formatPercent, formatUptime, fraction } from "./format";
import type { SensorCapability, TelemetryPayload } from "./contracts/types";
import { isStale } from "./ws/reducer";
import type { TelemetryState } from "./ws/reducer";
import { useTelemetry } from "./ws/useTelemetry";

/**
 * Used only when the sidecar has told us nothing at all about a field - before the first
 * handshake, or for a field its declaration did not mention. Anything the sidecar did explain is
 * shown in its words, not these.
 */
const NO_DECLARATION = "The system layer has not reported on this sensor.";

const PANELS: readonly RailItem[] = [
  { id: "processor", glyph: "▤", title: "Processor" },
  { id: "graphics", glyph: "◈", title: "Graphics" },
  { id: "memory", glyph: "▥", title: "Memory" },
  { id: "cores", glyph: "⠿", title: "Cores" },
  { id: "model", glyph: "◐", title: "Model" },
  { id: "vault", glyph: "❖", title: "Vault" },
  { id: "guard", glyph: "⌘", title: "Guard" },
  { id: "link", glyph: "⇄", title: "Link" },
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

/** The caption, in priority order. It reports; it does not narrate. */
function captionFor(state: TelemetryState, stale: boolean): { text: string; tone: LinkTone | "alarm" } {
  if (state.pendingApproval !== null) {
    return { text: `${state.pendingApproval.capability} is waiting for your decision.`, tone: "alarm" };
  }

  if (state.lastError !== null) return { text: state.lastError, tone: "alarm" };
  if (!state.systemConnected) {
    return { text: state.reason ?? "Waiting for the system layer.", tone: "waiting" };
  }

  if (stale) return { text: "The last sample is late. Nothing here is being estimated.", tone: "stale" };
  if (state.trustEnabled) {
    return { text: "Approval is switched off. The guard still runs; nothing stops to ask.", tone: "alarm" };
  }

  return { text: "All sensors reporting. Gaps below are declared, not guessed.", tone: "live" };
}

export function App() {
  const { state, now, decide, setTrust, selectProvider, setKey, reindexMemory } = useTelemetry();
  const [panel, setPanel] = useState("processor");

  const stale = isStale(state, now);
  const sample = state.sample;
  const sensors = state.sensors;

  const cpu = sample?.cpu ?? null;
  const memory = sample?.memory ?? null;
  const gpu = sample?.gpu ?? null;

  const tone = linkTone(state, stale);
  const caption = captionFor(state, stale);

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
    {
      label: "VRAM",
      value: formatGibibytes(gpu?.vram_used_bytes ?? null),
      unit: "GiB",
      reason: reasonFor(sensors, "gpu.vram_used_bytes"),
    },
    {
      label: "CLK",
      value: formatMegahertz(cpu?.frequency_mhz ?? null),
      unit: "MHz",
      reason: reasonFor(sensors, "cpu.frequency_mhz"),
    },
    {
      label: "UP",
      value: formatUptime(sample?.uptime_seconds ?? null),
      reason: NO_DECLARATION,
    },
  ];

  return (
    <main className={stale ? "hud hud--stale" : "hud"}>
      <TopBar
        tone={tone}
        trustEnabled={state.trustEnabled}
        providerMode={state.providerMode}
        clock={new Date(now).toLocaleTimeString()}
      />

      <ReadingStrip readings={readings} />

      <Rail items={PANELS} active={panel} onSelect={setPanel} />

      <div className="hud__stage">
        <Orb
          energy={orbEnergy(sample)}
          mood={orbMood(state, tone)}
          label={`System load ${formatPercent(cpu?.total_percent ?? null) ?? "unavailable"}`}
        />
        <Caption text={caption.text} tone={caption.tone} />
      </div>

      <Dock title={PANELS.find((item) => item.id === panel)?.title ?? ""}>
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
            <Gauge value={gpu ? fraction(gpu.utilization_percent, 100) : null} label="GPU utilization" />

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
                memory?.total_bytes != null ? `of ${formatGibibytes(memory.total_bytes)} GiB installed` : null
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
