using LocalZero.System.Telemetry;

namespace LocalZero.System.Sensors;

/// <summary>
/// Builds the sensor declaration sent in the hello message: every field the UI can display,
/// exactly once, available or not.
///
/// The declaration is derived from what this build can actually read on this machine, not from a
/// static list. If DXGI reports no hardware adapter, the GPU fields are declared unavailable with
/// a reason rather than declared available and then sent as null forever.
/// </summary>
internal static class SensorCatalog
{
    internal const string CpuTemperatureReason = "requires kernel driver - not installed";
    internal const string GpuTemperatureReason = "AMD ADLX not integrated - see ROADMAP M5";
    internal const string NoGraphicsAdapterReason = "no hardware graphics adapter reported by DXGI";

    internal static IReadOnlyList<SensorCapability> Build(bool hasGraphicsAdapter)
    {
        List<SensorCapability> capabilities =
        [
            SensorCapability.Readable("cpu.total_percent", SensorSource.PdhEnglish),
            SensorCapability.Readable("cpu.per_core_percent", SensorSource.PdhEnglish),

            // Read from PDH's Processor Frequency counter, not from a Win32 call, so the declared
            // source says pdh_english. The field is what the UI shows; the source is how it got
            // here, and the two must not drift.
            SensorCapability.Readable("cpu.frequency_mhz", SensorSource.PdhEnglish),

            // No ring-0 driver, therefore no source at any privilege level. Permanent.
            SensorCapability.Unavailable("cpu.temperature_c", CpuTemperatureReason),

            SensorCapability.Readable("memory.used_bytes", SensorSource.Win32Api),
            SensorCapability.Readable("memory.total_bytes", SensorSource.Win32Api),
            SensorCapability.Readable("memory.commit_used_bytes", SensorSource.Win32Api),
            SensorCapability.Readable("memory.commit_limit_bytes", SensorSource.Win32Api),
        ];

        capabilities.AddRange(BuildGpuCapabilities(hasGraphicsAdapter));
        capabilities.Add(SensorCapability.Readable("uptime_seconds", SensorSource.Win32Api));

        return capabilities;
    }

    private static IEnumerable<SensorCapability> BuildGpuCapabilities(bool hasGraphicsAdapter)
    {
        if (!hasGraphicsAdapter)
        {
            yield return SensorCapability.Unavailable("gpu.utilization_percent", NoGraphicsAdapterReason);
            yield return SensorCapability.Unavailable("gpu.vram_used_bytes", NoGraphicsAdapterReason);
            yield return SensorCapability.Unavailable("gpu.vram_total_bytes", NoGraphicsAdapterReason);
        }
        else
        {
            yield return SensorCapability.Readable("gpu.utilization_percent", SensorSource.PdhEnglish);
            yield return SensorCapability.Readable("gpu.vram_used_bytes", SensorSource.PdhEnglish);
            yield return SensorCapability.Readable("gpu.vram_total_bytes", SensorSource.Win32Api);
        }

        // Unavailable whether or not an adapter exists: the route to it is ADLX, and ADLX is an
        // unvalidated spike. Nothing may depend on it until M5.
        yield return SensorCapability.Unavailable("gpu.temperature_c", GpuTemperatureReason);
    }
}
