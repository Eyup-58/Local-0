using System.Runtime.InteropServices;

namespace LocalZero.System.Interop;

/// <summary>
/// Minimal DXGI interop, used for exactly two things: the adapter's total dedicated video memory,
/// and its LUID.
///
/// The LUID is the important half. PDH names its GPU instances after it
/// (<c>luid_0x00000000_0x0000CF8C_phys_0</c>), and this machine reports two adapters - the Radeon
/// and the Microsoft Basic Render Driver. Summing every PDH instance would fold a software adapter
/// into the GPU readout. Selecting one adapter here and matching its LUID downstream keeps
/// utilization, VRAM used and VRAM total describing the same physical device.
///
/// Measured 2026-08-11: adapter 0 = "AMD Radeon RX 7800 XT", 16963137536 bytes dedicated,
/// luid_0x00000000_0x0000CF8C; adapter 1 = "Microsoft Basic Render Driver", software, 0 bytes.
/// </summary>
internal static class DxgiNative
{
    internal const int ErrorNotFound = unchecked((int)0x887A_0002);

    /// <summary>DXGI_ADAPTER_FLAG_SOFTWARE - a rendering adapter with no hardware behind it.</summary>
    internal const uint AdapterFlagSoftware = 2;

    internal static readonly Guid FactoryInterfaceId = new("770aae78-f26f-4dba-a829-253c83d1b387");

    [StructLayout(LayoutKind.Sequential)]
    internal struct Luid
    {
        internal uint LowPart;
        internal int HighPart;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    internal struct AdapterDescription1
    {
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)]
        internal string Description;

        internal uint VendorId;
        internal uint DeviceId;
        internal uint SubSysId;
        internal uint Revision;
        internal nuint DedicatedVideoMemory;
        internal nuint DedicatedSystemMemory;
        internal nuint SharedSystemMemory;
        internal Luid AdapterLuid;
        internal uint Flags;
    }

    /// <summary>
    /// IDXGIAdapter1. Only GetDesc1 is called; the preceding members exist to hold their vtable
    /// slots in the inherited order (IDXGIObject, then IDXGIAdapter). Do not reorder them.
    /// </summary>
    [ComImport]
    [Guid("29038f61-3839-4626-91fd-086879011a05")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IDxgiAdapter1
    {
        // IDXGIObject
        void SetPrivateData();
        void SetPrivateDataInterface();
        void GetPrivateData();
        void GetParent();

        // IDXGIAdapter
        void EnumOutputs();
        void GetDesc();
        void CheckInterfaceSupport();

        // IDXGIAdapter1
        [PreserveSig]
        int GetDesc1(out AdapterDescription1 description);
    }

    /// <summary>
    /// IDXGIFactory1. As above - only EnumAdapters1 is called, and the earlier members are vtable
    /// placeholders in inherited order (IDXGIObject, then IDXGIFactory). Do not reorder them.
    /// </summary>
    [ComImport]
    [Guid("770aae78-f26f-4dba-a829-253c83d1b387")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IDxgiFactory1
    {
        // IDXGIObject
        void SetPrivateData();
        void SetPrivateDataInterface();
        void GetPrivateData();
        void GetParent();

        // IDXGIFactory
        void EnumAdapters();
        void MakeWindowAssociation();
        void GetWindowAssociation();
        void CreateSwapChain();
        void CreateSoftwareAdapter();

        // IDXGIFactory1
        [PreserveSig]
        int EnumAdapters1(uint index, out IDxgiAdapter1 adapter);

        [PreserveSig]
        int IsCurrent();
    }

    [DllImport("dxgi.dll", ExactSpelling = true)]
    internal static extern int CreateDXGIFactory1(ref Guid interfaceId, out IDxgiFactory1 factory);
}
