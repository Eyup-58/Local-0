using System.Runtime.InteropServices;

namespace LocalZero.System.Interop;

/// <summary>
/// One entry of a wildcard counter read.
/// </summary>
/// <param name="Name">The PDH instance name, e.g. <c>0,14</c> or <c>luid_..._eng_0_engtype_3D</c>.</param>
/// <param name="Value">
/// The reading, or null when the instance exists but reported no usable value.
///
/// The distinction matters: "this instance is not in the result" and "this instance is present but
/// silent" are different facts. A parked CPU core is the second, and a consumer that cannot tell
/// them apart has to choose between inventing a value and discarding the whole result.
/// </param>
internal readonly record struct PdhInstance(string Name, double? Value);

/// <summary>
/// A live PDH query and the counters added to it. One collection tick refreshes every counter on
/// the query at once, which is what makes a sweep a single syscall rather than one per field.
///
/// Counters are added through <see cref="PdhNative.PdhAddEnglishCounterW"/> only - invariant L1.
/// </summary>
internal sealed class PdhQuery : IDisposable
{
    private readonly List<PdhCounter> _counters = [];
    private nint _handle;
    private bool _isDisposed;

    private PdhQuery(nint handle) => _handle = handle;

    /// <summary>
    /// True once <see cref="Collect"/> has run at least twice. Rate counters are computed from the
    /// difference between two collections, so the first read of one has no value and PDH reports
    /// PDH_INVALID_DATA. Callers use this to tell "not ready yet" from "sensor unavailable".
    /// </summary>
    internal bool HasBaseline { get; private set; }

    private int _collectionCount;

    internal static PdhQuery Open()
    {
        uint status = PdhNative.PdhOpenQueryW(null, 0, out nint handle);
        if (status != PdhNative.StatusSuccess)
        {
            throw new PdhException(nameof(PdhNative.PdhOpenQueryW), "<query>", status);
        }

        return new PdhQuery(handle);
    }

    /// <summary>
    /// Adds a counter by its English path. The path is resolved by the English API regardless of
    /// system locale - see <see cref="PdhNative"/> for why nothing else is acceptable here.
    /// </summary>
    internal PdhCounter AddEnglishCounter(string counterPath)
    {
        ObjectDisposedException.ThrowIf(_isDisposed, this);

        uint status = PdhNative.PdhAddEnglishCounterW(_handle, counterPath, 0, out nint counterHandle);
        if (status != PdhNative.StatusSuccess)
        {
            throw new PdhException(nameof(PdhNative.PdhAddEnglishCounterW), counterPath, status);
        }

        PdhCounter counter = new(counterHandle, counterPath);
        _counters.Add(counter);
        return counter;
    }

    /// <summary>Refreshes every counter on this query. Cheap relative to a per-counter read.</summary>
    internal void Collect()
    {
        ObjectDisposedException.ThrowIf(_isDisposed, this);

        uint status = PdhNative.PdhCollectQueryData(_handle);

        // No data at all is a legitimate transient state - every instance may simply have gone
        // away between ticks. It is not a reason to tear the query down.
        if (status is not (PdhNative.StatusSuccess or PdhNative.StatusNoData))
        {
            throw new PdhException(nameof(PdhNative.PdhCollectQueryData), "<query>", status);
        }

        _collectionCount++;
        if (_collectionCount >= 2)
        {
            HasBaseline = true;
        }
    }

    public void Dispose()
    {
        if (_isDisposed)
        {
            return;
        }

        _isDisposed = true;
        _counters.Clear();

        if (_handle != 0)
        {
            _ = PdhNative.PdhCloseQuery(_handle);
            _handle = 0;
        }
    }
}

/// <summary>A single counter on a <see cref="PdhQuery"/>. Read after the query has collected.</summary>
internal sealed class PdhCounter
{
    private readonly nint _handle;

    internal PdhCounter(nint handle, string counterPath)
    {
        _handle = handle;
        CounterPath = counterPath;
    }

    internal string CounterPath { get; }

    /// <summary>
    /// Reads a single-instance counter. Returns null when PDH has no value to give yet or the
    /// instance has gone away - both of which mean "unavailable this tick", never zero.
    /// </summary>
    internal double? ReadDouble()
    {
        uint status = PdhNative.PdhGetFormattedCounterValue(
            _handle, PdhNative.FormatDouble, 0, out PdhNative.FormattedValue value);

        if (IsTransientlyEmpty(status) || IsTransientlyEmpty(value.CStatus))
        {
            return null;
        }

        if (status != PdhNative.StatusSuccess)
        {
            throw new PdhException(nameof(PdhNative.PdhGetFormattedCounterValue), CounterPath, status);
        }

        return value.DoubleValue;
    }

    /// <summary>
    /// Reads every instance of a wildcard counter. An instance whose own CStatus reports no value
    /// is returned with a null <see cref="PdhInstance.Value"/> - present, but silent - rather than
    /// being omitted or reported as zero.
    /// </summary>
    /// <param name="asLargeInteger">
    /// Read the value as a 64-bit integer instead of a double. Byte counts overflow the exact
    /// range of a double at large magnitudes, so they are read as integers.
    /// </param>
    internal IReadOnlyList<PdhInstance> ReadInstances(bool asLargeInteger = false)
    {
        uint format = asLargeInteger ? PdhNative.FormatLarge : PdhNative.FormatDouble;

        uint bufferSize = 0;
        uint sizeProbeStatus = PdhNative.PdhGetFormattedCounterArrayW(
            _handle, format, ref bufferSize, out _, 0);

        if (IsTransientlyEmpty(sizeProbeStatus))
        {
            return [];
        }

        if (sizeProbeStatus != PdhNative.StatusMoreData)
        {
            throw new PdhException(
                nameof(PdhNative.PdhGetFormattedCounterArrayW) + " (size probe)", CounterPath, sizeProbeStatus);
        }

        nint buffer = Marshal.AllocHGlobal((int)bufferSize);
        try
        {
            uint status = PdhNative.PdhGetFormattedCounterArrayW(
                _handle, format, ref bufferSize, out uint itemCount, buffer);

            if (IsTransientlyEmpty(status))
            {
                return [];
            }

            if (status != PdhNative.StatusSuccess)
            {
                throw new PdhException(nameof(PdhNative.PdhGetFormattedCounterArrayW), CounterPath, status);
            }

            return ReadItems(buffer, itemCount, asLargeInteger);
        }
        finally
        {
            Marshal.FreeHGlobal(buffer);
        }
    }

    private static List<PdhInstance> ReadItems(nint buffer, uint itemCount, bool asLargeInteger)
    {
        int itemSize = Marshal.SizeOf<PdhNative.FormattedValueItem>();
        List<PdhInstance> instances = new((int)itemCount);

        for (uint i = 0; i < itemCount; i++)
        {
            nint itemPtr = buffer + (nint)(i * (uint)itemSize);
            PdhNative.FormattedValueItem item = Marshal.PtrToStructure<PdhNative.FormattedValueItem>(itemPtr);

            string? name = Marshal.PtrToStringUni(item.NamePtr);
            if (name is null)
            {
                continue;
            }

            double? value = item.Value.CStatus == PdhNative.StatusSuccess
                ? asLargeInteger ? item.Value.LargeValue : item.Value.DoubleValue
                : null;

            instances.Add(new PdhInstance(name, value));
        }

        return instances;
    }

    /// <summary>
    /// True for the statuses that mean "nothing to report right now": a rate counter that has not
    /// seen two collections yet, or a counter whose instances have disappeared. These become a
    /// null telemetry field, not an error and never a zero.
    ///
    /// PDH spells "no usable value yet" four different ways depending on which API is asked and
    /// whether the miss is at the counter or the instance level. Measured on this machine: the
    /// first sweep after startup returns PDH_CSTATUS_INVALID_DATA (0xC0000BBA) from the array API,
    /// and handling only PDH_INVALID_DATA (0xC0000BC6) made a normal warm-up tick look like a hard
    /// CPU and GPU sensor failure.
    /// </summary>
    private static bool IsTransientlyEmpty(uint status) => status
        is PdhNative.StatusInvalidData
        or PdhNative.StatusCounterInvalidData
        or PdhNative.StatusNoData
        or PdhNative.StatusNoInstance;
}
