using System.Globalization;
using System.IO.Pipes;
using System.Threading.Channels;
using LocalZero.System.Diagnostics;
using LocalZero.System.Sensors;
using LocalZero.System.Telemetry;

namespace LocalZero.System.Ipc;

/// <summary>
/// Serves telemetry over a named pipe: accept a connection, send hello, then stream one sample per
/// tick until the peer goes away, and go back to waiting.
///
/// Failure behaviour is a milestone requirement, not polish (docs/ARCHITECTURE.md section 5). When
/// the brain dies, the write fails and this server returns to waiting for a connection. It does
/// not exit, does not spin, and does not buffer samples unboundedly - the outbound queue is
/// bounded and drops the oldest entry, which shows up at the consumer as a gap in <c>seq</c>.
/// </summary>
internal sealed class TelemetryPipeServer
{
    /// <summary>Full path is \\.\pipe\LocalZero.System.v1. The suffix is the contract major version.</summary>
    internal const string PipeName = "LocalZero.System.v1";

    private const int MaxServerInstances = 1;
    private const int PipeBufferBytes = 64 * 1024;

    /// <summary>
    /// Outbound queue depth. Deep enough to absorb a brief stall at 1 Hz, shallow enough that a
    /// stalled consumer is shown recent data with a visible gap rather than a growing backlog of
    /// stale samples presented as live.
    /// </summary>
    private const int OutboundCapacity = 32;

    private readonly SensorSweep _sweep;
    private readonly MessageFactory _messages;
    private readonly TimeProvider _timeProvider;
    private readonly TimeSpan _pollInterval;
    private readonly ILog _log;

    internal TelemetryPipeServer(
        SensorSweep sweep,
        MessageFactory messages,
        TimeProvider timeProvider,
        TimeSpan pollInterval,
        ILog log,
        DropCounters counters)
    {
        ArgumentNullException.ThrowIfNull(sweep);
        ArgumentNullException.ThrowIfNull(messages);
        ArgumentNullException.ThrowIfNull(timeProvider);
        ArgumentNullException.ThrowIfNull(log);
        ArgumentNullException.ThrowIfNull(counters);

        _sweep = sweep;
        _messages = messages;
        _timeProvider = timeProvider;
        _pollInterval = pollInterval;
        _log = log;
        Counters = counters;
    }

    internal DropCounters Counters { get; }

    /// <summary>
    /// Creates the listening pipe with a DACL granting only the current user. Exposed, with an
    /// overridable name, so tests can assert the ACL against a real pipe rather than against an
    /// inspection of this code - and can do so without colliding with a running sidecar.
    /// </summary>
    internal static NamedPipeServerStream CreateStream(string? pipeName = null) =>
        NamedPipeServerStreamAcl.Create(
            pipeName ?? PipeName,
            PipeDirection.InOut,
            MaxServerInstances,
            PipeTransmissionMode.Byte,
            PipeOptions.Asynchronous,
            PipeBufferBytes,
            PipeBufferBytes,
            PipeSecurityFactory.ForCurrentUserOnly());

    internal async Task RunAsync(CancellationToken cancellationToken)
    {
        while (!cancellationToken.IsCancellationRequested)
        {
            using NamedPipeServerStream stream = CreateStream();

            _log.Info($"waiting for the brain on \\\\.\\pipe\\{PipeName}");
            await stream.WaitForConnectionAsync(cancellationToken).ConfigureAwait(false);
            _log.Info("brain connected");

            await ServeConnectionAsync(stream, cancellationToken).ConfigureAwait(false);
            _log.Info("brain disconnected, waiting for a new connection");
        }
    }

    /// <summary>
    /// Runs one connection to completion. The producer, the writer and the inbound reader run
    /// concurrently; whichever finishes first ends the connection, and the others are cancelled.
    /// </summary>
    private async Task ServeConnectionAsync(NamedPipeServerStream stream, CancellationToken cancellationToken)
    {
        Channel<string> outbound = Channel.CreateBounded<string>(new BoundedChannelOptions(OutboundCapacity)
        {
            FullMode = BoundedChannelFullMode.DropOldest,
            SingleReader = true,
            SingleWriter = true,
        });

        using CancellationTokenSource connectionScope =
            CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);

        Task producer = ProduceAsync(outbound.Writer, connectionScope.Token);
        Task writer = WriteOutboundAsync(stream, outbound.Reader, connectionScope.Token);
        Task reader = ReadInboundAsync(stream, connectionScope.Token);

        try
        {
            await Task.WhenAny(producer, writer, reader).ConfigureAwait(false);
        }
        finally
        {
            await connectionScope.CancelAsync().ConfigureAwait(false);
            await WaitQuietlyAsync(producer, writer, reader).ConfigureAwait(false);
        }
    }

    /// <summary>Sends hello, then one telemetry sample per tick plus any new sensor fault.</summary>
    private async Task ProduceAsync(ChannelWriter<string> outbound, CancellationToken cancellationToken)
    {
        try
        {
            IReadOnlyList<SensorCapability> sensors = SensorCatalog.Build(_sweep.HasGraphicsAdapter, _sweep.HasGpuTemperature);
            int pollIntervalMs = (int)_pollInterval.TotalMilliseconds;
            Enqueue(outbound, IpcJson.Serialize(_messages.CreateHello(sensors, pollIntervalMs)));

            // seq restarts at 0 on every connection, per the contract.
            long seq = 0;
            using PeriodicTimer timer = new(_pollInterval, _timeProvider);

            while (await timer.WaitForNextTickAsync(cancellationToken).ConfigureAwait(false))
            {
                DateTimeOffset sampledAt = _timeProvider.GetUtcNow();
                SweepResult result = _sweep.Read();

                if (BenchMode.IsEnabled)
                {
                    _log.Info(
                        $"{BenchMode.SweepPrefix}{result.Duration.TotalMilliseconds.ToString("F3", CultureInfo.InvariantCulture)}");
                }

                Enqueue(outbound, IpcJson.Serialize(_messages.CreateSample(seq, sampledAt, result.Reading)));
                seq++;

                foreach (SensorFault fault in result.NewFaults)
                {
                    _log.Warn($"sensor group '{fault.SensorGroup}' failed: {fault.Detail}");
                    Enqueue(outbound, IpcJson.Serialize(_messages.CreateError(
                        ContractVocabulary.ErrorSensorReadFailed, fault.UserMessage, inReplyTo: null)));
                }
            }
        }
        catch (OperationCanceledException)
        {
            // Connection is closing. Nothing to report.
        }
        finally
        {
            outbound.TryComplete();
        }
    }

    /// <summary>
    /// Offers a line to the bounded queue. A refused write means the queue dropped the oldest entry
    /// instead, which the consumer sees as a seq gap - the contract's stated way of saying samples
    /// were lost.
    /// </summary>
    private static void Enqueue(ChannelWriter<string> outbound, string line) => outbound.TryWrite(line);

    private async Task WriteOutboundAsync(
        NamedPipeServerStream stream,
        ChannelReader<string> outbound,
        CancellationToken cancellationToken)
    {
        try
        {
            await foreach (string line in outbound.ReadAllAsync(cancellationToken).ConfigureAwait(false))
            {
                await Ndjson.WriteLineAsync(stream, line, cancellationToken).ConfigureAwait(false);
            }
        }
        catch (OperationCanceledException)
        {
            // Connection is closing.
        }
        catch (IOException exception)
        {
            // The brain went away mid-write. Expected, and not fatal to this process.
            _log.Warn($"pipe write failed, ending the connection: {exception.Message}");
        }
    }

    /// <summary>
    /// Validates everything the brain sends before acting on it. Invalid messages are dropped and
    /// counted; the connection survives, because one bad message is not a reason to tear it down.
    /// </summary>
    private async Task ReadInboundAsync(NamedPipeServerStream stream, CancellationToken cancellationToken)
    {
        try
        {
            await foreach (string line in Ndjson
                .ReadLinesAsync(stream, Counters.RecordOversizedLine, cancellationToken)
                .ConfigureAwait(false))
            {
                HandleInbound(line);
            }
        }
        catch (OperationCanceledException)
        {
            // Connection is closing.
        }
        catch (IOException exception)
        {
            _log.Warn($"pipe read failed, ending the connection: {exception.Message}");
        }
    }

    private void HandleInbound(string line)
    {
        InboundResult result = InboundMessageParser.Parse(line);
        switch (result.Outcome)
        {
            case InboundOutcome.Accepted:
                _log.Info($"brain reported an error, id {result.Id}");
                break;

            case InboundOutcome.UnsupportedVersion:
                Counters.RecordUnsupportedVersion();
                _log.Warn($"dropped an inbound message: {result.Detail}");
                break;

            case InboundOutcome.SchemaViolation:
            default:
                Counters.RecordSchemaViolation();
                _log.Warn($"dropped an inbound message: {result.Detail}");
                break;
        }
    }

    private static async Task WaitQuietlyAsync(params Task[] tasks)
    {
        try
        {
            await Task.WhenAll(tasks).ConfigureAwait(false);
        }
        catch (Exception exception) when (exception is OperationCanceledException or IOException)
        {
            // Both are the ordinary shape of a connection ending.
        }
    }
}
