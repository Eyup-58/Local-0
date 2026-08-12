namespace LocalZero.System.Telemetry;

/// <summary>How a telemetry field is obtained. Mirrors the sensors[].source enum in the contract.</summary>
internal enum SensorSource
{
    /// <summary>
    /// PDH, resolved through the English counter API. The only permitted performance-counter
    /// source in this repository - see invariant L1.
    /// </summary>
    PdhEnglish,

    /// <summary>A direct Win32 call.</summary>
    Win32Api,

    /// <summary>WMI. Not used by any field today.</summary>
    Wmi,

    /// <summary>
    /// AMD ADLX. Carries gpu.temperature_c on a machine with an AMD display driver; the field is
    /// declared unavailable everywhere else. Validated in M5 - see GpuTemperatureSensor.
    /// </summary>
    Adlx,

    /// <summary>No source exists on this machine. Required when the field is unavailable.</summary>
    None,
}

/// <summary>
/// One entry of the sensor declaration the sidecar sends in its hello.
///
/// This is the honesty mechanism of the whole contract. Without it the UI sees
/// <c>cpu.temperature_c: null</c> and cannot tell a missing sensor from a transient read failure
/// from a bug. With it, the UI renders the reason verbatim and the user knows where they stand.
///
/// The schema enforces the pairing: an unavailable field must carry a non-null reason and
/// <see cref="SensorSource.None"/>. A silent gap is a rejected message.
/// </summary>
internal sealed record SensorCapability(
    string Field,
    bool Available,
    SensorSource Source,
    string? UnavailableReason)
{
    internal static SensorCapability Readable(string field, SensorSource source) =>
        new(field, Available: true, source, UnavailableReason: null);

    internal static SensorCapability Unavailable(string field, string reason) =>
        new(field, Available: false, SensorSource.None, reason);
}
