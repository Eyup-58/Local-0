using System.Diagnostics;
using LocalZero.System.Sensors;

namespace LocalZero.System.Telemetry;

/// <summary>
/// Reads every sensor group once and assembles a <see cref="TelemetryReading"/>.
///
/// Two behaviours here are contract-level requirements rather than niceties:
///
/// <list type="bullet">
/// <item>A sensor group that throws makes <b>its own</b> fields null for that sample and leaves
/// every other group untouched. Groups own separate PDH queries so a broken one cannot take a
/// working one down with it.</item>
/// <item>An error is emitted <b>once per transition</b> into failure, not once per tick. A GPU
/// that has been unreadable for an hour produces one error, not 3600.</item>
/// </list>
///
/// <see cref="Read"/> does not throw. A sweep that cannot read anything returns a reading full of
/// nulls, which is the honest answer, rather than terminating the loop.
/// </summary>
internal sealed class SensorSweep : IDisposable
{
    private const string CpuGroup = "cpu";
    private const string MemoryGroup = "memory";
    private const string GpuGroup = "gpu";
    private const string UptimeGroup = "uptime";

    private static readonly CpuReading UnreadableCpu = new(null, null, null, null);
    private static readonly MemoryReading UnreadableMemory = new(null, null, null, null);
    private static readonly GpuReading UnreadableGpu = new(null, null, null, null);

    private readonly CpuSensor? _cpuSensor;
    private readonly GpuSensor? _gpuSensor;
    private readonly FaultTracker _faults = new();

    /// <summary>
    /// Builds a sweep over the sensors given. A null sensor group is one that could not be opened;
    /// its fields stay null for the life of the process while every other group keeps working.
    /// Internal so tests can compose a sweep with a group deliberately missing.
    /// </summary>
    internal SensorSweep(CpuSensor? cpuSensor, GpuSensor? gpuSensor)
    {
        _cpuSensor = cpuSensor;
        _gpuSensor = gpuSensor;
    }

    /// <summary>
    /// True when DXGI reported a hardware adapter and the GPU counters opened. Drives the sensor
    /// declaration so the UI is told up front whether GPU fields will ever carry a value.
    /// </summary>
    internal bool HasGraphicsAdapter => _gpuSensor is not null;

    /// <summary>
    /// Opens every sensor group. A group that cannot be opened at all is left closed and its
    /// fields stay null for the life of the process - the sidecar still starts, because partial
    /// telemetry that says what is missing beats no telemetry.
    /// </summary>
    internal static SensorSweep Create(Action<string> logWarning)
    {
        ArgumentNullException.ThrowIfNull(logWarning);

        CpuSensor? cpuSensor = TryOpen(CpuSensor.Create, CpuGroup, logWarning);
        GpuSensor? gpuSensor = TryOpen(GpuSensor.TryCreate, GpuGroup, logWarning);

        return new SensorSweep(cpuSensor, gpuSensor);
    }

    private static T? TryOpen<T>(Func<T?> open, string group, Action<string> logWarning)
        where T : class
    {
        try
        {
            return open();
        }
        catch (Exception exception) when (exception is not OutOfMemoryException)
        {
            logWarning($"{group} sensors could not be opened, their fields will stay null: {exception.Message}");
            return null;
        }
    }

    internal SweepResult Read()
    {
        long startedAt = Stopwatch.GetTimestamp();
        List<SensorFault> faults = [];

        CpuReading cpu = ReadGroup(CpuGroup, faults, UnreadableCpu, () =>
            _cpuSensor is null ? UnreadableCpu : _cpuSensor.Read());

        MemoryReading memory = ReadGroup(MemoryGroup, faults, UnreadableMemory, MemorySensor.Read);

        GpuReading gpu = ReadGroup(GpuGroup, faults, UnreadableGpu, () =>
            _gpuSensor is null ? UnreadableGpu : _gpuSensor.Read());

        long? uptimeSeconds = ReadGroup<long?>(UptimeGroup, faults, null, () => UptimeSensor.Read());

        TimeSpan duration = Stopwatch.GetElapsedTime(startedAt);
        return new SweepResult(new TelemetryReading(cpu, memory, gpu, uptimeSeconds), duration, faults);
    }

    /// <summary>
    /// Runs one sensor group, converting a failure into null fields plus at most one fault. The
    /// fault is recorded only on the transition into failure; recovery clears the flag silently,
    /// because the values reappearing is the signal.
    /// </summary>
    private TReading ReadGroup<TReading>(
        string group,
        List<SensorFault> faults,
        TReading unreadable,
        Func<TReading> read)
    {
        try
        {
            TReading reading = read();
            _faults.Clear(group);
            return reading;
        }
        catch (Exception exception) when (exception is not OutOfMemoryException)
        {
            if (_faults.ShouldReport(group))
            {
                faults.Add(new SensorFault(
                    group,
                    $"The {group} sensors could not be read. Those fields are unavailable until they recover.",
                    exception.Message));
            }

            return unreadable;
        }
    }

    public void Dispose()
    {
        _cpuSensor?.Dispose();
        _gpuSensor?.Dispose();
    }
}
