using LocalZero.System.Sensors;
using LocalZero.System.Telemetry;
using Xunit;

namespace LocalZero.System.Tests;

/// <summary>
/// The sensors, read against this machine rather than against a mock.
///
/// A mocked PDH would have passed happily on the localized-counter bug that motivates invariant
/// L1, because the bug is in what the OS returns, not in the shape of the code. These tests are
/// the ones that would have caught it.
/// </summary>
[Collection(LiveHardwareCollection.Name)]
public sealed class SensorTests
{
    /// <summary>
    /// PDH rate counters are computed from the difference between two collections, so a single
    /// read has nothing to report. Everything here reads twice with a gap.
    /// </summary>
    private static readonly TimeSpan CollectionGap = TimeSpan.FromSeconds(1);

    /// <summary>
    /// The GPU tests require a hardware adapter rather than skipping without one. M1's exit
    /// criteria include live GPU telemetry in the UI, so a machine that cannot produce it has not
    /// met the milestone - a silently skipped test would hide that.
    /// </summary>
    private const string NoAdapterMessage =
        "DXGI reported no hardware graphics adapter. M1 requires live GPU telemetry, so this is a "
        + "genuine failure on this machine rather than a test to skip.";

    [Fact]
    public void cpu_total_load_reads_a_real_percentage()
    {
        using CpuSensor sensor = CpuSensor.Create();

        _ = sensor.Read();
        Thread.Sleep(CollectionGap);
        CpuReading reading = sensor.Read();

        Assert.NotNull(reading.TotalPercent);
        Assert.InRange(reading.TotalPercent!.Value, 0, 100);
    }

    /// <summary>
    /// The invariant that keeps the UI honest: position in this array *is* the core's identity, so
    /// a short array silently draws one core's load on another core's bar. Either every logical
    /// processor is present or the field is null.
    ///
    /// This is not hypothetical. Before the sensor enforced it, concurrent PDH use produced 26
    /// entries on this 28-thread machine, roughly two runs in three.
    /// </summary>
    [Fact]
    public void per_core_load_is_all_or_nothing()
    {
        using CpuSensor sensor = CpuSensor.Create();

        _ = sensor.Read();
        Thread.Sleep(CollectionGap);
        CpuReading reading = sensor.Read();

        if (reading.PerCorePercent is null)
        {
            return;
        }

        Assert.Equal(Environment.ProcessorCount, reading.PerCorePercent.Count);

        // A null entry is a parked core keeping its slot, which is the point of the array being
        // nullable. Values that are present still have to be percentages.
        Assert.All(
            reading.PerCorePercent,
            percent =>
            {
                if (percent is { } value)
                {
                    Assert.InRange(value, 0, 100);
                }
            });
    }

    /// <summary>
    /// Parked cores keep their slots.
    ///
    /// Windows parks the E-cores of this i7-14700KF when they are idle, and a parked core reports
    /// no utilization. Because the contract's entries are nullable, that no longer costs the whole
    /// array - the core holds its position and carries null, so index 20 is still core 20.
    ///
    /// The assertion is deliberately weak about *whether* any core is parked, since that depends
    /// on what the machine happens to be doing. What it pins down is that the array keeps its full
    /// length regardless, which is the property the UI's bar alignment rests on.
    /// </summary>
    [Fact]
    public void parked_cores_keep_their_position_in_the_array()
    {
        const int ticks = 4;
        using CpuSensor sensor = CpuSensor.Create();

        _ = sensor.Read();

        for (int tick = 0; tick < ticks; tick++)
        {
            Thread.Sleep(CollectionGap);
            CpuReading reading = sensor.Read();

            Assert.NotNull(reading.TotalPercent);
            Assert.NotNull(reading.PerCorePercent);
            Assert.Equal(Environment.ProcessorCount, reading.PerCorePercent!.Count);
        }
    }

    [Fact]
    public void cpu_frequency_reads_a_positive_value()
    {
        using CpuSensor sensor = CpuSensor.Create();

        _ = sensor.Read();
        Thread.Sleep(CollectionGap);
        CpuReading reading = sensor.Read();

        Assert.NotNull(reading.FrequencyMhz);
        Assert.True(reading.FrequencyMhz > 0, "CPU frequency should be positive.");
    }

    /// <summary>
    /// Not a limitation to be worked around later. There is no ring-0 driver and there will not be
    /// one, so this field has no source at any privilege level. If it ever becomes non-null, the
    /// driverless decision has been reversed without the documentation catching up.
    /// </summary>
    [Fact]
    public void cpu_temperature_is_never_reported()
    {
        using CpuSensor sensor = CpuSensor.Create();

        _ = sensor.Read();
        Thread.Sleep(CollectionGap);
        CpuReading reading = sensor.Read();

        Assert.Null(reading.TemperatureC);
    }

    [Fact]
    public void memory_reports_a_consistent_physical_picture()
    {
        MemoryReading reading = MemorySensor.Read();

        Assert.NotNull(reading.TotalBytes);
        Assert.NotNull(reading.UsedBytes);
        Assert.True(reading.TotalBytes > 0, "Total physical memory should be positive.");
        Assert.InRange(reading.UsedBytes!.Value, 0, reading.TotalBytes!.Value);
    }

    [Fact]
    public void memory_reports_a_consistent_commit_picture()
    {
        MemoryReading reading = MemorySensor.Read();

        Assert.NotNull(reading.CommitLimitBytes);
        Assert.NotNull(reading.CommitUsedBytes);
        Assert.True(reading.CommitLimitBytes > 0, "Commit limit should be positive.");
        Assert.InRange(reading.CommitUsedBytes!.Value, 0, reading.CommitLimitBytes!.Value);
    }

    [Fact]
    public void uptime_is_positive()
    {
        Assert.True(UptimeSensor.Read() > 0, "Uptime should be positive on a running machine.");
    }

    /// <summary>
    /// GPU counters were measured readable unelevated on this machine (finding M2). VRAM used and
    /// total must describe the same adapter, which is why the sensor filters PDH instances by the
    /// selected adapter's LUID instead of summing every instance the counter reports.
    /// </summary>
    [Fact]
    public void gpu_reports_vram_for_the_selected_adapter()
    {
        using GpuSensor? sensor = GpuSensor.TryCreate();
        Assert.True(sensor is not null, NoAdapterMessage);

        _ = sensor!.Read();
        Thread.Sleep(CollectionGap);
        GpuReading reading = sensor.Read();

        Assert.NotNull(reading.VramTotalBytes);
        Assert.True(reading.VramTotalBytes > 0, "Total VRAM should be positive.");

        if (reading.VramUsedBytes is not null)
        {
            Assert.InRange(reading.VramUsedBytes.Value, 0, reading.VramTotalBytes!.Value);
        }
    }

    [Fact]
    public void gpu_utilization_stays_within_the_contract_range()
    {
        using GpuSensor? sensor = GpuSensor.TryCreate();
        Assert.True(sensor is not null, NoAdapterMessage);

        _ = sensor!.Read();
        Thread.Sleep(CollectionGap);
        GpuReading reading = sensor.Read();

        if (reading.UtilizationPercent is not null)
        {
            Assert.InRange(reading.UtilizationPercent.Value, 0, 100);
        }
    }

    /// <summary>
    /// Null until the ADLX spike in M5 passes. Nothing may depend on it before then.
    /// </summary>
    [Fact]
    public void gpu_temperature_is_never_reported()
    {
        using GpuSensor? sensor = GpuSensor.TryCreate();
        Assert.True(sensor is not null, NoAdapterMessage);

        _ = sensor!.Read();
        GpuReading reading = sensor.Read();

        Assert.Null(reading.TemperatureC);
    }
}
