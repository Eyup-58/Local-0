namespace LocalZero.System.Interop;

/// <summary>
/// The one ADLX session this process gets.
///
/// <para><b>ADLX is a process-global singleton, and it does not survive being torn down and brought
/// back.</b> That is not a guess: the first version of this code initialized and terminated per
/// sensor, and the second sensor built afterwards faulted with an access violation (0xC0000005).
/// Found by a test that opened a sensor, disposed it, and opened another - which is what a test
/// suite does naturally and what a long-lived process eventually does by accident.</para>
///
/// <para>So the session is opened at most once, shared, and <b>never terminated while the process
/// runs</b>. Termination is registered for process exit, where there is nothing left to fault.
/// The sidecar builds one sweep for its lifetime, so in production this is opened once and used
/// until shutdown either way - the singleton exists to make the accident impossible rather than
/// unlikely.</para>
/// </summary>
internal static class AdlxSession
{
    private static readonly Lock Gate = new();

    private static IntPtr _system;
    private static bool _attempted;

    /// <summary>
    /// The <c>IADLXSystem</c> pointer, or <see cref="IntPtr.Zero"/> when ADLX is not usable here.
    ///
    /// A failed attempt is remembered. Retrying on every sample would mean a machine with no AMD
    /// driver paying for a failing DLL load once a second, forever.
    /// </summary>
    internal static IntPtr System
    {
        get
        {
            lock (Gate)
            {
                if (_attempted)
                {
                    return _system;
                }

                _attempted = true;
                _system = Open();

                if (_system != IntPtr.Zero)
                {
                    // Not in a Dispose. Terminating while the process still runs is the thing that
                    // faulted, and there is no moment during a run when it would be correct.
                    AppDomain.CurrentDomain.ProcessExit += (_, _) => AdlxNative.ADLXTerminate();
                }

                return _system;
            }
        }
    }

    private static IntPtr Open()
    {
        try
        {
            if (AdlxNative.ADLXQueryFullVersion(out ulong version) != AdlxNative.Ok)
            {
                return IntPtr.Zero;
            }

            return AdlxNative.ADLXInitialize(version, out IntPtr system) == AdlxNative.Ok
                ? system
                : IntPtr.Zero;
        }
        catch (Exception exception) when (exception is not OutOfMemoryException)
        {
            // DllNotFoundException on any machine without an AMD display driver, which is the
            // ordinary case rather than an error.
            return IntPtr.Zero;
        }
    }
}
