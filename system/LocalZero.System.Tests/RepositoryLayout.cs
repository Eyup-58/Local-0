namespace LocalZero.System.Tests;

/// <summary>
/// Locates the repository from the test assembly's output directory.
///
/// Tests read the schemas from <c>contracts/</c> in place rather than from a copy. A copied schema
/// is a second source of truth, and the first time it goes stale the suite passes against a
/// contract that no longer exists.
/// </summary>
internal static class RepositoryLayout
{
    private const string SchemaMarker = "ipc.schema.json";

    internal static string Root { get; } = FindRoot();

    internal static string ContractsDirectory => Path.Combine(Root, "contracts");

    internal static string SystemDirectory => Path.Combine(Root, "system");

    internal static string IpcSchemaPath => Path.Combine(ContractsDirectory, SchemaMarker);

    private static string FindRoot()
    {
        DirectoryInfo? directory = new(AppContext.BaseDirectory);

        while (directory is not null)
        {
            if (File.Exists(Path.Combine(directory.FullName, "contracts", SchemaMarker)))
            {
                return directory.FullName;
            }

            directory = directory.Parent;
        }

        throw new InvalidOperationException(
            $"Could not find the repository root above {AppContext.BaseDirectory}: "
            + $"no ancestor contains contracts/{SchemaMarker}.");
    }
}
