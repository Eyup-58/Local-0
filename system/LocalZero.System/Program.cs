using LocalZero.System.Diagnostics;
using LocalZero.System.Ipc;
using LocalZero.System.Telemetry;

// Local Zero system sidecar. Reads this machine and streams telemetry to the brain over a named
// pipe whose ACL admits only the current user. It runs asInvoker and prompts for nothing.

const int PollIntervalMs = 1000;
const int ExitSuccess = 0;
const int ExitRefusedElevated = 2;

TimeProvider timeProvider = TimeProvider.System;
ILog log = new ConsoleLog(timeProvider);

if (ElevationGuard.IsElevated())
{
    log.Warn("refusing to start elevated. Local Zero runs every process asInvoker - see "
        + "docs/ARCHITECTURE.md section 2. Start this sidecar from an unelevated shell.");
    return ExitRefusedElevated;
}

using CancellationTokenSource shutdown = new();
Console.CancelKeyPress += (_, eventArgs) =>
{
    // Handle the signal ourselves so the pipe closes cleanly rather than being torn down.
    eventArgs.Cancel = true;
    shutdown.Cancel();
};

using SensorSweep sweep = SensorSweep.Create(log.Warn);
DropCounters counters = new();

TelemetryPipeServer server = new(
    sweep,
    new MessageFactory(timeProvider),
    timeProvider,
    TimeSpan.FromMilliseconds(PollIntervalMs),
    log,
    counters);

try
{
    await server.RunAsync(shutdown.Token);
}
catch (OperationCanceledException)
{
    // Ctrl+C. An ordinary shutdown.
}

log.Info($"stopped. dropped inbound messages: schema={counters.SchemaViolations} "
    + $"version={counters.UnsupportedVersions} oversized={counters.OversizedLines}");

return ExitSuccess;
