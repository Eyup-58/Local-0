/**
 * The telemetry panel.
 *
 * Every value on this page comes from the sidecar's declaration or its samples. Where a value is
 * missing, the reason shown is the sidecar's own words, looked up from the sensor declaration -
 * this layer never composes an explanation of its own for hardware it cannot see.
 */

import { ApprovalDialog } from "./components/ApprovalDialog";
import { Counters } from "./components/Counters";
import { CoreGrid } from "./components/CoreGrid";
import { DeclaredGaps } from "./components/DeclaredGaps";
import { Gauge } from "./components/Gauge";
import { LinkStatus } from "./components/LinkStatus";
import { Metric } from "./components/Metric";
import { TrustControl } from "./components/TrustControl";
import { formatGibibytes, formatMegahertz, formatPercent, formatUptime, fraction } from "./format";
import type { SensorCapability } from "./contracts/types";
import { isStale } from "./ws/reducer";
import { useTelemetry } from "./ws/useTelemetry";

/**
 * Used only when the sidecar has told us nothing at all about a field - before the first
 * handshake, or for a field its declaration did not mention. Anything the sidecar did explain is
 * shown in its words, not these.
 */
const NO_DECLARATION = "The system layer has not reported on this sensor.";

function reasonFor(sensors: readonly SensorCapability[], field: string): string {
  const declared = sensors.find((sensor) => sensor.field === field);
  return declared?.unavailable_reason ?? NO_DECLARATION;
}

export function App() {
  const { state, now, decide, setTrust } = useTelemetry();
  const stale = isStale(state, now);
  const sample = state.sample;
  const sensors = state.sensors;

  const cpu = sample?.cpu ?? null;
  const memory = sample?.memory ?? null;
  const gpu = sample?.gpu ?? null;

  return (
    <main className={stale ? "shell shell--stale" : "shell"}>
      <header className="masthead">
        <h1 className="masthead__title">Local Zero</h1>
        <p className="masthead__note">Driverless telemetry, read unelevated. Nothing here is estimated.</p>
      </header>

      <LinkStatus state={state} now={now} stale={stale} />

      <TrustControl enabled={state.trustEnabled} onChange={setTrust} />

      {state.pendingApproval !== null && (
        <ApprovalDialog
          request={state.pendingApproval}
          onDecision={(approve) => decide(state.pendingApproval!.request_id, approve)}
        />
      )}

      <div className="grid">
        <section className="panel">
          <div className="panel__head">
            <p className="eyebrow">Processor</p>
            <p className="masthead__note">{formatUptime(sample?.uptime_seconds ?? null) ?? ""} uptime</p>
          </div>

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
        </section>

        <section className="panel">
          <div className="panel__head">
            <p className="eyebrow">Graphics</p>
          </div>

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
              gpu?.vram_total_bytes != null ? `of ${formatGibibytes(gpu.vram_total_bytes)} GiB dedicated` : null
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
        </section>

        <section className="panel">
          <div className="panel__head">
            <p className="eyebrow">Memory</p>
          </div>

          <Metric
            label="In use"
            value={formatGibibytes(memory?.used_bytes ?? null)}
            unit="GiB"
            detail={memory?.total_bytes != null ? `of ${formatGibibytes(memory.total_bytes)} GiB installed` : null}
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
        </section>
      </div>

      <section className="panel">
        <CoreGrid
          cores={cpu?.per_core_percent ?? null}
          unavailableReason={reasonFor(sensors, "cpu.per_core_percent")}
        />
      </section>

      <DeclaredGaps sensors={sensors} />

      <Counters
        droppedFrames={state.droppedFrames}
        missedSamples={state.missedSamples}
        lastError={state.lastError}
      />
    </main>
  );
}
