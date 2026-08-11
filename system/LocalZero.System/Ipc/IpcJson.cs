using System.Text.Json;
using System.Text.Json.Serialization;

namespace LocalZero.System.Ipc;

/// <summary>
/// Source-generated serialization context for the IPC wire types.
///
/// Configured deliberately:
/// <list type="bullet">
/// <item><b>snake_case names</b> to match the contract.</item>
/// <item><b>Nulls are written, not skipped.</b> The schema lists every field as required,
/// including the nullable ones, so omitting a null would produce an invalid message. Null means
/// unavailable and has to travel.</item>
/// <item><b>No indentation.</b> The transport is newline-delimited JSON; a pretty-printed message
/// would span lines and break framing.</item>
/// </list>
/// </summary>
[JsonSourceGenerationOptions(
    PropertyNamingPolicy = JsonKnownNamingPolicy.SnakeCaseLower,
    DefaultIgnoreCondition = JsonIgnoreCondition.Never,
    WriteIndented = false)]
[JsonSerializable(typeof(HelloMessage))]
[JsonSerializable(typeof(TelemetrySampleMessage))]
[JsonSerializable(typeof(ErrorMessage))]
internal sealed partial class IpcJsonContext : JsonSerializerContext;

/// <summary>Serializes IPC messages to a single NDJSON line each.</summary>
internal static class IpcJson
{
    internal static string Serialize(HelloMessage message) =>
        JsonSerializer.Serialize(message, IpcJsonContext.Default.HelloMessage);

    internal static string Serialize(TelemetrySampleMessage message) =>
        JsonSerializer.Serialize(message, IpcJsonContext.Default.TelemetrySampleMessage);

    internal static string Serialize(ErrorMessage message) =>
        JsonSerializer.Serialize(message, IpcJsonContext.Default.ErrorMessage);
}
