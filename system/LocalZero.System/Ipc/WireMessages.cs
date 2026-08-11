namespace LocalZero.System.Ipc;

// The wire shapes for contracts/ipc.schema.json, and nothing else. Domain types live in
// Telemetry/; these exist so that the contract has exactly one representation in this codebase and
// a schema change shows up here as a compile error rather than as a silently different payload.
//
// Rules the schema imposes on every type in this file:
//   * additionalProperties is false everywhere, so no property may be added without a contract
//     change. See docs/CONTRACTS.md section 6.
//   * every field is required, including the nullable ones, so nulls are written rather than
//     omitted. The serializer must not be configured to skip them.
//   * property names are snake_case, applied by the naming policy in IpcJson.

/// <summary>system -> brain. First message on every new pipe connection.</summary>
internal sealed record HelloMessage(int V, string Id, string Ts, string Type, HelloPayload Payload);

/// <param name="Elevated">
/// Always false. The schema declares it const false: a sidecar claiming elevation is a contract
/// violation and the brain refuses the connection rather than trusting a component that is already
/// behaving unexpectedly.
/// </param>
internal sealed record HelloPayload(
    string Component,
    string AppVersion,
    bool Elevated,
    int PollIntervalMs,
    IReadOnlyList<SensorDeclaration> Sensors);

/// <param name="UnavailableReason">
/// Non-null exactly when <paramref name="Available"/> is false, and shown to the user verbatim.
/// The schema enforces the pairing, along with Source being "none".
/// </param>
internal sealed record SensorDeclaration(
    string Field,
    bool Available,
    string Source,
    string? UnavailableReason);

/// <summary>system -> brain. One reading, emitted on the sidecar's own tick.</summary>
internal sealed record TelemetrySampleMessage(int V, string Id, string Ts, string Type, TelemetryPayload Payload);

/// <param name="Seq">Monotonic from 0 per connection. A gap tells the consumer samples were dropped.</param>
/// <param name="SampledAt">
/// When the machine was read, as distinct from the envelope Ts, which is when the message was
/// built. Under load these differ, and conflating them makes latency unmeasurable.
/// </param>
internal sealed record TelemetryPayload(
    long Seq,
    string SampledAt,
    CpuPayload Cpu,
    MemoryPayload Memory,
    GpuPayload Gpu,
    long? UptimeSeconds);

/// <param name="PerCorePercent">
/// Nullable entries: a parked core keeps its slot and carries null, because position is the core's
/// identity and compacting the array would misattribute every core after the gap. Widened from
/// non-nullable numbers on 2026-08-11 - see docs/CONTRACTS.md section 5.
/// </param>
internal sealed record CpuPayload(
    double? TotalPercent,
    IReadOnlyList<double?>? PerCorePercent,
    double? FrequencyMhz,
    double? TemperatureC);

internal sealed record MemoryPayload(
    long? UsedBytes,
    long? TotalBytes,
    long? CommitUsedBytes,
    long? CommitLimitBytes);

internal sealed record GpuPayload(
    double? UtilizationPercent,
    long? VramUsedBytes,
    long? VramTotalBytes,
    double? TemperatureC);

/// <summary>Either direction. Reports a fault without terminating the connection.</summary>
internal sealed record ErrorMessage(int V, string Id, string Ts, string Type, ErrorPayload Payload);

/// <param name="Message">
/// User-safe prose, under 500 characters. It may not carry a secret, a stack trace, or a
/// filesystem path. Detail for debugging goes to the log, not onto the wire.
/// </param>
internal sealed record ErrorPayload(string Code, string Message, string? InReplyTo);
