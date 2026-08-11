using System.Text.Json;
using System.Text.RegularExpressions;
using LocalZero.System.Ipc;
using LocalZero.System.Sensors;
using LocalZero.System.Telemetry;
using Xunit;

namespace LocalZero.System.Tests;

/// <summary>
/// Envelope stamping and the field-level rules the schema's patterns enforce.
///
/// JSON Schema treats "format" as an annotation by default, so these patterns are checked here
/// explicitly rather than being assumed to fall out of schema validation.
/// </summary>
public sealed partial class MessageFactoryTests
{
    [GeneratedRegex(@"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")]
    private static partial Regex Rfc3339Milliseconds { get; }

    [GeneratedRegex("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")]
    private static partial Regex Uuid { get; }

    private static MessageFactory CreateFactory() =>
        new(new FixedTimeProvider(FixedTimeProvider.DefaultInstant));

    private static HelloMessage CreateHello() =>
        CreateFactory().CreateHello(SensorCatalog.Build(hasGraphicsAdapter: true), pollIntervalMs: 1000);

    [Fact]
    public void timestamps_are_rfc3339_utc_with_millisecond_precision()
    {
        HelloMessage hello = CreateHello();

        Assert.Matches(Rfc3339Milliseconds, hello.Ts);
        Assert.Equal("2026-08-11T09:14:02.117Z", hello.Ts);
    }

    [Fact]
    public void every_message_carries_a_lowercase_uuid()
    {
        HelloMessage hello = CreateHello();

        Assert.Matches(Uuid, hello.Id);
    }

    [Fact]
    public void message_ids_are_unique_per_message()
    {
        MessageFactory factory = CreateFactory();
        IReadOnlyList<SensorCapability> sensors = SensorCatalog.Build(hasGraphicsAdapter: true);

        string first = factory.CreateHello(sensors, 1000).Id;
        string second = factory.CreateHello(sensors, 1000).Id;

        Assert.NotEqual(first, second);
    }

    /// <summary>
    /// sampled_at is when the machine was read; the envelope ts is when the message was built.
    /// Conflating them makes latency unmeasurable, so the factory must keep them separate.
    /// </summary>
    [Fact]
    public void the_sample_time_is_independent_of_the_envelope_time()
    {
        MessageFactory factory = CreateFactory();
        DateTimeOffset sampledAt = FixedTimeProvider.DefaultInstant.AddSeconds(-5);
        TelemetryReading reading = new(
            new CpuReading(null, null, null, null),
            new MemoryReading(null, null, null, null),
            new GpuReading(null, null, null, null),
            null);

        TelemetrySampleMessage sample = factory.CreateSample(seq: 0, sampledAt, reading);

        Assert.Equal("2026-08-11T09:13:57.117Z", sample.Payload.SampledAt);
        Assert.Equal("2026-08-11T09:14:02.117Z", sample.Ts);
    }

    [Fact]
    public void the_app_version_matches_the_pattern_the_schema_requires()
    {
        HelloMessage hello = CreateHello();

        Assert.Matches(@"^\d+\.\d+\.\d+$", hello.Payload.AppVersion);
    }

    /// <summary>
    /// An over-long error would fail validation at the far end, which would tell the brain nothing
    /// about the fault that produced it. Truncating keeps the report deliverable.
    /// </summary>
    [Fact]
    public void an_over_long_error_message_is_truncated_to_the_contract_limit()
    {
        MessageFactory factory = CreateFactory();

        ErrorMessage error = factory.CreateError(
            ContractVocabulary.ErrorInternalError,
            new string('x', ContractVocabulary.MaxErrorMessageLength + 250),
            inReplyTo: null);

        Assert.Equal(ContractVocabulary.MaxErrorMessageLength, error.Payload.Message.Length);
    }

    [Fact]
    public void every_message_declares_the_current_contract_version()
    {
        HelloMessage hello = CreateHello();

        Assert.Equal(1, hello.V);
        Assert.Equal("system", hello.Payload.Component);
    }

    /// <summary>
    /// The schema sets additionalProperties false at every level, so an extra property is a
    /// rejected message rather than an ignored field. Serialization is checked against the exact
    /// key set the envelope allows.
    /// </summary>
    [Fact]
    public void the_serialized_envelope_carries_exactly_the_contract_fields()
    {
        string json = IpcJson.Serialize(CreateHello());

        using JsonDocument document = JsonDocument.Parse(json);
        string[] properties = [.. document.RootElement.EnumerateObject().Select(p => p.Name)];

        Assert.Equal(["v", "id", "ts", "type", "payload"], properties);
    }

    /// <summary>
    /// Null means unavailable and has to travel. If the serializer were ever configured to skip
    /// nulls, the field would go missing and the message would fail validation - the schema lists
    /// every field as required, nullable ones included.
    /// </summary>
    [Fact]
    public void unavailable_fields_are_serialized_as_null_rather_than_omitted()
    {
        MessageFactory factory = CreateFactory();
        TelemetryReading reading = new(
            new CpuReading(null, null, null, null),
            new MemoryReading(null, null, null, null),
            new GpuReading(null, null, null, null),
            null);

        string json = IpcJson.Serialize(factory.CreateSample(seq: 0, FixedTimeProvider.DefaultInstant, reading));

        using JsonDocument document = JsonDocument.Parse(json);
        JsonElement cpu = document.RootElement.GetProperty("payload").GetProperty("cpu");

        Assert.Equal(JsonValueKind.Null, cpu.GetProperty("temperature_c").ValueKind);
        Assert.Equal(JsonValueKind.Null, cpu.GetProperty("total_percent").ValueKind);
    }

    /// <summary>
    /// The contract's enum values are a closed set the far end validates strictly. A naming policy
    /// quietly renaming one would produce messages dropped for invisible reasons.
    /// </summary>
    [Fact]
    public void sensor_sources_serialize_to_their_contract_spelling()
    {
        Assert.Equal("pdh_english", ContractVocabulary.ToWire(SensorSource.PdhEnglish));
        Assert.Equal("win32_api", ContractVocabulary.ToWire(SensorSource.Win32Api));
        Assert.Equal("wmi", ContractVocabulary.ToWire(SensorSource.Wmi));
        Assert.Equal("adlx", ContractVocabulary.ToWire(SensorSource.Adlx));
        Assert.Equal("none", ContractVocabulary.ToWire(SensorSource.None));
    }

    /// <summary>
    /// Every value of the enum has a mapping. A value added without one would throw at
    /// serialization time, on a machine that happens to have that sensor.
    /// </summary>
    [Fact]
    public void every_sensor_source_has_a_contract_spelling()
    {
        foreach (SensorSource source in Enum.GetValues<SensorSource>())
        {
            Assert.False(string.IsNullOrEmpty(ContractVocabulary.ToWire(source)));
        }
    }

    /// <summary>
    /// The schema enforces the pairing, but building a message that cannot be sent is a bug worth
    /// catching here rather than at the far end.
    /// </summary>
    [Fact]
    public void every_unavailable_sensor_declares_a_reason_and_no_source()
    {
        HelloMessage hello = CreateHello();

        IEnumerable<SensorDeclaration> unavailable = hello.Payload.Sensors.Where(s => !s.Available);

        Assert.NotEmpty(unavailable);
        Assert.All(unavailable, sensor =>
        {
            Assert.False(string.IsNullOrWhiteSpace(sensor.UnavailableReason));
            Assert.Equal(ContractVocabulary.SourceNone, sensor.Source);
        });
    }

    [Fact]
    public void every_available_sensor_declares_no_reason()
    {
        HelloMessage hello = CreateHello();

        Assert.All(
            hello.Payload.Sensors.Where(s => s.Available),
            sensor => Assert.Null(sensor.UnavailableReason));
    }

    /// <summary>
    /// The declaration is what the UI builds its labelled gaps from, so a field the sample can
    /// carry but the hello never mentions would show up as an unexplained null.
    /// </summary>
    [Fact]
    public void the_declaration_covers_every_field_the_sample_can_carry()
    {
        string[] expected =
        [
            "cpu.total_percent", "cpu.per_core_percent", "cpu.frequency_mhz", "cpu.temperature_c",
            "memory.used_bytes", "memory.total_bytes", "memory.commit_used_bytes", "memory.commit_limit_bytes",
            "gpu.utilization_percent", "gpu.vram_used_bytes", "gpu.vram_total_bytes", "gpu.temperature_c",
            "uptime_seconds",
        ];

        HelloMessage hello = CreateHello();
        string[] declared = [.. hello.Payload.Sensors.Select(s => s.Field)];

        Assert.Equal(expected.Order(), declared.Order());
        Assert.Equal(declared.Length, declared.Distinct().Count());
    }
}
