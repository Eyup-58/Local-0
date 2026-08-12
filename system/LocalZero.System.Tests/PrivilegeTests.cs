using LocalZero.System.Diagnostics;
using LocalZero.System.Ipc;
using LocalZero.System.Sensors;
using LocalZero.System.Telemetry;
using Xunit;

namespace LocalZero.System.Tests;

/// <summary>
/// The privilege model: every process runs asInvoker, no elevated helper, no UAC prompt anywhere.
///
/// Elevation would buy nothing here. Everything the sidecar reads is available unelevated, and the
/// one thing elevation might have bought - CPU temperature - needs a ring-0 driver rather than
/// privilege. See docs/ARCHITECTURE.md section 2 and CLAUDE.md invariant 11.
/// </summary>
public sealed class PrivilegeTests
{
    /// <summary>
    /// Schema-level: hello.payload.elevated is declared const false. A sidecar claiming elevation
    /// is a contract violation the brain refuses.
    /// </summary>
    [Fact]
    public void hello_always_declares_the_sidecar_unelevated()
    {
        MessageFactory factory = new(new FixedTimeProvider(FixedTimeProvider.DefaultInstant));
        IReadOnlyList<SensorCapability> sensors = SensorCatalog.Build(hasGraphicsAdapter: true, hasGpuTemperature: true);

        HelloMessage hello = factory.CreateHello(sensors, pollIntervalMs: 1000);

        Assert.False(hello.Payload.Elevated);
    }

    /// <summary>
    /// Runtime-level, and the reason the guard exists: the manifest requests asInvoker, but a user
    /// can still launch the sidecar from an elevated shell. If that happened it would keep
    /// declaring elevated:false - a message that validates while being untrue. The guard refuses
    /// to start instead.
    ///
    /// This test failing means the suite itself is running elevated, which is worth knowing: it
    /// would let an ACL or privilege assertion pass for the wrong reason.
    /// </summary>
    [Fact]
    public void the_test_process_is_not_elevated()
    {
        Assert.False(
            ElevationGuard.IsElevated(),
            "These tests are running elevated. Local Zero runs asInvoker everywhere, and privilege "
            + "assertions made from an elevated process do not prove what they appear to.");
    }
}
