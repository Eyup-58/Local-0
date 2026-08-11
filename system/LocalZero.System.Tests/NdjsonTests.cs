using System.Text;
using LocalZero.System.Ipc;
using Xunit;

namespace LocalZero.System.Tests;

/// <summary>
/// NDJSON framing: one message per line, UTF-8, no BOM.
/// </summary>
public sealed class NdjsonTests
{
    private const string Line = """{"v":1,"type":"error"}""";

    [Fact]
    public async Task written_messages_carry_no_byte_order_mark()
    {
        using MemoryStream stream = new();

        await Ndjson.WriteLineAsync(stream, Line, CancellationToken.None);

        byte[] written = stream.ToArray();
        byte[] utf8Bom = [0xEF, 0xBB, 0xBF];

        Assert.False(
            written.Take(utf8Bom.Length).SequenceEqual(utf8Bom),
            "A BOM was written. The contract specifies UTF-8 without one, and a receiver splitting "
            + "on newlines would hand the BOM to its JSON parser as part of the first message.");
    }

    [Fact]
    public async Task written_messages_are_terminated_by_a_single_newline()
    {
        using MemoryStream stream = new();

        await Ndjson.WriteLineAsync(stream, Line, CancellationToken.None);

        string written = Encoding.UTF8.GetString(stream.ToArray());
        Assert.Equal(Line + "\n", written);
    }

    [Fact]
    public async Task each_line_is_read_back_as_one_message()
    {
        using MemoryStream stream = new(Encoding.UTF8.GetBytes("first\nsecond\nthird\n"));

        List<string> lines = await ReadAllAsync(stream);

        Assert.Equal(["first", "second", "third"], lines);
    }

    /// <summary>A sender that terminates with CRLF should not leave a stray carriage return behind.</summary>
    [Fact]
    public async Task carriage_returns_are_stripped_from_line_endings()
    {
        using MemoryStream stream = new(Encoding.UTF8.GetBytes("first\r\nsecond\r\n"));

        List<string> lines = await ReadAllAsync(stream);

        Assert.Equal(["first", "second"], lines);
    }

    [Fact]
    public async Task blank_lines_are_not_surfaced_as_messages()
    {
        using MemoryStream stream = new(Encoding.UTF8.GetBytes("first\n\n\nsecond\n"));

        List<string> lines = await ReadAllAsync(stream);

        Assert.Equal(["first", "second"], lines);
    }

    /// <summary>
    /// Without a length ceiling, a peer that never sends a newline makes the reader allocate until
    /// the process dies. The over-long line is dropped and counted, and the stream resynchronises
    /// on the next newline rather than the connection being torn down.
    /// </summary>
    [Fact]
    public async Task an_oversized_line_is_dropped_counted_and_recovered_from()
    {
        string oversized = new('x', Ndjson.MaxLineBytes + 1);
        using MemoryStream stream = new(Encoding.UTF8.GetBytes($"{oversized}\nrecovered\n"));

        int oversizedCount = 0;
        List<string> lines = [];
        await foreach (string line in Ndjson.ReadLinesAsync(stream, () => oversizedCount++, CancellationToken.None))
        {
            lines.Add(line);
        }

        Assert.Equal(1, oversizedCount);
        Assert.Equal(["recovered"], lines);
    }

    [Fact]
    public async Task a_written_message_survives_a_round_trip()
    {
        using MemoryStream stream = new();
        await Ndjson.WriteLineAsync(stream, Line, CancellationToken.None);
        stream.Position = 0;

        List<string> lines = await ReadAllAsync(stream);

        Assert.Equal([Line], lines);
    }

    private static async Task<List<string>> ReadAllAsync(Stream stream)
    {
        List<string> lines = [];
        await foreach (string line in Ndjson.ReadLinesAsync(stream, static () => { }, CancellationToken.None))
        {
            lines.Add(line);
        }

        return lines;
    }
}
