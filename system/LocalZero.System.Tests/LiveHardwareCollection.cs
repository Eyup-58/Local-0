using Xunit;

namespace LocalZero.System.Tests;

/// <summary>
/// Test classes that read this machine's real sensors, run one at a time.
///
/// Not a workaround for a flaky test - a correction to an unrealistic one. The sidecar opens one
/// set of PDH queries for the life of the process. Several test classes opening their own and
/// sweeping them concurrently is a load pattern the product never produces, and it made PDH return
/// incomplete instance sets often enough to obscure whether a failure meant anything.
///
/// The sensor itself still refuses to report an incomplete set, so the correctness rule holds
/// regardless of how the tests are scheduled. This only stops the suite manufacturing the
/// condition.
/// </summary>
[CollectionDefinition(Name, DisableParallelization = true)]
public sealed class LiveHardwareCollection
{
    public const string Name = "live hardware";
}
