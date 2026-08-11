using System.Globalization;
using LocalZero.System.Interop;
using LocalZero.System.Telemetry;

namespace LocalZero.System.Sensors;

/// <summary>
/// CPU load and frequency, read through PDH's English counter API.
///
/// This sensor is where invariant L1 bites. The counter set on this machine is named
/// <c>İşlemci Bilgileri</c>; resolving these same paths through the localized API fails with
/// PDH_CSTATUS_NO_OBJECT while the GPU sets still succeed, so a localized implementation looks
/// like a broken CPU sampler rather than a locale problem. See <see cref="PdhNative"/>.
/// </summary>
internal sealed class CpuSensor : IDisposable
{
    private const string TotalPercentPath = @"\Processor Information(_Total)\% Processor Time";
    private const string PerCorePercentPath = @"\Processor Information(*)\% Processor Time";
    private const string FrequencyPath = @"\Processor Information(_Total)\Processor Frequency";

    /// <summary>
    /// PDH aggregates roll-up instances into the same wildcard result as the real cores. Any
    /// instance name containing this marker is a total, not a core.
    /// </summary>
    private const string TotalInstanceMarker = "_Total";

    private const double MinPercent = 0;
    private const double MaxPercent = 100;

    private readonly PdhQuery _query;
    private readonly PdhCounter _totalPercent;
    private readonly PdhCounter _perCorePercent;
    private readonly PdhCounter _frequency;

    private CpuSensor(PdhQuery query, PdhCounter totalPercent, PdhCounter perCorePercent, PdhCounter frequency)
    {
        _query = query;
        _totalPercent = totalPercent;
        _perCorePercent = perCorePercent;
        _frequency = frequency;
    }

    internal static CpuSensor Create()
    {
        PdhQuery query = PdhQuery.Open();
        try
        {
            PdhCounter totalPercent = query.AddEnglishCounter(TotalPercentPath);
            PdhCounter perCorePercent = query.AddEnglishCounter(PerCorePercentPath);
            PdhCounter frequency = query.AddEnglishCounter(FrequencyPath);
            return new CpuSensor(query, totalPercent, perCorePercent, frequency);
        }
        catch
        {
            query.Dispose();
            throw;
        }
    }

    internal CpuReading Read()
    {
        _query.Collect();

        return new CpuReading(
            TotalPercent: Clamp(_totalPercent.ReadDouble()),
            PerCorePercent: ReadPerCore(),
            FrequencyMhz: _frequency.ReadDouble(),
            // Requires a kernel driver. Not installed, and not going to be. Always null.
            TemperatureC: null);
    }

    /// <summary>
    /// Per-core load, ordered by processor group and then by core number, or null when a complete
    /// set of cores is not available on this tick.
    ///
    /// Three details the counter forces:
    ///
    /// <list type="number">
    /// <item>The wildcard result includes the roll-up instances (<c>0,_Total</c> and
    /// <c>_Total</c>) alongside the real cores.</item>
    /// <item>PDH returns instances in lexicographic order, which puts core 10 before core 2.</item>
    /// <item><b>Individual cores routinely report no data.</b> Measured on this machine
    /// 2026-08-11: PDH always returns all 30 instances, but 3 to 6 of them come back with a
    /// non-success CStatus on roughly half of all samples, and the affected cores are almost
    /// always numbered 16 and above. Those are the E-cores of this i7-14700KF, and Windows parks
    /// them when idle. A parked core has no utilization to report.</item>
    /// </list>
    ///
    /// The third is why the contract's per_core_percent entries are nullable - amended 2026-08-11,
    /// see CONTRACTS.md section 5.
    ///
    /// A parked core keeps its slot and carries null. Position <i>is</i> the core's identity, so
    /// the array is never compacted: dropping an entry shifts every core after it and draws core
    /// 7's load on core 5's bar. A number in the wrong place is worse than no number, because the
    /// labelled gap is visible and the misaligned bar is not. See CLAUDE.md invariant 10.
    ///
    /// The whole array is null only when the instance set itself is incomplete, which would make
    /// the mapping from position to core unknowable.
    /// </summary>
    private IReadOnlyList<double?>? ReadPerCore()
    {
        IReadOnlyList<PdhInstance> instances = _perCorePercent.ReadInstances();
        if (instances.Count == 0)
        {
            return null;
        }

        List<(int Group, int Core, double? Percent)> cores = [];
        HashSet<(int Group, int Core)> seen = [];

        foreach (PdhInstance instance in instances)
        {
            if (instance.Name.Contains(TotalInstanceMarker, StringComparison.Ordinal))
            {
                continue;
            }

            if (!TryParseCoreInstance(instance.Name, out int group, out int core))
            {
                continue;
            }

            // A repeated instance would let the count come out right while the contents are wrong.
            if (!seen.Add((group, core)))
            {
                return null;
            }

            cores.Add((group, core, Clamp(instance.Value)));
        }

        // Every logical processor must be represented, present or parked. Anything else means the
        // position-to-core mapping is guesswork.
        if (cores.Count != Environment.ProcessorCount)
        {
            return null;
        }

        cores.Sort(static (left, right) =>
        {
            int byGroup = left.Group.CompareTo(right.Group);
            return byGroup != 0 ? byGroup : left.Core.CompareTo(right.Core);
        });

        return cores.ConvertAll(static core => core.Percent);
    }

    /// <summary>
    /// Parses a Processor Information instance name. It is <c>group,core</c> on group-aware
    /// systems (this machine reports <c>0,0</c> through <c>0,27</c>) and a bare core number on
    /// systems that do not report groups.
    /// </summary>
    private static bool TryParseCoreInstance(string name, out int group, out int core)
    {
        group = 0;
        core = 0;

        int separator = name.IndexOf(',', StringComparison.Ordinal);
        if (separator < 0)
        {
            return int.TryParse(name, NumberStyles.Integer, CultureInfo.InvariantCulture, out core);
        }

        return int.TryParse(name.AsSpan(0, separator), NumberStyles.Integer, CultureInfo.InvariantCulture, out group)
            && int.TryParse(name.AsSpan(separator + 1), NumberStyles.Integer, CultureInfo.InvariantCulture, out core);
    }

    /// <summary>
    /// Keeps a percentage inside the range the contract allows. PDH occasionally returns a
    /// fraction over 100 for a rate counter; letting that onto the wire would make the whole
    /// message schema-invalid and get the sample dropped, which is a worse answer than a value
    /// rounded back to its own ceiling.
    /// </summary>
    private static double? Clamp(double? percent) =>
        percent is null ? null : Math.Clamp(percent.Value, MinPercent, MaxPercent);

    public void Dispose() => _query.Dispose();
}
