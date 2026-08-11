"""The system-layer session: what the brain accepts from the sidecar, and what it refuses.

Every rule here comes from docs/CONTRACTS.md section 2:

* Validate **before reading any field**. A message that fails validation has no readable fields,
  including for logging - which is why rejections name the offending field rather than echoing its
  value.
* A failed message is **dropped and counted**, never partially applied.
* One bad message does not tear down the connection. Repeated violations do.
* An unknown ``v`` is dropped with ``unsupported_version``. No best-effort parsing.

Plus one rule that is not about shape at all: a sidecar declaring itself elevated is refused
outright, connection and all. See :meth:`SystemSession.handle`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from local_zero_brain.contracts.common import CONTRACT_VERSION
from local_zero_brain.contracts.ipc import IPC_MESSAGE_ADAPTER, IpcHello, IpcMessage
from local_zero_brain.metrics import DropCounters

RejectionCode = Literal["schema_violation", "unsupported_version", "handshake_required"]

#: How many rejected messages a single connection may produce before the brain stops treating it as
#: a peer having a bad moment and starts treating it as a peer it cannot talk to.
MAX_VIOLATIONS_PER_CONNECTION = 50


@dataclass(frozen=True, slots=True)
class Accepted:
    """A message that validated.

    ``raw`` is the parsed JSON exactly as it arrived. The brain forwards the telemetry payload
    unchanged and re-stamps only the envelope, so it forwards this rather than a re-serialization
    of the model - a round trip through Python floats is a chance for the numbers to come out
    spelled differently than they went in.
    """

    message: IpcMessage
    raw: dict


@dataclass(frozen=True, slots=True)
class Rejected:
    """A message that was dropped, with enough detail to log why."""

    code: RejectionCode
    detail: str
    #: True when the connection itself must be dropped, not merely this message.
    fatal: bool = False


class SystemSession:
    """Tracks one pipe connection's state: whether the sidecar has handshaked, and how badly it is
    behaving.

    A new instance is created per connection, so ``seq`` numbering and the handshake gate both
    reset exactly when the contract says they do.
    """

    def __init__(self, counters: DropCounters) -> None:
        self._counters = counters
        self._hello: IpcHello | None = None
        self._violations = 0

    @property
    def hello(self) -> IpcHello | None:
        """The sidecar's declaration, or None before a valid handshake."""
        return self._hello

    @property
    def is_handshaked(self) -> bool:
        return self._hello is not None

    @property
    def should_close(self) -> bool:
        """True once this connection has produced more rejections than it is worth tolerating."""
        return self._violations >= MAX_VIOLATIONS_PER_CONNECTION

    def handle(self, line: str) -> Accepted | Rejected:
        """Validates one inbound line and advances the session."""
        raw = self._parse(line)
        if isinstance(raw, Rejected):
            return self._count(raw)

        version_failure = self._check_version(raw)
        if version_failure is not None:
            return self._count(version_failure)

        # Checked explicitly, ahead of schema validation, because refusing the *connection* is a
        # different action from dropping a *message* and should not be an incidental side effect of
        # a shape check. Local Zero runs every process asInvoker; a sidecar claiming elevation is
        # either not ours or not behaving as described, and neither is something to keep talking to.
        # See docs/ARCHITECTURE.md section 2.
        elevation_failure = self._check_elevation(raw)
        if elevation_failure is not None:
            return self._count(elevation_failure)

        try:
            message = IPC_MESSAGE_ADAPTER.validate_python(raw)
        except ValidationError as error:
            return self._count(Rejected("schema_violation", _summarize(error)))

        return self._accept(message, raw)

    def _parse(self, line: str) -> dict | Rejected:
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            return Rejected("schema_violation", "body is not valid JSON")

        if not isinstance(raw, dict):
            return Rejected("schema_violation", "root is not an object")

        return raw

    def _check_version(self, raw: dict) -> Rejected | None:
        version = raw.get("v")
        if not isinstance(version, int) or isinstance(version, bool):
            return Rejected("schema_violation", "v is missing or not an integer")

        if version != CONTRACT_VERSION:
            return Rejected(
                "unsupported_version",
                f"v is {version}, this build implements {CONTRACT_VERSION}",
            )

        return None

    def _check_elevation(self, raw: dict) -> Rejected | None:
        if raw.get("type") != "hello":
            return None

        payload = raw.get("payload")
        if not isinstance(payload, dict):
            return Rejected("schema_violation", "hello payload is not an object")

        if payload.get("elevated") is not False:
            return Rejected(
                "schema_violation",
                "hello declares elevated other than false, which the contract forbids",
                fatal=True,
            )

        return None

    def _accept(self, message: IpcMessage, raw: dict) -> Accepted | Rejected:
        if isinstance(message, IpcHello):
            if self.is_handshaked:
                return self._count(
                    Rejected("schema_violation", "a second hello arrived on an established connection")
                )

            self._hello = message
            return Accepted(message, raw)

        if not self.is_handshaked:
            # The brain must not accept telemetry before a valid hello. Without the declaration it
            # has no way to tell the UI which nulls are missing sensors and which are read failures.
            return self._count(
                Rejected("handshake_required", f"{message.type} arrived before a valid hello")
            )

        return Accepted(message, raw)

    def _count(self, rejection: Rejected) -> Rejected:
        self._violations += 1

        if rejection.code == "schema_violation":
            self._counters.record_schema_violation()
        elif rejection.code == "unsupported_version":
            self._counters.record_unsupported_version()
        else:
            self._counters.record_handshake_required()

        return rejection


def _summarize(error: ValidationError) -> str:
    """Names the offending field without echoing its value.

    A rejected message has no readable fields, and that includes for logging: repeating the
    contents of something that just failed validation is how untrusted input ends up in a log line
    that something else later reads.
    """
    first = error.errors()[0]
    location = ".".join(str(part) for part in first["loc"]) or "<root>"
    return f"{location}: {first['msg']}"
