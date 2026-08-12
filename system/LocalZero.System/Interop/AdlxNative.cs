using System.Runtime.InteropServices;

namespace LocalZero.System.Interop;

/// <summary>
/// Minimal AMD ADLX interop, used for exactly one thing: GPU edge temperature.
///
/// ADLX is the only driverless route to it. This project loads no kernel driver (CLAUDE.md
/// invariant 10) and runs every process <c>asInvoker</c> (invariant 11), so if ADLX cannot supply
/// the number, nothing can and the field stays a labelled gap.
///
/// <para><b>The C interface is COM-shaped without being COM.</b> Each object is
/// <c>{ const Vtbl* pVtbl; }</c> and calls go through function pointers read out of that table by
/// index. There is no type safety here at all: a wrong index is an access violation, or worse, a
/// plausible-looking double that is not a temperature - which is precisely what invariant 10
/// exists to prevent.</para>
///
/// <para><b>So the layout was measured rather than trusted, on 2026-08-13.</b> The spike validated
/// it three ways before any value was believed:</para>
/// <list type="bullet">
///   <item><c>IADLXSystem</c> slot 10 (<c>TotalSystemRAM</c>) returned 65302 MB against the 63.8 GiB
///   Windows reports - the layout is right, because that number cannot be a coincidence.</item>
///   <item>Metrics <c>GPUVRAM</c> returned <b>2942 MB against PDH's 2942 MB</b>, exactly, read in
///   the same second. An exact match on a neighbouring slot is what proves the metrics object and
///   its offsets, and therefore that slot 7 is the temperature.</item>
///   <item><c>GPUUsage</c> 23.0% against PDH's 22.0%, and the temperature moved 49 C -> 51 C as
///   load rose 8% -> 23%. Hotspot read higher than edge, which is the physically correct
///   relationship.</item>
/// </list>
///
/// <para><b>What the spike did not establish:</b> <c>IADLXList</c> slot 3 returned 0 for a list
/// whose element 0 was then fetched successfully, so that slot is <i>not</i> Size, or not with that
/// signature. It is unused here and must stay unused until somebody measures what it is. One GPU is
/// all this needs, and <c>At(0)</c> is measured to work.</para>
///
/// <para><b>IADLXSystem is the one interface that is not reference counted</b> and does not begin
/// with Acquire/Release/QueryInterface, which is why its slot numbering starts at
/// <c>GetHybridGraphicsType</c> while every other interface here starts at Acquire.</para>
/// </summary>
internal static class AdlxNative
{
    /// <summary>Ships with the AMD display driver. Absent on any machine without one.</summary>
    private const string Library = "amdadlx64.dll";

    /// <summary>ADLX_OK. Every other value is a failure or an unsupported field.</summary>
    internal const int Ok = 0;

    // --- IADLXSystem, not reference counted, no Acquire/Release/QueryInterface prefix ------------

    internal const int SystemGetGpus = 1;
    internal const int SystemGetPerformanceMonitoringServices = 9;

    // --- IADLXGPUList: IADLXInterface (0-2) then IADLXList then the typed accessor ---------------

    /// <summary>The typed <c>At</c>, which hands back an IADLXGPU rather than a base interface.</summary>
    internal const int GpuListAt = 11;

    // --- IADLXPerformanceMonitoringServices ------------------------------------------------------

    internal const int PerformanceGetCurrentGpuMetrics = 18;

    // --- IADLXGPUMetrics -------------------------------------------------------------------------

    internal const int MetricsRelease = 1;
    internal const int MetricsGpuUsage = 4;
    internal const int MetricsGpuTemperature = 7;

    [DllImport(Library, CallingConvention = CallingConvention.Cdecl)]
    internal static extern int ADLXQueryFullVersion(out ulong fullVersion);

    [DllImport(Library, CallingConvention = CallingConvention.Cdecl)]
    internal static extern int ADLXInitialize(ulong version, out IntPtr system);

    [DllImport(Library, CallingConvention = CallingConvention.Cdecl)]
    internal static extern int ADLXTerminate();

    [UnmanagedFunctionPointer(CallingConvention.StdCall)]
    internal delegate int OutPointer(IntPtr self, out IntPtr value);

    [UnmanagedFunctionPointer(CallingConvention.StdCall)]
    internal delegate int AtIndex(IntPtr self, uint index, out IntPtr value);

    [UnmanagedFunctionPointer(CallingConvention.StdCall)]
    internal delegate int WithArgument(IntPtr self, IntPtr argument, out IntPtr value);

    [UnmanagedFunctionPointer(CallingConvention.StdCall)]
    internal delegate int OutDouble(IntPtr self, out double value);

    [UnmanagedFunctionPointer(CallingConvention.StdCall)]
    internal delegate int NoArguments(IntPtr self);

    /// <summary>
    /// Binds one vtable slot of <paramref name="instance"/> to a delegate.
    ///
    /// Every caller passes a named constant from above rather than a literal. A literal index in a
    /// call site is the form this mistake takes, and the mistake is unreadable memory.
    /// </summary>
    internal static TDelegate Method<TDelegate>(IntPtr instance, int slot)
        where TDelegate : Delegate
    {
        IntPtr table = Marshal.ReadIntPtr(instance);
        IntPtr function = Marshal.ReadIntPtr(table, slot * IntPtr.Size);

        return Marshal.GetDelegateForFunctionPointer<TDelegate>(function);
    }

    /// <summary>Drops a reference on any reference-counted ADLX interface.</summary>
    internal static void Release(IntPtr instance)
    {
        if (instance != IntPtr.Zero)
        {
            Method<NoArguments>(instance, MetricsRelease)(instance);
        }
    }
}
