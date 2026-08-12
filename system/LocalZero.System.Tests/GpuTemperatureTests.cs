using LocalZero.System.Sensors;
using LocalZero.System.Telemetry;
using Xunit;

namespace LocalZero.System.Tests;

/// <summary>
/// GPU temperature through ADLX, which closed the U1 open question in M5.
///
/// The interop is vtable-index based and therefore has no type safety at all: a wrong slot returns
/// a double that looks exactly like a temperature. So what these tests hold is not "a number came
/// back" but that an absent or misbehaving ADLX produces a <b>labelled gap</b> rather than a
/// plausible substitute - CLAUDE.md invariant 10.
/// </summary>
public sealed class GpuTemperatureTests
{
    /// <summary>
    /// Skipped rather than failed off an AMD machine. This suite has to pass on a laptop with
    /// Intel graphics, and a test that demands a Radeon would make the repository unbuildable for
    /// anyone else - which for a project about to be open-sourced is the wrong trade.
    /// </summary>
    private static GpuTemperatureSensor? TryOpen() => GpuTemperatureSensor.TryCreate();

    [Fact]
    public void a_machine_without_adlx_declares_the_field_unavailable_with_a_reason()
    {
        IReadOnlyList<SensorCapability> sensors =
            SensorCatalog.Build(hasGraphicsAdapter: true, hasGpuTemperature: false);

        SensorCapability temperature = sensors.Single(s => s.Field == "gpu.temperature_c");

        Assert.False(temperature.Available);
        Assert.Equal(SensorSource.None, temperature.Source);
        Assert.False(string.IsNullOrWhiteSpace(temperature.UnavailableReason));
    }

    /// <summary>
    /// The two sources fail independently. A machine with another vendor's card still has working
    /// utilization and VRAM through PDH, and taking those down with the temperature would make one
    /// missing driver cost three fields.
    /// </summary>
    [Fact]
    public void an_absent_adlx_does_not_take_the_pdh_gpu_fields_with_it()
    {
        IReadOnlyList<SensorCapability> sensors =
            SensorCatalog.Build(hasGraphicsAdapter: true, hasGpuTemperature: false);

        Assert.True(sensors.Single(s => s.Field == "gpu.utilization_percent").Available);
        Assert.True(sensors.Single(s => s.Field == "gpu.vram_used_bytes").Available);
        Assert.False(sensors.Single(s => s.Field == "gpu.temperature_c").Available);
    }

    [Fact]
    public void the_field_is_declared_from_adlx_when_it_opened()
    {
        IReadOnlyList<SensorCapability> sensors =
            SensorCatalog.Build(hasGraphicsAdapter: true, hasGpuTemperature: true);

        SensorCapability temperature = sensors.Single(s => s.Field == "gpu.temperature_c");

        Assert.True(temperature.Available);
        Assert.Equal(SensorSource.Adlx, temperature.Source);
        Assert.Null(temperature.UnavailableReason);
    }

    /// <summary>
    /// A sweep with no temperature sensor leaves the field null while the rest of the GPU reading
    /// is untouched. Null means unavailable; it never means zero.
    /// </summary>
    [Fact]
    public void a_sweep_without_the_sensor_leaves_the_temperature_null()
    {
        using SensorSweep sweep = new(cpuSensor: null, gpuSensor: null, gpuTemperatureSensor: null);

        SweepResult result = sweep.Read();

        Assert.False(sweep.HasGpuTemperature);
        Assert.Null(result.Reading.Gpu.TemperatureC);
        // And the absence is not reported as a per-tick fault: it was declared in the hello.
        Assert.Empty(result.NewFaults);
    }

    /// <summary>
    /// The hardware case, on a machine that has ADLX. Asserts a plausible range rather than a
    /// value: the point is that what comes back is a temperature at all, which is what the
    /// vtable-index interop could silently get wrong.
    ///
    /// Measured during the M5 spike on a Radeon RX 7800 XT: 49 C at 8% load, 51 C at 23%, with
    /// hotspot reading above edge - and VRAM from the neighbouring slot matching PDH exactly, which
    /// is what proved the offsets.
    /// </summary>
    [Fact]
    public void a_real_read_returns_a_temperature_or_nothing_at_all()
    {
        using GpuTemperatureSensor? sensor = TryOpen();
        if (sensor is null)
        {
            // No AMD driver on this machine. The declared-unavailable path above is the contract
            // that matters here, and it is asserted without hardware.
            return;
        }

        double? celsius = sensor.Read();

        if (celsius is null)
        {
            // ADLX opened but the card does not report this metric. Legitimate, and a gap.
            return;
        }

        Assert.InRange(celsius.Value, 0, 125);
    }

    /// <summary>
    /// Reading twice must not fault, and must not hand back a frozen first sample. A metrics object
    /// held for the life of the process would report the temperature at startup forever.
    /// </summary>
    [Fact]
    public void reading_repeatedly_keeps_working()
    {
        using GpuTemperatureSensor? sensor = TryOpen();
        if (sensor is null)
        {
            return;
        }

        for (int i = 0; i < 5; i++)
        {
            double? celsius = sensor.Read();
            if (celsius is not null)
            {
                Assert.InRange(celsius.Value, 0, 125);
            }
        }
    }

    [Fact]
    public void reading_after_dispose_is_null_rather_than_a_crash()
    {
        GpuTemperatureSensor? sensor = TryOpen();
        if (sensor is null)
        {
            return;
        }

        sensor.Dispose();

        Assert.Null(sensor.Read());
    }
}
