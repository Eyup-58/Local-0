using System.Globalization;
using System.Reflection;
using LocalZero.System.Telemetry;

namespace LocalZero.System.Ipc;

/// <summary>
/// Builds contract-shaped messages from domain readings, and is the only place an envelope is
/// stamped. Every message gets a fresh UUIDv4 and the time it was built.
/// </summary>
internal sealed class MessageFactory
{
    /// <summary>
    /// RFC 3339, UTC, millisecond precision, always ending in Z - exactly what the schema's
    /// date-time fields expect. Formatted explicitly rather than through a round-trip specifier so
    /// the precision cannot drift with the framework.
    /// </summary>
    private const string Rfc3339Milliseconds = "yyyy-MM-dd'T'HH:mm:ss.fff'Z'";

    private readonly string _appVersion;
    private readonly TimeProvider _timeProvider;

    internal MessageFactory(TimeProvider timeProvider, string? appVersion = null)
    {
        ArgumentNullException.ThrowIfNull(timeProvider);

        _timeProvider = timeProvider;
        _appVersion = appVersion ?? ReadAssemblyVersion();
    }

    /// <summary>
    /// The sidecar's version in the major.minor.patch form the schema's pattern requires. Taken
    /// from the assembly so it cannot drift from the csproj, and trimmed of the fourth component
    /// the CLR always appends.
    /// </summary>
    internal static string ReadAssemblyVersion()
    {
        Version? version = Assembly.GetExecutingAssembly().GetName().Version;
        return version is null
            ? "0.0.0"
            : string.Create(CultureInfo.InvariantCulture, $"{version.Major}.{version.Minor}.{version.Build}");
    }

    internal HelloMessage CreateHello(IReadOnlyList<SensorCapability> sensors, int pollIntervalMs)
    {
        ArgumentNullException.ThrowIfNull(sensors);

        List<SensorDeclaration> declarations = [];
        foreach (SensorCapability capability in sensors)
        {
            declarations.Add(new SensorDeclaration(
                capability.Field,
                capability.Available,
                ContractVocabulary.ToWire(capability.Source),
                capability.UnavailableReason));
        }

        return new HelloMessage(
            ContractVocabulary.Version,
            NewId(),
            Now(),
            ContractVocabulary.TypeHello,
            new HelloPayload(
                ContractVocabulary.ComponentSystem,
                _appVersion,
                // Const false in the schema. Local Zero runs every process asInvoker; see
                // docs/ARCHITECTURE.md section 2.
                Elevated: false,
                pollIntervalMs,
                declarations));
    }

    internal TelemetrySampleMessage CreateSample(long seq, DateTimeOffset sampledAt, TelemetryReading reading)
    {
        ArgumentNullException.ThrowIfNull(reading);

        return new TelemetrySampleMessage(
            ContractVocabulary.Version,
            NewId(),
            Now(),
            ContractVocabulary.TypeTelemetrySample,
            new TelemetryPayload(
                seq,
                Format(sampledAt),
                new CpuPayload(
                    reading.Cpu.TotalPercent,
                    reading.Cpu.PerCorePercent,
                    reading.Cpu.FrequencyMhz,
                    reading.Cpu.TemperatureC),
                new MemoryPayload(
                    reading.Memory.UsedBytes,
                    reading.Memory.TotalBytes,
                    reading.Memory.CommitUsedBytes,
                    reading.Memory.CommitLimitBytes),
                new GpuPayload(
                    reading.Gpu.UtilizationPercent,
                    reading.Gpu.VramUsedBytes,
                    reading.Gpu.VramTotalBytes,
                    reading.Gpu.TemperatureC),
                reading.UptimeSeconds));
    }

    /// <summary>
    /// Builds an error. The message is truncated to the length the schema allows rather than being
    /// sent over-long and rejected wholesale - an error that fails validation tells the far end
    /// nothing about the fault that caused it.
    /// </summary>
    internal ErrorMessage CreateError(string code, string message, string? inReplyTo)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(code);
        ArgumentException.ThrowIfNullOrWhiteSpace(message);

        string safeMessage = message.Length > ContractVocabulary.MaxErrorMessageLength
            ? message[..ContractVocabulary.MaxErrorMessageLength]
            : message;

        return new ErrorMessage(
            ContractVocabulary.Version,
            NewId(),
            Now(),
            ContractVocabulary.TypeError,
            new ErrorPayload(code, safeMessage, inReplyTo));
    }

    private string Now() => Format(_timeProvider.GetUtcNow());

    private static string Format(DateTimeOffset instant) =>
        instant.UtcDateTime.ToString(Rfc3339Milliseconds, CultureInfo.InvariantCulture);

    private static string NewId() => Guid.NewGuid().ToString("D", CultureInfo.InvariantCulture);
}
