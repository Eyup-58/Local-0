namespace LocalZero.System.Diagnostics;

/// <summary>
/// Emits per-tick sweep durations to the log when enabled by environment variable.
///
/// Budget P3 in docs/PERFORMANCE.md is the wall time of a sensor sweep, and the IPC contract has
/// no field for it - correctly, since it is the sidecar's own diagnostic and not something the UI
/// should render. Adding one would be a contract change made for the convenience of a benchmark,
/// which is the wrong reason to change a contract.
///
/// So the number leaves through the log instead, only when asked for. Off by default: a line per
/// second on stderr forever is a cost the product should not pay to make itself measurable.
/// </summary>
internal static class BenchMode
{
    private const string Variable = "LOCALZERO_BENCH";

    /// <summary>Prefix the bench scripts parse. Changing it breaks bench/poll_latency.py.</summary>
    internal const string SweepPrefix = "bench sweep_ms=";

    internal static bool IsEnabled { get; } =
        string.Equals(Environment.GetEnvironmentVariable(Variable), "1", StringComparison.Ordinal);
}
