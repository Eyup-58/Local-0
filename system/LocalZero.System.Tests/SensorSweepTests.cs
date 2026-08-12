using LocalZero.System.Sensors;
using LocalZero.System.Telemetry;
using Xunit;

namespace LocalZero.System.Tests;

/// <summary>
/// Sweep behaviour: groups fail independently, and a failure is reported once per transition.
/// docs/ARCHITECTURE.md section 5.
/// </summary>
public sealed class SensorSweepTests
{
    /// <summary>
    /// A sensor group that could not be opened must not take the working ones with it. Partial
    /// telemetry that says what is missing beats no telemetry.
    /// </summary>
    [Fact]
    public void a_missing_sensor_group_does_not_suppress_the_others()
    {
        using SensorSweep sweep = new(cpuSensor: null, gpuSensor: null);

        SweepResult result = sweep.Read();

        Assert.Null(result.Reading.Cpu.TotalPercent);
        Assert.Null(result.Reading.Gpu.UtilizationPercent);
        Assert.NotNull(result.Reading.Memory.TotalBytes);
        Assert.NotNull(result.Reading.UptimeSeconds);
    }

    /// <summary>
    /// A group that is simply absent is already declared unavailable in the hello. Reporting it as
    /// a fault every tick as well would be noise about something the consumer was told up front.
    /// </summary>
    [Fact]
    public void a_missing_sensor_group_is_not_reported_as_a_fault()
    {
        using SensorSweep sweep = new(cpuSensor: null, gpuSensor: null);

        SweepResult result = sweep.Read();

        Assert.Empty(result.NewFaults);
    }

    [Fact]
    public void a_sweep_without_a_graphics_adapter_declares_gpu_fields_unavailable()
    {
        using SensorSweep sweep = new(cpuSensor: null, gpuSensor: null);

        Assert.False(sweep.HasGraphicsAdapter);

        IReadOnlyList<SensorCapability> sensors = SensorCatalog.Build(sweep.HasGraphicsAdapter, sweep.HasGpuTemperature);
        SensorCapability utilization = sensors.Single(s => s.Field == "gpu.utilization_percent");

        Assert.False(utilization.Available);
        Assert.Equal(SensorSource.None, utilization.Source);
        Assert.False(string.IsNullOrWhiteSpace(utilization.UnavailableReason));
    }

    [Fact]
    public void the_sweep_reports_how_long_it_took()
    {
        using SensorSweep sweep = new(cpuSensor: null, gpuSensor: null);

        SweepResult result = sweep.Read();

        Assert.True(result.Duration >= TimeSpan.Zero, "Sweep duration should be measurable.");
    }
}

/// <summary>
/// The once-per-transition rule on its own, without needing a sensor that fails on command.
/// </summary>
public sealed class FaultTrackerTests
{
    [Fact]
    public void the_first_failure_of_a_group_is_reported()
    {
        FaultTracker tracker = new();

        Assert.True(tracker.ShouldReport("gpu"));
    }

    /// <summary>
    /// The point of the whole type: a sensor unreadable for an hour at 1 Hz produces one error,
    /// not 3600.
    /// </summary>
    [Fact]
    public void a_group_that_keeps_failing_is_reported_only_once()
    {
        FaultTracker tracker = new();

        _ = tracker.ShouldReport("gpu");
        int furtherReports = Enumerable.Range(0, 3600).Count(_ => tracker.ShouldReport("gpu"));

        Assert.Equal(0, furtherReports);
    }

    [Fact]
    public void a_group_that_recovers_and_fails_again_is_reported_again()
    {
        FaultTracker tracker = new();

        _ = tracker.ShouldReport("gpu");
        tracker.Clear("gpu");

        Assert.True(tracker.ShouldReport("gpu"));
    }

    [Fact]
    public void groups_are_tracked_independently()
    {
        FaultTracker tracker = new();

        _ = tracker.ShouldReport("cpu");

        Assert.True(tracker.ShouldReport("gpu"));
        Assert.True(tracker.IsFailing("cpu"));
    }

    [Fact]
    public void clearing_a_group_that_never_failed_is_harmless()
    {
        FaultTracker tracker = new();

        tracker.Clear("cpu");

        Assert.False(tracker.IsFailing("cpu"));
    }
}
