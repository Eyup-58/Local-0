using System.Runtime.InteropServices;

namespace LocalZero.System.Interop;

/// <summary>
/// Raw P/Invoke surface for the Performance Data Helper (pdh.dll).
///
/// BUILD INVARIANT L1 - counters are resolved through <c>PdhAddEnglishCounterW</c> and nothing
/// else. This machine reports its counter set names in Turkish, so the English path
/// <c>\Processor Information(_Total)\% Processor Time</c> fails through the localized
/// <c>PdhAddCounterW</c> with 0xC0000BB8 (PDH_CSTATUS_NO_OBJECT) while the GPU sets happen to
/// resolve. A localized lookup therefore *half works*: GPU telemetry appears, CPU telemetry
/// silently returns nothing, and it reads as a bug in the CPU sampler for hours.
///
/// Measured on this machine 2026-08-11 - English API returned 15.59, localized API returned
/// 0xC0000BB8 for the identical path.
///
/// <c>System.Diagnostics.PerformanceCounter</c> resolves by localized name and is banned
/// repository-wide. See docs/ARCHITECTURE.md section 0 and CLAUDE.md invariant 9.
/// </summary>
internal static class PdhNative
{
    internal const uint StatusSuccess = 0x0000_0000;
    internal const uint StatusMoreData = 0x8000_07D2;
    internal const uint StatusNoData = 0x8000_07D5;
    internal const uint StatusNoObject = 0xC000_0BB8;
    internal const uint StatusNoCounter = 0xC000_0BB9;

    /// <summary>PDH_CSTATUS_NO_INSTANCE - the counter exists, this instance of it does not.</summary>
    internal const uint StatusNoInstance = 0x8000_07D1;

    /// <summary>
    /// PDH_CSTATUS_INVALID_DATA - the counter resolved but has no usable value yet. This is what
    /// the array API returns for a rate counter that has only been collected once.
    /// </summary>
    internal const uint StatusCounterInvalidData = 0xC000_0BBA;

    /// <summary>
    /// PDH_INVALID_DATA - the same idea as <see cref="StatusCounterInvalidData"/> but raised by
    /// the single-value API. The two codes are distinct and both have to be handled: treating only
    /// one as transient makes the first tick after startup look like a hard sensor failure.
    /// </summary>
    internal const uint StatusInvalidData = 0xC000_0BC6;

    /// <summary>Format the value as a double.</summary>
    internal const uint FormatDouble = 0x0000_0200;

    /// <summary>Format the value as a 64-bit integer.</summary>
    internal const uint FormatLarge = 0x0000_0400;

    /// <summary>
    /// A single formatted counter value. The union starts at offset 8 because the leading
    /// CStatus field is padded out to the alignment of the widest union member.
    /// </summary>
    [StructLayout(LayoutKind.Explicit)]
    internal struct FormattedValue
    {
        [FieldOffset(0)] internal uint CStatus;
        [FieldOffset(8)] internal double DoubleValue;
        [FieldOffset(8)] internal long LargeValue;
    }

    /// <summary>One entry of a wildcard counter's formatted array: instance name plus value.</summary>
    [StructLayout(LayoutKind.Sequential)]
    internal struct FormattedValueItem
    {
        internal nint NamePtr;
        internal FormattedValue Value;
    }

    [DllImport("pdh.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
    internal static extern uint PdhOpenQueryW(string? dataSource, nint userData, out nint query);

    /// <summary>
    /// The only permitted way to resolve a counter path in this repository. Resolves English
    /// counter names under any system locale. See the type-level remarks - this is invariant L1.
    /// </summary>
    [DllImport("pdh.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
    internal static extern uint PdhAddEnglishCounterW(nint query, string counterPath, nint userData, out nint counter);

    [DllImport("pdh.dll", ExactSpelling = true)]
    internal static extern uint PdhCollectQueryData(nint query);

    [DllImport("pdh.dll", ExactSpelling = true)]
    internal static extern uint PdhGetFormattedCounterValue(nint counter, uint format, nint counterType, out FormattedValue value);

    [DllImport("pdh.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
    internal static extern uint PdhGetFormattedCounterArrayW(nint counter, uint format, ref uint bufferSize, out uint itemCount, nint buffer);

    [DllImport("pdh.dll", ExactSpelling = true)]
    internal static extern uint PdhCloseQuery(nint query);
}
