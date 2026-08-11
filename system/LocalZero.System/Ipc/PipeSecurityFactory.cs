using System.IO.Pipes;
using System.Security.AccessControl;
using System.Security.Principal;

namespace LocalZero.System.Ipc;

/// <summary>
/// Builds the ACL that is the whole reason this transport is a named pipe rather than a loopback
/// socket.
///
/// A pipe carries a Windows ACL the OS enforces. A TCP listener on 127.0.0.1 is reachable by every
/// process running as any user on the machine, and the only defence left is an application-level
/// token that has to be stored somewhere - and that somewhere becomes the new weakest link. For a
/// channel whose messages will eventually authorize OS actions, an OS-enforced ACL is the right
/// control. See docs/ARCHITECTURE.md section 4.
/// </summary>
internal static class PipeSecurityFactory
{
    /// <summary>
    /// A DACL containing exactly one entry: full control for the user this process is running as.
    /// Nothing is granted to Administrators, SYSTEM, or anyone else - a fresh
    /// <see cref="PipeSecurity"/> starts with no rules, and exactly one is added.
    /// </summary>
    internal static PipeSecurity ForCurrentUserOnly()
    {
        using WindowsIdentity identity = WindowsIdentity.GetCurrent();
        SecurityIdentifier owner = identity.User
            ?? throw new InvalidOperationException("The current Windows identity has no user SID.");

        PipeSecurity security = new();
        security.AddAccessRule(new PipeAccessRule(owner, PipeAccessRights.FullControl, AccessControlType.Allow));
        return security;
    }
}
