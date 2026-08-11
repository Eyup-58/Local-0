using System.Security.Principal;

namespace LocalZero.System.Diagnostics;

/// <summary>
/// Enforces the privilege model at runtime instead of trusting the manifest alone.
///
/// The manifest requests asInvoker, but a user can still launch this process from an elevated
/// shell. If that happened the sidecar would keep sending <c>elevated: false</c>, which the schema
/// declares const - so the message would still validate while being untrue, and the brain would
/// accept a connection from a component with far more authority than the contract describes.
///
/// Refusing to start is the honest response. Nothing in Local Zero needs elevation, so there is no
/// case to accommodate. See docs/ARCHITECTURE.md section 2 and CLAUDE.md invariant 11.
/// </summary>
internal static class ElevationGuard
{
    internal static bool IsElevated()
    {
        using WindowsIdentity identity = WindowsIdentity.GetCurrent();
        WindowsPrincipal principal = new(identity);
        return principal.IsInRole(WindowsBuiltInRole.Administrator);
    }
}
