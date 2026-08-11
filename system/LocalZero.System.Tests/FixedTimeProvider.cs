namespace LocalZero.System.Tests;

/// <summary>
/// A TimeProvider pinned to one instant, so envelope timestamps are assertable.
///
/// Hand-written rather than pulled from a testing package: one overridden method is not worth a
/// dependency, and this makes the fixed instant visible at the call site.
/// </summary>
internal sealed class FixedTimeProvider(DateTimeOffset now) : TimeProvider
{
    internal static readonly DateTimeOffset DefaultInstant =
        new(2026, 8, 11, 9, 14, 2, 117, TimeSpan.Zero);

    public override DateTimeOffset GetUtcNow() => now;
}
