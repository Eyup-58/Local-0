using System.Runtime.InteropServices;

namespace LocalZero.System.Interop;

/// <summary>
/// P/Invoke surface for the plain Win32 system-information calls: physical memory, commit charge
/// and uptime. All three are readable unelevated - measured, see docs/ARCHITECTURE.md section 3.
/// </summary>
internal static class SystemInfoNative
{
    /// <summary>Physical and page-file totals, as reported to a normal user process.</summary>
    [StructLayout(LayoutKind.Sequential)]
    internal struct MemoryStatusEx
    {
        internal uint Length;
        internal uint MemoryLoad;
        internal ulong TotalPhys;
        internal ulong AvailPhys;
        internal ulong TotalPageFile;
        internal ulong AvailPageFile;
        internal ulong TotalVirtual;
        internal ulong AvailVirtual;
        internal ulong AvailExtendedVirtual;
    }

    /// <summary>
    /// System-wide performance information. The commit figures here are the ones Task Manager
    /// shows as "Committed", which is why commit is read from this call rather than derived from
    /// <see cref="MemoryStatusEx"/> page-file fields. Sizes are in pages, not bytes.
    /// </summary>
    [StructLayout(LayoutKind.Sequential)]
    internal struct PerformanceInformation
    {
        internal uint Size;
        internal nuint CommitTotal;
        internal nuint CommitLimit;
        internal nuint CommitPeak;
        internal nuint PhysicalTotal;
        internal nuint PhysicalAvailable;
        internal nuint SystemCache;
        internal nuint KernelTotal;
        internal nuint KernelPaged;
        internal nuint KernelNonpaged;
        internal nuint PageSize;
        internal uint HandleCount;
        internal uint ProcessCount;
        internal uint ThreadCount;
    }

    [DllImport("kernel32.dll", SetLastError = true, ExactSpelling = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool GlobalMemoryStatusEx(ref MemoryStatusEx buffer);

    [DllImport("kernel32.dll", ExactSpelling = true)]
    internal static extern ulong GetTickCount64();

    [DllImport("psapi.dll", SetLastError = true, ExactSpelling = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool GetPerformanceInfo(ref PerformanceInformation buffer, uint size);
}
