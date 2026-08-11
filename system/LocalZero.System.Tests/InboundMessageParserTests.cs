using LocalZero.System.Ipc;
using Xunit;

namespace LocalZero.System.Tests;

/// <summary>
/// Validation on the inbound half of the pipe boundary.
///
/// docs/CONTRACTS.md section 2: validate before reading any field, drop and count what fails, and
/// treat an unknown field as a rejection rather than something to ignore - that is what stops a
/// field being smuggled past one layer in the hope a later one reads it.
/// </summary>
public sealed class InboundMessageParserTests
{
    private const string ValidError =
        """{"v":1,"id":"3f2a9c1e-7b4d-4a68-9e01-5c8d2f6b1a30","ts":"2026-08-11T09:14:02.117Z","type":"error","payload":{"code":"internal_error","message":"something went wrong","in_reply_to":null}}""";

    [Fact]
    public void a_well_formed_error_is_accepted()
    {
        InboundResult result = InboundMessageParser.Parse(ValidError);

        Assert.Equal(InboundOutcome.Accepted, result.Outcome);
        Assert.Equal("error", result.Type);
    }

    [Fact]
    public void an_unknown_envelope_field_is_a_schema_violation()
    {
        const string smuggled =
            """{"v":1,"id":"3f2a9c1e-7b4d-4a68-9e01-5c8d2f6b1a30","ts":"2026-08-11T09:14:02.117Z","type":"error","payload":{},"exec":"calc.exe"}""";

        InboundResult result = InboundMessageParser.Parse(smuggled);

        Assert.Equal(InboundOutcome.SchemaViolation, result.Outcome);
    }

    [Fact]
    public void a_missing_envelope_field_is_a_schema_violation()
    {
        const string truncated =
            """{"v":1,"id":"3f2a9c1e-7b4d-4a68-9e01-5c8d2f6b1a30","type":"error","payload":{}}""";

        InboundResult result = InboundMessageParser.Parse(truncated);

        Assert.Equal(InboundOutcome.SchemaViolation, result.Outcome);
    }

    /// <summary>
    /// An unimplemented version fails closed. No best-effort parsing of a shape this build does
    /// not understand.
    /// </summary>
    [Fact]
    public void an_unimplemented_contract_version_is_refused_as_such()
    {
        const string futureVersion =
            """{"v":99,"id":"3f2a9c1e-7b4d-4a68-9e01-5c8d2f6b1a30","ts":"2026-08-11T09:14:02.117Z","type":"error","payload":{}}""";

        InboundResult result = InboundMessageParser.Parse(futureVersion);

        Assert.Equal(InboundOutcome.UnsupportedVersion, result.Outcome);
    }

    /// <summary>
    /// hello and telemetry.sample are declared system-to-brain. Receiving one back is a violation,
    /// not a message to process - the direction is part of the contract.
    /// </summary>
    [Theory]
    [InlineData("hello")]
    [InlineData("telemetry.sample")]
    [InlineData("capability.invoke")]
    public void a_message_type_the_brain_may_not_send_is_refused(string type)
    {
        // Built by concatenation rather than interpolation: the payload's closing "}}" collides
        // with raw-string interpolation delimiters.
        string message =
            "{\"v\":1,\"id\":\"3f2a9c1e-7b4d-4a68-9e01-5c8d2f6b1a30\",\"ts\":\"2026-08-11T09:14:02.117Z\","
            + "\"type\":\"" + type + "\",\"payload\":{}}";

        InboundResult result = InboundMessageParser.Parse(message);

        Assert.Equal(InboundOutcome.SchemaViolation, result.Outcome);
    }

    [Theory]
    [InlineData("not json at all")]
    [InlineData("[1,2,3]")]
    [InlineData("\"a bare string\"")]
    [InlineData("{")]
    public void anything_that_is_not_a_contract_object_is_a_schema_violation(string line)
    {
        InboundResult result = InboundMessageParser.Parse(line);

        Assert.Equal(InboundOutcome.SchemaViolation, result.Outcome);
    }

    [Fact]
    public void a_payload_that_is_not_an_object_is_a_schema_violation()
    {
        const string scalarPayload =
            """{"v":1,"id":"3f2a9c1e-7b4d-4a68-9e01-5c8d2f6b1a30","ts":"2026-08-11T09:14:02.117Z","type":"error","payload":"oops"}""";

        InboundResult result = InboundMessageParser.Parse(scalarPayload);

        Assert.Equal(InboundOutcome.SchemaViolation, result.Outcome);
    }

    /// <summary>
    /// A rejection has to say which field was wrong. Counting drops without recording why turns a
    /// contract mismatch into an unexplained silence.
    /// </summary>
    [Fact]
    public void a_rejection_names_what_was_wrong_with_the_message()
    {
        InboundResult result = InboundMessageParser.Parse("not json at all");

        Assert.NotEmpty(result.Detail);
    }
}
