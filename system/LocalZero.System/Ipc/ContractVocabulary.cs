using LocalZero.System.Telemetry;

namespace LocalZero.System.Ipc;

/// <summary>
/// Every literal string the IPC contract defines, in one place.
///
/// These are deliberately not produced by a naming policy or an enum converter. The contract's
/// enums are a closed set that a receiver validates strictly, so the cost of a converter quietly
/// renaming a value - "win32_api" becoming "win32api" after a framework upgrade - is a message
/// dropped at the far end for reasons that are invisible here. Writing them out makes a contract
/// change a visible edit to this file.
/// </summary>
internal static class ContractVocabulary
{
    /// <summary>Contract major version. Incremented only on a breaking change - CONTRACTS.md section 5.</summary>
    internal const int Version = 1;

    internal const string ComponentSystem = "system";

    internal const string TypeHello = "hello";
    internal const string TypeTelemetrySample = "telemetry.sample";
    internal const string TypeError = "error";

    internal const string SourcePdhEnglish = "pdh_english";
    internal const string SourceWin32Api = "win32_api";
    internal const string SourceWmi = "wmi";
    internal const string SourceAdlx = "adlx";
    internal const string SourceNone = "none";

    internal const string ErrorSchemaViolation = "schema_violation";
    internal const string ErrorUnsupportedVersion = "unsupported_version";
    internal const string ErrorSensorReadFailed = "sensor_read_failed";
    internal const string ErrorHandshakeRequired = "handshake_required";
    internal const string ErrorInternalError = "internal_error";

    /// <summary>Maximum length of the error message field, from the schema.</summary>
    internal const int MaxErrorMessageLength = 500;

    internal static string ToWire(SensorSource source) => source switch
    {
        SensorSource.PdhEnglish => SourcePdhEnglish,
        SensorSource.Win32Api => SourceWin32Api,
        SensorSource.Wmi => SourceWmi,
        SensorSource.Adlx => SourceAdlx,
        SensorSource.None => SourceNone,
        _ => throw new ArgumentOutOfRangeException(nameof(source), source, "Unmapped sensor source."),
    };
}
