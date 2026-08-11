using System.Globalization;
using System.Runtime.InteropServices;

namespace LocalZero.System.Interop;

/// <summary>
/// One physical graphics adapter, discovered through DXGI.
/// </summary>
/// <param name="Description">Adapter name as the driver reports it, e.g. "AMD Radeon RX 7800 XT".</param>
/// <param name="DedicatedVideoMemoryBytes">Total dedicated VRAM. Source for gpu.vram_total_bytes.</param>
/// <param name="PdhLuidToken">
/// The substring PDH embeds in every GPU instance name belonging to this adapter, e.g.
/// <c>luid_0x00000000_0x0000CF8C_</c>. Both <c>GPU Engine</c> and <c>GPU Adapter Memory</c>
/// instances carry it, so one token filters both counter sets down to this device.
/// </param>
internal sealed record GraphicsAdapter(
    string Description,
    ulong DedicatedVideoMemoryBytes,
    string PdhLuidToken)
{
    /// <summary>
    /// Picks the adapter the telemetry describes: the hardware adapter with the most dedicated
    /// video memory. Returns null when DXGI reports no hardware adapter at all, in which case
    /// every GPU field becomes a labelled gap rather than a guess.
    ///
    /// Software adapters are excluded. This machine exposes the Microsoft Basic Render Driver
    /// alongside the Radeon, and folding it in would mean summing a WARP device into the GPU
    /// readout.
    /// </summary>
    internal static GraphicsAdapter? SelectPrimary()
    {
        Guid interfaceId = DxgiNative.FactoryInterfaceId;
        int hr = DxgiNative.CreateDXGIFactory1(ref interfaceId, out DxgiNative.IDxgiFactory1 factory);
        if (hr != 0)
        {
            return null;
        }

        try
        {
            return SelectFrom(factory);
        }
        finally
        {
            _ = Marshal.ReleaseComObject(factory);
        }
    }

    private static GraphicsAdapter? SelectFrom(DxgiNative.IDxgiFactory1 factory)
    {
        GraphicsAdapter? best = null;

        for (uint index = 0; ; index++)
        {
            int hr = factory.EnumAdapters1(index, out DxgiNative.IDxgiAdapter1 adapter);
            if (hr != 0)
            {
                // DXGI_ERROR_NOT_FOUND ends the enumeration; anything else ends it too, because a
                // partial enumeration is still an honest answer about what could be seen.
                break;
            }

            try
            {
                GraphicsAdapter? candidate = Describe(adapter);
                if (candidate is not null &&
                    (best is null || candidate.DedicatedVideoMemoryBytes > best.DedicatedVideoMemoryBytes))
                {
                    best = candidate;
                }
            }
            finally
            {
                _ = Marshal.ReleaseComObject(adapter);
            }
        }

        return best;
    }

    private static GraphicsAdapter? Describe(DxgiNative.IDxgiAdapter1 adapter)
    {
        if (adapter.GetDesc1(out DxgiNative.AdapterDescription1 description) != 0)
        {
            return null;
        }

        bool isSoftware = (description.Flags & DxgiNative.AdapterFlagSoftware) != 0;
        if (isSoftware)
        {
            return null;
        }

        return new GraphicsAdapter(
            description.Description.Trim(),
            description.DedicatedVideoMemory,
            FormatLuidToken(description.AdapterLuid));
    }

    /// <summary>
    /// Renders a LUID the way PDH spells it inside GPU instance names: high part first, both
    /// halves as eight uppercase hex digits, with the trailing underscore that separates the token
    /// from whatever follows it (<c>phys_0</c>, <c>phys_0_eng_2_engtype_Compute</c>, ...).
    /// </summary>
    private static string FormatLuidToken(DxgiNative.Luid luid)
    {
        string high = unchecked((uint)luid.HighPart).ToString("X8", CultureInfo.InvariantCulture);
        string low = luid.LowPart.ToString("X8", CultureInfo.InvariantCulture);
        return $"luid_0x{high}_0x{low}_";
    }
}
