namespace LocalZero.System.Telemetry;

/// <summary>
/// Remembers which sensor groups are currently failing, so an error is emitted on the transition
/// into failure rather than on every tick.
///
/// This is the difference between one error message and 3600 of them for a sensor that has been
/// unreadable for an hour. docs/ARCHITECTURE.md section 5 states it as a requirement: "an error
/// message is emitted once per transition, not once per tick".
///
/// Extracted from the sweep so the rule can be tested on its own, without needing a sensor that
/// fails on command.
/// </summary>
internal sealed class FaultTracker
{
    private readonly HashSet<string> _failingGroups = new(StringComparer.Ordinal);

    /// <summary>
    /// Records a failure. Returns true only the first time a group fails, which is the caller's
    /// signal to emit an error.
    /// </summary>
    internal bool ShouldReport(string group)
    {
        ArgumentNullException.ThrowIfNull(group);
        return _failingGroups.Add(group);
    }

    /// <summary>
    /// Records a success. Recovery is silent - the values reappearing is the signal, and the
    /// contract has no message type for "a sensor started working again".
    /// </summary>
    internal void Clear(string group)
    {
        ArgumentNullException.ThrowIfNull(group);
        _ = _failingGroups.Remove(group);
    }

    internal bool IsFailing(string group) => _failingGroups.Contains(group);
}
