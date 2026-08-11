namespace LocalZero.System.Telemetry;

/// <summary>
/// One sweep of the machine, in domain terms. Mirrors the telemetry.sample payload in
/// contracts/ipc.schema.json.
///
/// Every field is nullable and <b>null means unavailable</b>. It is never rendered as zero, never
/// interpolated, never inferred from load. See CLAUDE.md invariant 10.
/// </summary>
internal sealed record TelemetryReading(
    CpuReading Cpu,
    MemoryReading Memory,
    GpuReading Gpu,
    long? UptimeSeconds);

/// <param name="TemperatureC">
/// Permanently null on this machine. CPU temperature needs a ring-0 driver, which this project
/// will not load, so no privilege level makes it readable. See docs/ARCHITECTURE.md section 3.
/// </param>
/// <param name="PerCorePercent">
/// One entry per logical processor, ordered by processor group then core number, or null when a
/// complete instance set was not available. A null <i>entry</i> is a core Windows had parked, which
/// keeps its slot so that position stays the core's identity.
/// </param>
internal sealed record CpuReading(
    double? TotalPercent,
    IReadOnlyList<double?>? PerCorePercent,
    double? FrequencyMhz,
    double? TemperatureC);

internal sealed record MemoryReading(
    long? UsedBytes,
    long? TotalBytes,
    long? CommitUsedBytes,
    long? CommitLimitBytes);

/// <param name="TemperatureC">
/// Null until the AMD ADLX spike in M5 resolves. Nothing may depend on it before then - see the
/// U1 open question in docs/ARCHITECTURE.md.
/// </param>
internal sealed record GpuReading(
    double? UtilizationPercent,
    long? VramUsedBytes,
    long? VramTotalBytes,
    double? TemperatureC);
