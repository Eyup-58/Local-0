namespace LocalZero.System.Interop;

/// <summary>
/// A PDH call returned a status this code does not know how to continue from.
///
/// Transient emptiness is not an exception: PDH needs two collections before a rate counter has a
/// value, and a counter whose instances have gone away reports no data. Those map to a null
/// telemetry field for that tick. This type is for the rest - a path that does not resolve, a
/// query that will not open - where continuing would mean inventing a number.
/// </summary>
internal sealed class PdhException : Exception
{
    internal PdhException(string operation, string counterPath, uint status)
        : base($"{operation} failed for '{counterPath}' with PDH status 0x{status:X8}{Hint(status)}")
    {
        Operation = operation;
        CounterPath = counterPath;
        Status = status;
    }

    internal string Operation { get; }

    internal string CounterPath { get; }

    internal uint Status { get; }

    private static string Hint(uint status) => status switch
    {
        PdhNative.StatusNoObject =>
            " (PDH_CSTATUS_NO_OBJECT - the counter set does not exist under this name. If this is a "
            + "CPU counter, check that it was added with PdhAddEnglishCounterW: this machine's "
            + "counter sets are named in Turkish. See invariant L1.)",
        PdhNative.StatusNoCounter =>
            " (PDH_CSTATUS_NO_COUNTER - the counter set exists but has no counter by this name.)",
        _ => string.Empty,
    };
}
