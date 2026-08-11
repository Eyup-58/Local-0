using Xunit;

namespace LocalZero.System.Tests;

/// <summary>
/// Invariant L1, enforced as a test rather than as a habit.
///
/// The ROADMAP makes grep the evidence for "System.Diagnostics.PerformanceCounter appears nowhere
/// in system/". A grep somebody remembers to run is not evidence, so the grep lives here and runs
/// on every build.
///
/// Why this matters more than it looks: this machine's counter sets are named in Turkish. A
/// localized counter lookup half works - GPU telemetry appears, CPU telemetry silently returns
/// nothing - and reads as a bug in the CPU sampler for hours. See docs/ARCHITECTURE.md section 0.
/// </summary>
public sealed class BuildInvariantTests
{
    /// <summary>
    /// The sidecar's own sources - the code that ships and reads the machine.
    ///
    /// The scan is scoped to this project rather than to all of system/ because the banned tokens
    /// necessarily appear as literals in this test file, which lives in the sibling test project.
    /// A gate cannot include its own definition of what it forbids.
    /// </summary>
    private static string ProductionSources => Path.Combine(RepositoryLayout.SystemDirectory, "LocalZero.System");

    [Theory]
    [InlineData("PerformanceCounter")]
    [InlineData("PdhAddCounterW")]
    [InlineData("İşlemci")]
    public void banned_counter_apis_appear_in_no_source_file(string bannedToken)
    {
        List<string> offenders = [];

        foreach (string file in EnumerateSourceFiles())
        {
            foreach ((int number, string text) in ReadCodeLines(file))
            {
                if (text.Contains(bannedToken, StringComparison.Ordinal))
                {
                    offenders.Add($"{Path.GetRelativePath(RepositoryLayout.Root, file)}:{number}: {text.Trim()}");
                }
            }
        }

        Assert.True(
            offenders.Count == 0,
            $"'{bannedToken}' is banned in system/ (invariant L1). Found in:{Environment.NewLine}"
            + string.Join(Environment.NewLine, offenders));
    }

    /// <summary>
    /// The positive half of the gate. Banning the localized call is worth nothing if the English
    /// one is not actually the thing being used - a refactor that dropped PDH entirely would pass
    /// every ban above while quietly breaking CPU telemetry.
    /// </summary>
    [Fact]
    public void counters_are_resolved_through_the_english_api()
    {
        bool isUsed = EnumerateSourceFiles()
            .SelectMany(ReadCodeLines)
            .Any(line => line.Text.Contains("PdhAddEnglishCounterW", StringComparison.Ordinal));

        Assert.True(isUsed, "No source file calls PdhAddEnglishCounterW. Invariant L1 requires it.");
    }

    private static IEnumerable<string> EnumerateSourceFiles() =>
        Directory.EnumerateFiles(ProductionSources, "*.cs", SearchOption.AllDirectories)
            .Where(static path => !IsBuildOutput(path));

    private static bool IsBuildOutput(string path)
    {
        string relative = Path.GetRelativePath(ProductionSources, path);
        return relative.Split(Path.DirectorySeparatorChar)
            .Any(static segment =>
                segment.Equals("bin", StringComparison.OrdinalIgnoreCase)
                || segment.Equals("obj", StringComparison.OrdinalIgnoreCase));
    }

    /// <summary>
    /// Yields the lines of a file that are code, skipping comment lines.
    ///
    /// Line-based rather than a real comment stripper on purpose: stripping "//" out of the middle
    /// of a line would also cut it out of string literals, and a gate that removes code before
    /// scanning it can be defeated by accident. Skipping whole comment lines can only ever make
    /// the gate look at more code, never less.
    /// </summary>
    private static IEnumerable<(int Number, string Text)> ReadCodeLines(string path)
    {
        int number = 0;
        foreach (string line in File.ReadLines(path))
        {
            number++;
            string trimmed = line.TrimStart();

            bool isComment = trimmed.StartsWith("//", StringComparison.Ordinal)
                || trimmed.StartsWith("*", StringComparison.Ordinal)
                || trimmed.StartsWith("/*", StringComparison.Ordinal);

            if (!isComment)
            {
                yield return (number, line);
            }
        }
    }
}
