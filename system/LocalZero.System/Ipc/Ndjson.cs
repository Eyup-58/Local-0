using System.Buffers;
using System.Runtime.CompilerServices;
using System.Text;

namespace LocalZero.System.Ipc;

/// <summary>
/// Newline-delimited JSON framing: one message per line, UTF-8, no BOM.
///
/// A human-readable wire format is a deliberate choice - the contract is the load-bearing artifact
/// here, and a failing message can be read straight out of a log during debugging. At 1 Hz with a
/// payload this small, encoding cost is not a consideration. See docs/ARCHITECTURE.md section 4.
/// </summary>
internal static class Ndjson
{
    /// <summary>
    /// UTF-8 without a BOM, and strict on the way in. Invalid bytes throw rather than silently
    /// becoming replacement characters, because a message that decodes to something other than
    /// what was sent is exactly the kind of quiet corruption this contract exists to prevent.
    /// </summary>
    internal static readonly UTF8Encoding Utf8NoBom = new(encoderShouldEmitUTF8Identifier: false, throwOnInvalidBytes: true);

    /// <summary>
    /// The longest line that will be assembled from the wire. Without a ceiling, a peer that never
    /// sends a newline makes the reader allocate until the process dies - validate at the
    /// boundary, and a length limit is part of that.
    /// </summary>
    internal const int MaxLineBytes = 1024 * 1024;

    private const byte LineFeed = (byte)'\n';
    private const byte CarriageReturn = (byte)'\r';
    private const int ReadBufferBytes = 8 * 1024;

    /// <summary>Writes one message followed by the framing newline, and flushes it.</summary>
    internal static async Task WriteLineAsync(Stream stream, string json, CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(stream);
        ArgumentNullException.ThrowIfNull(json);

        int maxBytes = Utf8NoBom.GetMaxByteCount(json.Length) + 1;
        byte[] buffer = ArrayPool<byte>.Shared.Rent(maxBytes);
        try
        {
            int written = Utf8NoBom.GetBytes(json, buffer);
            buffer[written] = LineFeed;

            await stream.WriteAsync(buffer.AsMemory(0, written + 1), cancellationToken).ConfigureAwait(false);
            await stream.FlushAsync(cancellationToken).ConfigureAwait(false);
        }
        finally
        {
            ArrayPool<byte>.Shared.Return(buffer);
        }
    }

    /// <summary>
    /// Reads lines until the peer disconnects.
    ///
    /// A line longer than <see cref="MaxLineBytes"/> is discarded rather than thrown on: one
    /// malformed message does not tear down a connection, it is dropped and counted. The rest of
    /// that line is skipped so the next newline resynchronises the stream.
    /// </summary>
    /// <param name="onOversizedLine">Invoked once per discarded line, for the drop counter.</param>
    internal static async IAsyncEnumerable<string> ReadLinesAsync(
        Stream stream,
        Action onOversizedLine,
        [EnumeratorCancellation] CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(stream);
        ArgumentNullException.ThrowIfNull(onOversizedLine);

        byte[] readBuffer = ArrayPool<byte>.Shared.Rent(ReadBufferBytes);
        List<byte> pending = new(ReadBufferBytes);
        bool isSkippingOversizedLine = false;

        try
        {
            while (true)
            {
                int read = await stream.ReadAsync(readBuffer.AsMemory(0, ReadBufferBytes), cancellationToken)
                    .ConfigureAwait(false);
                if (read == 0)
                {
                    yield break;
                }

                for (int i = 0; i < read; i++)
                {
                    byte current = readBuffer[i];

                    if (current != LineFeed)
                    {
                        if (isSkippingOversizedLine)
                        {
                            continue;
                        }

                        if (pending.Count >= MaxLineBytes)
                        {
                            isSkippingOversizedLine = true;
                            pending.Clear();
                            onOversizedLine();
                            continue;
                        }

                        pending.Add(current);
                        continue;
                    }

                    if (isSkippingOversizedLine)
                    {
                        isSkippingOversizedLine = false;
                        continue;
                    }

                    string? line = DecodeLine(pending);
                    pending.Clear();
                    if (line is not null)
                    {
                        yield return line;
                    }
                }
            }
        }
        finally
        {
            ArrayPool<byte>.Shared.Return(readBuffer);
        }
    }

    /// <summary>
    /// Decodes one framed line, tolerating a CRLF sender. Returns null for a blank line and for
    /// bytes that are not valid UTF-8 - both are nothing to hand upward, and neither is worth
    /// closing a connection over.
    /// </summary>
    private static string? DecodeLine(List<byte> pending)
    {
        int length = pending.Count;
        if (length > 0 && pending[length - 1] == CarriageReturn)
        {
            length--;
        }

        if (length == 0)
        {
            return null;
        }

        byte[] bytes = new byte[length];
        pending.CopyTo(0, bytes, 0, length);

        try
        {
            return Utf8NoBom.GetString(bytes);
        }
        catch (DecoderFallbackException)
        {
            return null;
        }
    }
}
