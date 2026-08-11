using System.Text.Json;

namespace LocalZero.System.Ipc;

/// <summary>Why an inbound line was accepted or refused.</summary>
internal enum InboundOutcome
{
    Accepted,
    SchemaViolation,
    UnsupportedVersion,
}

/// <param name="Detail">
/// Names the offending field, for the log. The contract requires a rejected message to be logged
/// with what was wrong with it, not just counted.
/// </param>
internal sealed record InboundResult(InboundOutcome Outcome, string? Type, string? Id, string Detail)
{
    internal static InboundResult Accepted(string type, string? id) =>
        new(InboundOutcome.Accepted, type, id, string.Empty);

    internal static InboundResult Violation(string detail) =>
        new(InboundOutcome.SchemaViolation, null, null, detail);

    internal static InboundResult BadVersion(string detail) =>
        new(InboundOutcome.UnsupportedVersion, null, null, detail);
}

/// <summary>
/// Validates an inbound line before any field of it is read for meaning.
///
/// Two rules from docs/CONTRACTS.md section 2 shape this:
/// <list type="bullet">
/// <item>Validate <b>before</b> reading any field. A message that fails validation has no readable
/// fields, including for logging - which is why the log detail here names the offending field
/// rather than echoing its value.</item>
/// <item>An unknown field is a rejected message, not an ignored one. That is what stops a field
/// being smuggled past one layer in the hope a later one reads it.</item>
/// </list>
///
/// The brain only ever sends <c>error</c> to the sidecar. <c>hello</c> and
/// <c>telemetry.sample</c> are declared system-to-brain, so receiving one back is a violation
/// rather than a message to process.
/// </summary>
internal static class InboundMessageParser
{
    private static readonly string[] EnvelopeFields = ["v", "id", "ts", "type", "payload"];

    internal static InboundResult Parse(string line)
    {
        ArgumentNullException.ThrowIfNull(line);

        JsonDocument document;
        try
        {
            document = JsonDocument.Parse(line);
        }
        catch (JsonException)
        {
            return InboundResult.Violation("body is not valid JSON");
        }

        using (document)
        {
            return Validate(document.RootElement);
        }
    }

    private static InboundResult Validate(JsonElement root)
    {
        if (root.ValueKind != JsonValueKind.Object)
        {
            return InboundResult.Violation("root is not an object");
        }

        InboundResult? envelopeFailure = ValidateEnvelopeShape(root);
        if (envelopeFailure is not null)
        {
            return envelopeFailure;
        }

        if (!root.GetProperty("v").TryGetInt32(out int version))
        {
            return InboundResult.Violation("v is not an integer");
        }

        if (version != ContractVocabulary.Version)
        {
            // Do not attempt best-effort parsing of a version this build does not implement.
            return InboundResult.BadVersion($"v is {version}, this build implements {ContractVocabulary.Version}");
        }

        JsonElement type = root.GetProperty("type");
        if (type.ValueKind != JsonValueKind.String)
        {
            return InboundResult.Violation("type is not a string");
        }

        string? typeName = type.GetString();
        if (!string.Equals(typeName, ContractVocabulary.TypeError, StringComparison.Ordinal))
        {
            return InboundResult.Violation("type is not one the brain may send to the system layer");
        }

        if (root.GetProperty("payload").ValueKind != JsonValueKind.Object)
        {
            return InboundResult.Violation("payload is not an object");
        }

        JsonElement id = root.GetProperty("id");
        return id.ValueKind == JsonValueKind.String
            ? InboundResult.Accepted(ContractVocabulary.TypeError, id.GetString())
            : InboundResult.Violation("id is not a string");
    }

    /// <summary>
    /// Requires exactly the five envelope fields - no fewer, and crucially no more. The schema
    /// sets additionalProperties false at every level.
    /// </summary>
    private static InboundResult? ValidateEnvelopeShape(JsonElement root)
    {
        int propertyCount = 0;
        foreach (JsonProperty property in root.EnumerateObject())
        {
            propertyCount++;
            if (Array.IndexOf(EnvelopeFields, property.Name) < 0)
            {
                return InboundResult.Violation("envelope carries an unknown field");
            }
        }

        if (propertyCount != EnvelopeFields.Length)
        {
            return InboundResult.Violation("envelope is missing a required field");
        }

        return null;
    }
}
