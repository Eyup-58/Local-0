using System.Text.Json;
using Json.Schema;
using LocalZero.System.Ipc;
using LocalZero.System.Sensors;
using LocalZero.System.Telemetry;
using Xunit;

namespace LocalZero.System.Tests;

/// <summary>
/// Holds what this sidecar actually puts on the wire to contracts/ipc.schema.json.
///
/// The schema is the single source of truth (docs/CONTRACTS.md), so these tests serialize real
/// messages through the real serializer and evaluate them against the real schema file. Asserting
/// on hand-written expected JSON instead would only prove the test agrees with itself.
/// </summary>
[Collection(LiveHardwareCollection.Name)]
public sealed class ContractComplianceTests
{
    private static readonly JsonSchema IpcSchema = JsonSchema.FromFile(RepositoryLayout.IpcSchemaPath);

    private static readonly EvaluationOptions Options = new()
    {
        OutputFormat = OutputFormat.List,

        // format is annotation-only by default in JSON Schema 2020-12. The contract's timestamps
        // are only useful if the format is actually enforced, so it is turned on here.
        RequireFormatValidation = true,
    };

    [Fact]
    public void hello_message_validates_against_the_ipc_schema()
    {
        MessageFactory factory = new(new FixedTimeProvider(FixedTimeProvider.DefaultInstant));
        IReadOnlyList<SensorCapability> sensors = SensorCatalog.Build(hasGraphicsAdapter: true, hasGpuTemperature: true);

        string json = IpcJson.Serialize(factory.CreateHello(sensors, pollIntervalMs: 1000));

        AssertValid(json);
    }

    [Fact]
    public void hello_message_validates_when_no_graphics_adapter_is_present()
    {
        MessageFactory factory = new(new FixedTimeProvider(FixedTimeProvider.DefaultInstant));
        IReadOnlyList<SensorCapability> sensors = SensorCatalog.Build(hasGraphicsAdapter: false, hasGpuTemperature: false);

        string json = IpcJson.Serialize(factory.CreateHello(sensors, pollIntervalMs: 1000));

        AssertValid(json);
    }

    [Fact]
    public void telemetry_sample_validates_when_every_sensor_reports_a_value()
    {
        MessageFactory factory = new(new FixedTimeProvider(FixedTimeProvider.DefaultInstant));
        TelemetryReading reading = new(
            // The null entry is a parked core holding its slot, which is the case the contract was
            // widened for on 2026-08-11.
            new CpuReading(12.4, [18.2, 4.1, null, 22.7], 3420.0, null),
            new MemoryReading(19327352832, 68719476736, 24696061952, 79322677248),
            new GpuReading(8.2, 3249733632, 17179869184, null),
            184213);

        string json = IpcJson.Serialize(
            factory.CreateSample(seq: 1, FixedTimeProvider.DefaultInstant, reading));

        AssertValid(json);
    }

    /// <summary>
    /// The all-null sample is the one that matters most: it is what a machine with no readable
    /// sensors produces, and it has to stay a valid message rather than becoming an unsendable
    /// one. Null means unavailable and must travel.
    /// </summary>
    [Fact]
    public void telemetry_sample_validates_when_every_sensor_is_unavailable()
    {
        MessageFactory factory = new(new FixedTimeProvider(FixedTimeProvider.DefaultInstant));
        TelemetryReading reading = new(
            new CpuReading(null, null, null, null),
            new MemoryReading(null, null, null, null),
            new GpuReading(null, null, null, null),
            null);

        string json = IpcJson.Serialize(
            factory.CreateSample(seq: 0, FixedTimeProvider.DefaultInstant, reading));

        AssertValid(json);
    }

    [Fact]
    public void error_message_validates_against_the_ipc_schema()
    {
        MessageFactory factory = new(new FixedTimeProvider(FixedTimeProvider.DefaultInstant));

        string json = IpcJson.Serialize(factory.CreateError(
            ContractVocabulary.ErrorSensorReadFailed,
            "The gpu sensors could not be read.",
            inReplyTo: null));

        AssertValid(json);
    }

    /// <summary>
    /// A sample built from a live sweep, not from hand-picked values. This is what catches a real
    /// sensor producing something the contract forbids - a percentage over 100, say - which fixed
    /// test data never would.
    /// </summary>
    [Fact]
    public void a_sample_read_from_this_machine_validates_against_the_ipc_schema()
    {
        using SensorSweep sweep = SensorSweep.Create(_ => { });
        MessageFactory factory = new(TimeProvider.System);

        // Discard the first sweep: PDH rate counters have no value until a second collection.
        _ = sweep.Read();
        Thread.Sleep(TimeSpan.FromSeconds(1));
        SweepResult result = sweep.Read();

        string json = IpcJson.Serialize(
            factory.CreateSample(seq: 1, DateTimeOffset.UtcNow, result.Reading));

        AssertValid(json);
    }

    private static void AssertValid(string json)
    {
        using JsonDocument document = JsonDocument.Parse(json);

        EvaluationResults results = IpcSchema.Evaluate(document.RootElement, Options);

        Assert.True(results.IsValid, Describe(results, json));
    }

    private static string Describe(EvaluationResults results, string json)
    {
        string detail = JsonSerializer.Serialize(results, new JsonSerializerOptions { WriteIndented = true });

        return $"Message did not validate against ipc.schema.json.{Environment.NewLine}"
            + $"message: {json}{Environment.NewLine}"
            + $"evaluation: {detail}";
    }
}
