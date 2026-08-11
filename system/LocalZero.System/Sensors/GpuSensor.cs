using LocalZero.System.Interop;
using LocalZero.System.Telemetry;

namespace LocalZero.System.Sensors;

/// <summary>
/// GPU utilization and VRAM for one adapter, read driverless through PDH.
///
/// Both counter sets report every adapter on the machine, and this one has two - the Radeon and
/// the Microsoft Basic Render Driver. Instances are therefore filtered by the selected adapter's
/// LUID token so that utilization, VRAM used and VRAM total all describe the same device rather
/// than a sum across unrelated hardware.
///
/// Measured unelevated on this machine (finding M2, docs/ARCHITECTURE.md): GPU Engine returned
/// live instances and GPU Adapter Memory returned a dedicated usage figure, both without
/// privilege. Temperature is not here - that needs ADLX, which is an M5 spike.
/// </summary>
internal sealed class GpuSensor : IDisposable
{
    private const string UtilizationPath = @"\GPU Engine(*)\Utilization Percentage";
    private const string DedicatedUsagePath = @"\GPU Adapter Memory(*)\Dedicated Usage";

    private const double MinPercent = 0;
    private const double MaxPercent = 100;

    private readonly PdhQuery _query;
    private readonly PdhCounter _utilization;
    private readonly PdhCounter _dedicatedUsage;

    private GpuSensor(GraphicsAdapter adapter, PdhQuery query, PdhCounter utilization, PdhCounter dedicatedUsage)
    {
        Adapter = adapter;
        _query = query;
        _utilization = utilization;
        _dedicatedUsage = dedicatedUsage;
    }

    /// <summary>The adapter every reading from this sensor describes.</summary>
    internal GraphicsAdapter Adapter { get; }

    /// <summary>
    /// Opens the GPU counters for the primary hardware adapter. Returns null when DXGI reports no
    /// hardware adapter, which makes every GPU field a labelled gap instead of a guess.
    /// </summary>
    internal static GpuSensor? TryCreate()
    {
        GraphicsAdapter? adapter = GraphicsAdapter.SelectPrimary();
        if (adapter is null)
        {
            return null;
        }

        PdhQuery query = PdhQuery.Open();
        try
        {
            PdhCounter utilization = query.AddEnglishCounter(UtilizationPath);
            PdhCounter dedicatedUsage = query.AddEnglishCounter(DedicatedUsagePath);
            return new GpuSensor(adapter, query, utilization, dedicatedUsage);
        }
        catch
        {
            query.Dispose();
            throw;
        }
    }

    internal GpuReading Read()
    {
        _query.Collect();

        return new GpuReading(
            UtilizationPercent: ReadUtilization(),
            VramUsedBytes: ReadDedicatedUsage(),
            VramTotalBytes: ToInt64(Adapter.DedicatedVideoMemoryBytes),
            // Requires ADLX, which is an unvalidated M5 spike. Null until that passes.
            TemperatureC: null);
    }

    /// <summary>
    /// Sum of this adapter's engine instances, clamped to 100 - the definition
    /// contracts/ipc.schema.json states for the field.
    ///
    /// Worth knowing when reading the number: instances are per process and per engine
    /// (3D, Copy, Compute, VideoDecode...), this machine reported 258 of them, and engines run
    /// concurrently. A sum is therefore not the same quantity Task Manager's "GPU %" shows, which
    /// is a maximum across engines. The contract asks for the sum, so the sum is what is sent.
    /// </summary>
    private double? ReadUtilization()
    {
        IReadOnlyList<PdhInstance> instances = _utilization.ReadInstances();
        if (instances.Count == 0)
        {
            return null;
        }

        double total = 0;
        bool matched = false;
        foreach (PdhInstance instance in instances)
        {
            // A silent instance contributes nothing rather than being counted as zero load. Engine
            // instances come and go with the processes that own them.
            if (instance.Value is not { } value || !BelongsToAdapter(instance.Name))
            {
                continue;
            }

            matched = true;
            total += value;
        }

        // No instance for this adapter is not the same as zero load: it means the counter told us
        // nothing about this device on this tick.
        return matched ? Math.Clamp(total, MinPercent, MaxPercent) : null;
    }

    private long? ReadDedicatedUsage()
    {
        IReadOnlyList<PdhInstance> instances = _dedicatedUsage.ReadInstances(asLargeInteger: true);
        if (instances.Count == 0)
        {
            return null;
        }

        double total = 0;
        bool matched = false;
        foreach (PdhInstance instance in instances)
        {
            if (instance.Value is not { } value || !BelongsToAdapter(instance.Name))
            {
                continue;
            }

            matched = true;
            total += value;
        }

        return matched ? (long)Math.Clamp(total, 0, long.MaxValue) : null;
    }

    /// <summary>
    /// True when a PDH GPU instance name carries the selected adapter's LUID. Both counter sets
    /// embed it: <c>luid_..._phys_0</c> for adapter memory, and
    /// <c>pid_1234_luid_..._phys_0_eng_2_engtype_Compute</c> for engines.
    /// </summary>
    private bool BelongsToAdapter(string instanceName) =>
        instanceName.Contains(Adapter.PdhLuidToken, StringComparison.Ordinal);

    private static long ToInt64(ulong value) => value > long.MaxValue ? long.MaxValue : (long)value;

    public void Dispose() => _query.Dispose();
}
