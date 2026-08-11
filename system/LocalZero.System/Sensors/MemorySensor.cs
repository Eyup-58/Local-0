using System.ComponentModel;
using System.Runtime.InteropServices;
using LocalZero.System.Interop;
using LocalZero.System.Telemetry;

namespace LocalZero.System.Sensors;

/// <summary>
/// Physical memory and commit charge. Plain Win32, no counters and no privilege.
///
/// Physical figures come from GlobalMemoryStatusEx and commit figures from GetPerformanceInfo,
/// because the latter is what Task Manager shows as "Committed". Deriving commit from the
/// page-file fields of GlobalMemoryStatusEx gives a subtly different number, and a telemetry panel
/// that disagrees with Task Manager for unexplained reasons is a panel nobody trusts.
/// </summary>
internal static class MemorySensor
{
    internal static MemoryReading Read()
    {
        (long? usedBytes, long? totalBytes) = ReadPhysical();
        (long? commitUsedBytes, long? commitLimitBytes) = ReadCommit();

        return new MemoryReading(usedBytes, totalBytes, commitUsedBytes, commitLimitBytes);
    }

    private static (long? Used, long? Total) ReadPhysical()
    {
        SystemInfoNative.MemoryStatusEx status = default;
        status.Length = (uint)Marshal.SizeOf<SystemInfoNative.MemoryStatusEx>();

        if (!SystemInfoNative.GlobalMemoryStatusEx(ref status))
        {
            throw new Win32Exception(Marshal.GetLastWin32Error(), "GlobalMemoryStatusEx failed.");
        }

        long total = ToInt64(status.TotalPhys);
        long used = ToInt64(status.TotalPhys - status.AvailPhys);
        return (used, total);
    }

    private static (long? Used, long? Limit) ReadCommit()
    {
        SystemInfoNative.PerformanceInformation info = default;
        uint size = (uint)Marshal.SizeOf<SystemInfoNative.PerformanceInformation>();
        info.Size = size;

        if (!SystemInfoNative.GetPerformanceInfo(ref info, size))
        {
            throw new Win32Exception(Marshal.GetLastWin32Error(), "GetPerformanceInfo failed.");
        }

        // The struct counts pages, not bytes.
        ulong pageSize = info.PageSize;
        long used = ToInt64((ulong)info.CommitTotal * pageSize);
        long limit = ToInt64((ulong)info.CommitLimit * pageSize);
        return (used, limit);
    }

    /// <summary>
    /// Narrows an unsigned byte count to the signed integer the contract uses. Saturating rather
    /// than wrapping: a machine with more than 8 exabytes of RAM does not exist, and if one ever
    /// does, a clamped maximum is a less misleading answer than a negative byte count.
    /// </summary>
    private static long ToInt64(ulong value) => value > long.MaxValue ? long.MaxValue : (long)value;
}
