using System.IO.Pipes;
using System.Security.AccessControl;
using System.Security.Principal;
using LocalZero.System.Ipc;
using Xunit;

namespace LocalZero.System.Tests;

/// <summary>
/// The named pipe's ACL, asserted against a real pipe.
///
/// This is the control that justifies the whole transport choice. A pipe carries an OS-enforced
/// ACL; a loopback socket is reachable by every process on the machine and can only be defended
/// with an application-level secret that has to be stored somewhere. Since these messages will
/// eventually authorize OS actions, the ACL has to be verified rather than assumed - which is why
/// the ROADMAP requires this be proved "by a test, not by inspection".
/// </summary>
public sealed class PipeSecurityTests
{
    /// <summary>
    /// Each test uses its own pipe name so the suite never collides with a running sidecar - or
    /// with itself, since the server allows a single instance.
    /// </summary>
    private static string UniquePipeName() => $"LocalZero.Test.{Guid.NewGuid():N}";

    [Fact]
    public void the_pipe_grants_access_to_exactly_one_identity()
    {
        using NamedPipeServerStream stream = TelemetryPipeServer.CreateStream(UniquePipeName());

        AuthorizationRuleCollection rules = stream.GetAccessControl()
            .GetAccessRules(includeExplicit: true, includeInherited: true, typeof(SecurityIdentifier));

        Assert.Single(rules);
    }

    [Fact]
    public void the_only_identity_on_the_pipe_is_the_current_user()
    {
        using WindowsIdentity identity = WindowsIdentity.GetCurrent();
        SecurityIdentifier expected = identity.User!;

        using NamedPipeServerStream stream = TelemetryPipeServer.CreateStream(UniquePipeName());

        PipeAccessRule rule = stream.GetAccessControl()
            .GetAccessRules(includeExplicit: true, includeInherited: true, typeof(SecurityIdentifier))
            .Cast<PipeAccessRule>()
            .Single();

        Assert.Equal(expected, rule.IdentityReference);
        Assert.Equal(AccessControlType.Allow, rule.AccessControlType);
    }

    /// <summary>
    /// Named administrators and SYSTEM specifically, because those are the identities a default
    /// DACL would have handed access to if the explicit PipeSecurity were ever dropped.
    /// </summary>
    [Fact]
    public void no_other_well_known_identity_can_reach_the_pipe()
    {
        SecurityIdentifier administrators = new(WellKnownSidType.BuiltinAdministratorsSid, null);
        SecurityIdentifier localSystem = new(WellKnownSidType.LocalSystemSid, null);

        using NamedPipeServerStream stream = TelemetryPipeServer.CreateStream(UniquePipeName());

        IEnumerable<IdentityReference> identities = stream.GetAccessControl()
            .GetAccessRules(includeExplicit: true, includeInherited: true, typeof(SecurityIdentifier))
            .Cast<PipeAccessRule>()
            .Select(rule => rule.IdentityReference);

        Assert.DoesNotContain(administrators, identities);
        Assert.DoesNotContain(localSystem, identities);
    }
}
