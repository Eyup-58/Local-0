"""The network boundary: what may leave this machine, and what is merely visible.

docs/SECURITY.md section 11 draws a line these tests hold, and it is a line with two halves that
are not equally strong. Local mode is a hard guarantee - nothing non-loopback connects, whatever
attempts it - and Cloud mode is not: the host restriction lives in the provider client, and this
guard only makes the departure visible. The tests are written so that difference is legible rather
than implied.

Two properties carry the guarantee:

* the patch sits on ``socket.socket.connect``, ``connect_ex`` and ``sendto``, which is the floor
  every library reaches eventually - a guard installed one layer higher would be routed around by
  the first dependency that opens its own socket, and one covering only the connection methods is
  routed around by any datagram;
* an address the guard cannot interpret is refused in Local mode. A hostname, an unexpected address
  family, a malformed tuple: none of those is a reason to guess in the permissive direction.
"""

from __future__ import annotations

import errno
import socket
from collections.abc import Iterator
from pathlib import Path

import pytest

from local_zero_brain.audit import AuditLog
from local_zero_brain.net.egress import EgressBlocked, EgressGuard

#: Reserved for documentation (RFC 5737). Nothing here ever tries to reach it - every test that
#: names it expects to be stopped before a packet exists.
OFF_MACHINE = ("203.0.113.7", 443)

#: Every socket method the guard replaces. ``sendmsg`` is absent on Windows, which is why the list
#: is written down rather than derived.
GUARDED_METHODS = ("connect", "connect_ex", "sendto")


@pytest.fixture
def audit(tmp_path: Path) -> AuditLog:
    return AuditLog(tmp_path / "audit.jsonl")


@pytest.fixture
def guard(audit: AuditLog) -> Iterator[EgressGuard]:
    """Installs the guard and always removes it.

    A leaked patch would silently apply to every test that ran afterwards, and the failure would
    appear in an unrelated file.
    """
    installed = EgressGuard(audit=audit)
    installed.install()
    try:
        yield installed
    finally:
        installed.uninstall()


@pytest.fixture
def listener() -> Iterator[tuple[str, int]]:
    """A real loopback server, so the allowed path is proven by a connection rather than by a mock."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        yield server.getsockname()
    finally:
        server.close()


def test_local_mode_permits_loopback(guard: EgressGuard, listener: tuple[str, int]) -> None:
    """The default mode still has to let the product work: Ollama lives on 127.0.0.1."""
    with socket.socket() as client:
        client.connect(listener)

        assert client.getpeername() == listener


def test_local_mode_refuses_anything_off_the_machine(guard: EgressGuard) -> None:
    with socket.socket() as client, pytest.raises(EgressBlocked):
        client.connect(OFF_MACHINE)


def test_a_blocked_connection_is_refused_before_any_packet(guard: EgressGuard) -> None:
    """The refusal is synchronous and local.

    If the guard let the syscall run and merely recorded the result, "nothing leaves the machine"
    would be a description of intent rather than of behaviour.
    """
    with socket.socket() as client:
        client.settimeout(0.001)

        with pytest.raises(EgressBlocked):
            client.connect(OFF_MACHINE)


def test_the_guard_is_not_routed_around_by_a_higher_level_helper(guard: EgressGuard) -> None:
    """``create_connection`` is what most libraries actually call.

    It resolves the name itself and then opens its own socket, so a guard installed on a wrapper
    would never see this. Patching the socket method is what makes the boundary hold for code
    Local Zero did not write.
    """
    with pytest.raises(EgressBlocked):
        socket.create_connection(OFF_MACHINE, timeout=0.001)


def test_connect_ex_is_guarded_too(guard: EgressGuard) -> None:
    """It reports by return code rather than by raising, and it is a real way out of the process.

    A guard covering only ``connect`` would be one ``connect_ex`` call away from being decorative.
    """
    with socket.socket() as client:
        assert client.connect_ex(OFF_MACHINE) == errno.EACCES


def test_a_datagram_off_the_machine_is_refused(guard: EgressGuard) -> None:
    """UDP never calls connect, so a guard covering only connect leaves this route wide open.

    ``sendto`` carries its destination as an argument, which is the whole point: the socket is
    unconnected and the packet leaves anyway. Found by checking which methods ``install`` actually
    replaces rather than by reading the docstring that said "total".
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client, pytest.raises(EgressBlocked):
        client.sendto(b"leaving", OFF_MACHINE)


def test_a_datagram_sent_with_flags_is_refused_too(guard: EgressGuard) -> None:
    """``sendto`` has two arities and the address is the last argument in both.

    A guard that only understood the two-argument form would be bypassed by adding a ``0``.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client, pytest.raises(EgressBlocked):
        client.sendto(b"leaving", 0, OFF_MACHINE)


def test_a_refused_datagram_is_recorded(guard: EgressGuard, audit: AuditLog) -> None:
    """Same record as a refused connection. An attempt nobody can see is an attempt nobody counts."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client, pytest.raises(EgressBlocked):
        client.sendto(b"leaving", OFF_MACHINE)

    assert "blocked" in audit.path.read_text(encoding="utf-8")


def test_loopback_datagrams_are_still_delivered(guard: EgressGuard) -> None:
    """The guard must not break the local path, so this asserts arrival rather than absence of error."""
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(("127.0.0.1", 0))
    receiver.settimeout(1)

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(b"ping", receiver.getsockname())

        assert receiver.recv(4) == b"ping"
    finally:
        receiver.close()


def test_ipv6_loopback_is_loopback(guard: EgressGuard) -> None:
    server = socket.socket(socket.AF_INET6)
    server.bind(("::1", 0))
    server.listen(1)
    host, port = server.getsockname()[:2]

    try:
        with socket.socket(socket.AF_INET6) as client:
            client.connect((host, port))
    finally:
        server.close()


def test_localhost_by_name_is_loopback(guard: EgressGuard, listener: tuple[str, int]) -> None:
    """Written down because it is an exception the code makes deliberately.

    By the time connect runs the guard sees an address, not a hostname - except for this one, which
    libraries pass through literally often enough that refusing it would break the local path for no
    security gain.
    """
    with socket.socket() as client:
        client.connect(("localhost", listener[1]))


def test_a_hostname_the_guard_cannot_resolve_to_an_address_is_refused(guard: EgressGuard) -> None:
    """Fails closed. An address the guard cannot interpret is not an address it may permit."""
    with socket.socket() as client, pytest.raises(EgressBlocked):
        client.connect(("example.invalid", 443))


def test_cloud_mode_permits_outbound(guard: EgressGuard, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cloud mode opens the door, and the door is all this guard controls.

    Which host is reached is the provider client's constraint, not this one's - SECURITY.md section
    11 says so and the test is written to match rather than to imply more.

    The underlying connect is replaced so the suite makes no real outbound attempt; what is being
    asserted is the guard's decision, not the network's.
    """
    reached: list[tuple[str, int]] = []
    monkeypatch.setattr(guard, "_connect", lambda sock, address: reached.append(address))
    guard.set_mode("cloud")

    with socket.socket() as client:
        client.connect(OFF_MACHINE)

    assert reached == [OFF_MACHINE]


def test_cloud_mode_records_every_departure(guard: EgressGuard, audit: AuditLog, monkeypatch) -> None:
    """Visibility is the only thing Cloud mode guarantees, so it is asserted explicitly."""
    monkeypatch.setattr(guard, "_connect", lambda sock, address: None)
    guard.set_mode("cloud")

    with socket.socket() as client:
        client.connect(OFF_MACHINE)

    entries = audit.path.read_text(encoding="utf-8").splitlines()
    assert len(entries) == 1
    assert "203.0.113.7" in entries[0]


def test_a_refusal_is_recorded_too(guard: EgressGuard, audit: AuditLog) -> None:
    with socket.socket() as client, pytest.raises(EgressBlocked):
        client.connect(OFF_MACHINE)

    assert "blocked" in audit.path.read_text(encoding="utf-8")


def test_loopback_traffic_is_not_recorded(
    guard: EgressGuard, audit: AuditLog, listener: tuple[str, int]
) -> None:
    """Telemetry runs at 1 Hz and the model server is local.

    A log recording those would be a log nobody reads, and the entries that matter would be buried
    in it. The audit exists to make *departures* visible.
    """
    with socket.socket() as client:
        client.connect(listener)

    assert not audit.path.exists()


def test_the_mode_starts_local(audit: AuditLog) -> None:
    """Absence is not permission - the same rule trust.json follows.

    The state before anybody chooses anything has to be the one that sends nothing.
    """
    assert EgressGuard(audit=audit).mode == "local"


def test_uninstalling_restores_the_original_socket(audit: AuditLog, listener: tuple[str, int]) -> None:
    installed = EgressGuard(audit=audit)
    installed.install()
    installed.uninstall()

    with socket.socket() as client:
        client.connect(listener)


def test_uninstalling_restores_every_method_it_patched(audit: AuditLog) -> None:
    """Named one by one, so a method added to ``install`` and forgotten in ``uninstall`` fails here.

    A patch left behind outlives the guard that owns it and applies to whatever runs next.
    """
    before = {name: getattr(socket.socket, name) for name in GUARDED_METHODS}

    installed = EgressGuard(audit=audit)
    installed.install()
    patched = {name: getattr(socket.socket, name) for name in GUARDED_METHODS}
    installed.uninstall()

    assert all(patched[name] is not before[name] for name in GUARDED_METHODS)
    assert {name: getattr(socket.socket, name) for name in GUARDED_METHODS} == before
