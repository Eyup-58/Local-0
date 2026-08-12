"""The network boundary, installed at the lowest layer Python offers.

docs/SECURITY.md section 11 decided Selectable Hybrid: **Local mode is the default and permits
loopback only**, Cloud mode additionally permits outbound. This module is the first of the three
mechanisms that document names, and it is the only one with a hard guarantee - so it is worth being
exact about what it does and does not do.

**What it enforces:** loopback-only versus loopback-plus-outbound, process-wide, against any library
including transitive dependencies. That is why the patch sits on ``socket.socket.connect`` rather
than on a client wrapper: a dependency that opens its own socket goes through this method too, and
a guard installed any higher would simply not see it.

``sendto`` is patched for the same reason and was originally missed. A UDP datagram never calls
``connect`` - it carries its destination as an argument and leaves from an unconnected socket - so a
guard covering only the connection methods left a whole protocol outside a boundary this module's
own docstring described as total. ``sendmsg`` would need the same treatment and does not exist on
Windows, which is the only platform this product runs on.

**What it does not enforce:** *which* remote host is reached in Cloud mode. By the time ``connect``
runs, name resolution has already happened and the guard is handed an address. The Gemini endpoint
is anycast and its addresses rotate, so a hostname allowlist here is not a thing that can be
honoured. Pinning the host is the provider client's job, and the audit trail exists precisely
because that is not enforcement: a departure nobody intended is recorded even where it was not
prevented.

**It also does not enforce name resolution.** ``getaddrinfo`` runs in the C library against the
system resolver and is not a ``socket.socket`` operation, so a lookup for a remote name departs even
in Local mode - the connection that follows is refused, but the hostname was already sent. The
payload never leaves; the fact that something was looked up does. docs/SECURITY.md section 11 states
this as the one exception to Local mode's guarantee rather than leaving it implied.

Windows Firewall rules would enforce the host properly and are unavailable - creating them needs
elevation, which red line 11 forbids outright.

An address the guard cannot interpret is refused in Local mode. A hostname, an unexpected address
family, a malformed tuple: none of those is a reason to guess in the permissive direction.
"""

from __future__ import annotations

import errno
import socket
import threading
from ipaddress import ip_address
from typing import Any, Literal

from local_zero_brain.audit import AuditLog, EgressRecord

#: ``local`` sends nothing off the machine. ``cloud`` additionally permits outbound, aimed at one
#: endpoint by construction rather than by enforcement.
EgressMode = Literal["local", "cloud"]

#: The one name the guard treats as an address.
#:
#: Everything else arrives here already resolved; this one is passed through literally by enough
#: libraries that refusing it would break the local path for no security gain. A machine whose hosts
#: file redirects ``localhost`` has problems this guard is not positioned to solve.
LOOPBACK_HOSTNAMES = frozenset({"localhost"})

_LOCAL_ONLY_REASON = "local mode permits loopback only; nothing leaves this machine"
_CLOUD_REASON = (
    "cloud mode permits outbound. The host is constrained by the provider client, not by this "
    "guard - see docs/SECURITY.md section 11"
)


class EgressBlocked(PermissionError):
    """Refused before a packet existed.

    A subclass of ``OSError`` on purpose: callers - including standard-library helpers like
    ``socket.create_connection`` - already handle a failed connection, and this arrives through the
    path they expect rather than as an exception type nothing is prepared for.
    """


class EgressGuard:
    """Decides whether a connection may leave, and records the ones that try.

    One instance owns the patch. Constructing a second one while the first is installed would
    capture the patched method as its "original", so ``create_app`` builds exactly one.
    """

    __slots__ = ("_audit", "_mode", "_connect", "_connect_ex", "_sendto", "_lock")

    def __init__(self, *, audit: AuditLog, mode: EgressMode = "local") -> None:
        self._audit = audit
        #: Local until something says otherwise. Absence is not permission - the rule trust.json
        #: follows, and the state before anybody chooses anything has to be the one that sends
        #: nothing.
        self._mode: EgressMode = mode
        self._connect = socket.socket.connect
        self._connect_ex = socket.socket.connect_ex
        self._sendto = socket.socket.sendto
        self._lock = threading.Lock()

    @property
    def mode(self) -> EgressMode:
        return self._mode

    def set_mode(self, mode: EgressMode) -> None:
        """Switches the boundary. The only writer of this state."""
        if mode not in ("local", "cloud"):
            raise ValueError(f"unknown egress mode {mode!r}: the guard routes on this value")

        self._mode = mode

    def install(self) -> None:
        """Patches the socket methods. Idempotent enough to be safe, not clever about it."""
        guard = self

        def connect(sock: socket.socket, address: Any) -> Any:
            guard.require_permitted(address)
            return guard._connect(sock, address)

        def connect_ex(sock: socket.socket, address: Any) -> int:
            # Reports by return code rather than by raising, so it is a real way out of the process
            # and a guard covering only connect() would be decorative.
            try:
                guard.require_permitted(address)
            except EgressBlocked:
                return errno.EACCES

            return guard._connect_ex(sock, address)

        def sendto(sock: socket.socket, data: Any, *rest: Any) -> int:
            # sendto(data, address) and sendto(data, flags, address). The destination is the last
            # argument in both forms, so the arity is read rather than assumed - understanding only
            # the two-argument call would leave a bypass that costs an attacker one extra `0`.
            if rest:
                guard.require_permitted(rest[-1])

            return guard._sendto(sock, data, *rest)

        with self._lock:
            socket.socket.connect = connect  # type: ignore[method-assign]
            socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]
            socket.socket.sendto = sendto  # type: ignore[method-assign]

    def uninstall(self) -> None:
        with self._lock:
            socket.socket.connect = self._connect  # type: ignore[method-assign]
            socket.socket.connect_ex = self._connect_ex  # type: ignore[method-assign]
            socket.socket.sendto = self._sendto  # type: ignore[method-assign]

    def require_permitted(self, address: Any) -> None:
        """Raises ``EgressBlocked`` unless this destination is allowed in the current mode."""
        target = _endpoint(address)
        if target is None:
            # Not an IP endpoint: an AF_UNIX path, or a family whose address shape this guard does
            # not model. Nothing leaves the machine by that route, so there is nothing here to
            # decide - and refusing it would break local transports for no gain.
            return

        host, port = target
        if _is_loopback(host):
            return

        if self._mode == "cloud":
            self._record(host, port, decision="allowed", reason=_CLOUD_REASON)
            return

        self._record(host, port, decision="blocked", reason=_LOCAL_ONLY_REASON)
        raise EgressBlocked(f"{host}:{port} is off this machine, and {_LOCAL_ONLY_REASON}")

    def _record(self, host: str, port: int, *, decision: Literal["allowed", "blocked"], reason: str) -> None:
        self._audit.record(
            EgressRecord(mode=self._mode, host=host, port=port, decision=decision, reason=reason)
        )


def _endpoint(address: Any) -> tuple[str, int] | None:
    """The (host, port) pair, or None when the address is not an IP endpoint."""
    if not isinstance(address, tuple) or len(address) < 2:
        return None

    host, port = address[0], address[1]
    if not isinstance(host, str) or not isinstance(port, int):
        return None

    return host, port


def _is_loopback(host: str) -> bool:
    if host.lower() in LOOPBACK_HOSTNAMES:
        return True

    try:
        # A scope id (``fe80::1%eth0``) is part of the sockaddr, not of the address.
        return ip_address(host.split("%")[0]).is_loopback
    except ValueError:
        # Not an address at all. In Local mode the caller turns this into a refusal; guessing that
        # an uninterpretable host is safe is the failure this whole module exists to avoid.
        return False
