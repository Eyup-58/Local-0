using System.Globalization;

namespace LocalZero.System.Diagnostics;

/// <summary>
/// Where detail goes. The contract keeps error messages on the wire short and user-safe, so the
/// status codes, counter paths and exception text that make a fault diagnosable have to land
/// somewhere else. This is that somewhere.
/// </summary>
internal interface ILog
{
    void Info(string message);

    void Warn(string message);
}

/// <summary>
/// Writes to standard error, timestamped. Standard error rather than standard output because
/// standard output is where a future mode may emit machine-readable data, and mixing the two
/// makes both unusable.
/// </summary>
internal sealed class ConsoleLog : ILog
{
    private const string TimestampFormat = "yyyy-MM-dd'T'HH:mm:ss.fff'Z'";

    private readonly TimeProvider _timeProvider;

    internal ConsoleLog(TimeProvider timeProvider)
    {
        ArgumentNullException.ThrowIfNull(timeProvider);
        _timeProvider = timeProvider;
    }

    public void Info(string message) => Write("info", message);

    public void Warn(string message) => Write("warn", message);

    private void Write(string level, string message)
    {
        string timestamp = _timeProvider.GetUtcNow().UtcDateTime.ToString(TimestampFormat, CultureInfo.InvariantCulture);
        Console.Error.WriteLine($"{timestamp} {level} {message}");
    }
}
