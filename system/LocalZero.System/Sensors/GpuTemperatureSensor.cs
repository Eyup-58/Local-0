using LocalZero.System.Interop;

namespace LocalZero.System.Sensors;

/// <summary>
/// GPU edge temperature, through AMD ADLX.
///
/// Kept apart from <see cref="GpuSensor"/> on purpose. That one reads PDH counters that are always
/// present on any Windows machine; this one depends on an AMD driver being installed, and folding
/// them together would make a missing ADLX cost the utilization and VRAM figures too. Separate
/// sensors mean the temperature is the only field that goes null when ADLX is absent.
///
/// <para><b>Resolved the U1 open question on 2026-08-13.</b> ADLX was unvalidated since M0 and
/// nothing was allowed to depend on it. The spike ran in three stages against
/// <c>amdadlx64.dll</c> 1.5.0.124 on a Radeon RX 7800 XT, unelevated throughout, and the slot
/// layout was cross-checked against PDH before any value was believed - see
/// <see cref="AdlxNative"/> for the measurements.</para>
///
/// <para><b>Every failure is null, never a substitute.</b> No ADLX, no AMD GPU, an unsupported
/// metric, a non-zero result code: each one leaves the field empty and the UI draws a labelled gap.
/// Inferring a temperature from load would be invariant 10 broken in the exact way it names.</para>
/// </summary>
internal sealed class GpuTemperatureSensor : IDisposable
{
    /// <summary>Edge temperatures outside this range are a misread, not a reading.</summary>
    private const double MinPlausibleC = -50;
    private const double MaxPlausibleC = 150;

    private readonly IntPtr _gpu;
    private readonly IntPtr _performance;

    private bool _disposed;

    private GpuTemperatureSensor(IntPtr gpu, IntPtr performance)
    {
        _gpu = gpu;
        _performance = performance;
    }

    /// <summary>
    /// Starts an ADLX session and holds the one GPU it will report on, or returns null when ADLX
    /// is not usable on this machine.
    ///
    /// Null is an ordinary answer here: a machine with an NVIDIA card, or no AMD driver, or an
    /// older driver without ADLX reaches it. The sidecar starts either way.
    /// </summary>
    internal static GpuTemperatureSensor? TryCreate()
    {
        IntPtr system = AdlxSession.System;
        if (system == IntPtr.Zero)
        {
            return null;
        }

        IntPtr gpu = IntPtr.Zero;
        IntPtr performance = IntPtr.Zero;

        try
        {
            if (AdlxNative.Method<AdlxNative.OutPointer>(system, AdlxNative.SystemGetGpus)(system, out IntPtr gpus)
                != AdlxNative.Ok || gpus == IntPtr.Zero)
            {
                return null;
            }

            // Element 0 rather than a search. This product reports one adapter - the one DXGI
            // selects for utilization and VRAM - and a second AMD GPU would need the whole
            // telemetry shape to grow, not just this sensor.
            int atResult = AdlxNative.Method<AdlxNative.AtIndex>(gpus, AdlxNative.GpuListAt)(gpus, 0, out gpu);
            AdlxNative.Release(gpus);

            if (atResult != AdlxNative.Ok || gpu == IntPtr.Zero)
            {
                return null;
            }

            if (AdlxNative.Method<AdlxNative.OutPointer>(
                    system, AdlxNative.SystemGetPerformanceMonitoringServices)(system, out performance)
                != AdlxNative.Ok || performance == IntPtr.Zero)
            {
                AdlxNative.Release(gpu);
                return null;
            }

            return new GpuTemperatureSensor(gpu, performance);
        }
        catch (Exception exception) when (exception is not OutOfMemoryException)
        {
            // A vtable layout that moved under a future driver arrives here. Not a reason to fail
            // the sidecar; the field becomes a labelled gap like any other missing sensor.
            AdlxNative.Release(gpu);
            AdlxNative.Release(performance);
            return null;
        }
    }

    /// <summary>
    /// The current edge temperature in degrees Celsius, or null when it could not be read.
    ///
    /// A fresh metrics object per sample: it is a snapshot, and holding one would report the
    /// temperature at the moment the sensor opened for the life of the process.
    /// </summary>
    internal double? Read()
    {
        if (_disposed)
        {
            return null;
        }

        IntPtr metrics = IntPtr.Zero;
        try
        {
            if (AdlxNative.Method<AdlxNative.WithArgument>(
                    _performance, AdlxNative.PerformanceGetCurrentGpuMetrics)(_performance, _gpu, out metrics)
                != AdlxNative.Ok || metrics == IntPtr.Zero)
            {
                return null;
            }

            if (AdlxNative.Method<AdlxNative.OutDouble>(
                    metrics, AdlxNative.MetricsGpuTemperature)(metrics, out double celsius) != AdlxNative.Ok)
            {
                // A non-zero result is ADLX saying this card does not report the metric. Board power
                // answered this way during the spike, and the honest rendering of it is a gap.
                return null;
            }

            // A value outside anything a GPU can be is a misread of the vtable, not a reading, and
            // shipping it would be exactly the plausible-looking placeholder invariant 10 forbids.
            return celsius is >= MinPlausibleC and <= MaxPlausibleC ? celsius : null;
        }
        catch (Exception exception) when (exception is not OutOfMemoryException)
        {
            return null;
        }
        finally
        {
            AdlxNative.Release(metrics);
        }
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }

        // Releases this sensor's own references and nothing else. The ADLX session itself outlives
        // every sensor and is terminated at process exit - tearing it down here is what faulted.
        _disposed = true;
        AdlxNative.Release(_gpu);
        AdlxNative.Release(_performance);
    }
}
