namespace LocalZero.System.Telemetry;

/// <summary>
/// A sensor group that has just started failing.
///
/// Two messages on purpose. <see cref="UserMessage"/> goes on the wire, where the contract allows
/// user-safe prose under 500 characters and forbids stack traces, secrets and paths.
/// <see cref="Detail"/> goes to the log, where the actual status code is worth having.
/// </summary>
internal sealed record SensorFault(string SensorGroup, string UserMessage, string Detail);

/// <summary>
/// The outcome of one sweep: what was read, how long the read took, and any sensor group that
/// changed from working to failing on this tick.
/// </summary>
/// <param name="Duration">
/// Wall time for the sensor sweep alone, excluding the pipe write and everything downstream. This
/// is the raw input to budget P3 in docs/PERFORMANCE.md.
/// </param>
/// <param name="NewFaults">
/// Only transitions. A sensor that has been failing for an hour produces one entry, not 3600 -
/// see docs/ARCHITECTURE.md section 5.
/// </param>
internal sealed record SweepResult(
    TelemetryReading Reading,
    TimeSpan Duration,
    IReadOnlyList<SensorFault> NewFaults);
