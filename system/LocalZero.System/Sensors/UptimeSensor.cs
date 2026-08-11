using LocalZero.System.Interop;

namespace LocalZero.System.Sensors;

/// <summary>Time since the machine last booted, from the Win32 tick count.</summary>
internal static class UptimeSensor
{
    private const ulong MillisecondsPerSecond = 1000;

    internal static long Read() => (long)(SystemInfoNative.GetTickCount64() / MillisecondsPerSecond);
}
