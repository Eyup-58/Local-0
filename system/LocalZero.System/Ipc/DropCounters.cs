namespace LocalZero.System.Ipc;

/// <summary>
/// Counts messages the sidecar refused to act on.
///
/// The contract requires that a failed message is dropped <b>and counted</b>, never partially
/// applied - a silent drop is indistinguishable from a peer that never sent anything. These
/// counters are what makes "dropped" observable, and they are what the boundary tests assert on.
/// </summary>
internal sealed class DropCounters
{
    private long _schemaViolations;
    private long _unsupportedVersions;
    private long _oversizedLines;

    /// <summary>Inbound messages that did not match the contract's shape.</summary>
    internal long SchemaViolations => Interlocked.Read(ref _schemaViolations);

    /// <summary>Inbound messages carrying a contract version this build does not implement.</summary>
    internal long UnsupportedVersions => Interlocked.Read(ref _unsupportedVersions);

    /// <summary>Inbound lines that exceeded the framing length limit and were discarded.</summary>
    internal long OversizedLines => Interlocked.Read(ref _oversizedLines);

    internal long Total => SchemaViolations + UnsupportedVersions + OversizedLines;

    internal void RecordSchemaViolation() => Interlocked.Increment(ref _schemaViolations);

    internal void RecordUnsupportedVersion() => Interlocked.Increment(ref _unsupportedVersions);

    internal void RecordOversizedLine() => Interlocked.Increment(ref _oversizedLines);
}
