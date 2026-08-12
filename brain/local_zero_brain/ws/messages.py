"""Builds the frames the brain sends to the UI.

Messages are built as plain dicts and then validated against the Pydantic mirror before leaving
this module. Two reasons for that order rather than building models and dumping them:

* The telemetry payload is **forwarded unchanged**. Round-tripping it through a model would
  re-serialize numbers the brain did not produce, and a value that arrives spelled differently
  than it was sent is a divergence nobody asked for.
* Validating on the way out means the brain cannot emit a frame that violates its own contract.
  A frame that fails here is a bug in this layer, and it is raised rather than sent.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from local_zero_brain import __version__
from local_zero_brain.contracts.common import CONTRACT_VERSION, MAX_ERROR_MESSAGE_LENGTH
from local_zero_brain.contracts.ws import (
    WS_MESSAGE_ADAPTER,
    ToolLogStatus,
    TurnStateName,
    WsErrorCode,
)

#: RFC 3339, UTC, seconds and coarser. Milliseconds are appended separately because strftime has
#: no millisecond directive and %f would give six digits, which the contract's pattern rejects.
_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S."

#: What the brain declares before the sidecar has said otherwise. The UI needs a poll interval to
#: reason about staleness from its very first frame, and the schema has no "unknown" to express.
DEFAULT_POLL_INTERVAL_MS = 1000

MAX_CAPTION_LENGTH = 2000
MAX_DETAIL_LENGTH = 120


def utc_now() -> str:
    """The current instant in the contract's timestamp format."""
    now = datetime.now(UTC)
    return f"{now.strftime(_TIMESTAMP_FORMAT)}{now.microsecond // 1000:03d}Z"


class WsMessageFactory:
    """Stamps envelopes. The only place a WebSocket frame is constructed."""

    def __init__(self, now: Callable[[], str] = utc_now, app_version: str = __version__) -> None:
        self._now = now
        self._app_version = app_version

    def server_hello(
        self,
        *,
        poll_interval_ms: int,
        system_connected: bool,
        sensors: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._envelope(
            "server.hello",
            {
                "component": "brain",
                "app_version": self._app_version,
                "poll_interval_ms": poll_interval_ms,
                "system_connected": system_connected,
                "sensors": sensors,
            },
        )

    def system_status(
        self,
        *,
        connected: bool,
        since: str,
        reason: str | None,
        sensors: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._envelope(
            "system.status",
            {
                "connected": connected,
                "since": since,
                "reason": _truncate(reason),
                "sensors": sensors,
            },
        )

    def telemetry_sample(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Forwards a sample, re-stamping the envelope and nothing else.

        ``payload`` is the object that arrived from the system layer, passed through by reference
        rather than rebuilt. docs/CONTRACTS.md section 1: in M1 the brain forwards
        telemetry.sample unchanged apart from the envelope, and the first time that stops being
        true the divergence gets written down.
        """
        return self._envelope("telemetry.sample", payload)

    def approval_request(
        self,
        *,
        request_id: str,
        capability: str,
        resolved_args: dict[str, Any],
        affected_paths: list[str],
        side_effect: str,
        origin: str,
    ) -> dict[str, Any]:
        """The payload the user decides on.

        Everything here comes from the invocation *after* it passed the guard's first three steps -
        the resolved arguments and the computed paths, not a description of intent. The brain builds
        it; nothing else contributes a field. docs/SECURITY.md section 5.
        """
        return self._envelope(
            "approval.request",
            {
                "request_id": request_id,
                "capability": capability,
                "resolved_args": resolved_args,
                "affected_paths": affected_paths,
                "side_effect": side_effect,
                "origin": origin,
            },
        )

    def approval_resolved(self, *, request_id: str, outcome: str) -> dict[str, Any]:
        return self._envelope("approval.resolved", {"request_id": request_id, "outcome": outcome})

    def trust_status(self, *, enabled: bool, since: str) -> dict[str, Any]:
        return self._envelope("trust.status", {"enabled": enabled, "since": since})

    def provider_status(self, *, mode: str, model: str, has_key: bool, since: str) -> dict[str, Any]:
        """What the UI is told about the model layer.

        ``has_key`` is a boolean and that is the whole of it. The key is never sent to the UI - not
        the value, not a prefix, not its length. There is a rejected contract example holding that
        line, because "just show the last four characters" is exactly the change somebody makes
        later in good faith.
        """
        return self._envelope(
            "provider.status", {"mode": mode, "model": model, "has_key": has_key, "since": since}
        )

    def memory_status(
        self,
        *,
        enabled: bool,
        vault: str | None,
        notes: int,
        chunks: int,
        embedded_chunks: int,
        last_indexed_at: str | None,
        embeddings_available: bool,
    ) -> dict[str, Any]:
        return self._envelope(
            "memory.status",
            {
                "enabled": enabled,
                "vault": vault,
                "notes": notes,
                "chunks": chunks,
                "embedded_chunks": embedded_chunks,
                "last_indexed_at": last_indexed_at,
                "embeddings_available": embeddings_available,
            },
        )

    def turn_state(
        self,
        *,
        state: TurnStateName,
        since: str,
        caption: str | None = None,
        detail: str | None = None,
    ) -> dict[str, Any]:
        """What the brain is doing right now, for the core and the caption line.

        ``caption`` is None when there is nothing to say, and None travels as None. Empty prose is
        deliberately not the same thing: the contract rejects "" so that a turn which produced no
        speech cannot arrive looking like a caption that failed to render.
        """
        return self._envelope(
            "turn.state",
            {
                "state": state,
                "since": since,
                "caption": _truncate(_or_none(caption), MAX_CAPTION_LENGTH),
                "detail": _truncate(_or_none(detail), MAX_DETAIL_LENGTH),
            },
        )

    def tool_log(
        self, *, at: str, capability: str, message: str, status: ToolLogStatus
    ) -> dict[str, Any]:
        """One line of the tool log, emitted as it happens.

        ``message`` may paraphrase content the brain fetched. It leaves here as text and the UI
        renders it as text; it is never routed back into the planner. docs/SECURITY.md section 2.
        """
        return self._envelope(
            "tool.log",
            {
                "at": at,
                "capability": capability[:MAX_DETAIL_LENGTH],
                "message": _truncate(message, MAX_ERROR_MESSAGE_LENGTH) or "(no detail was recorded)",
                "status": status,
            },
        )

    def error(self, *, code: WsErrorCode, message: str, in_reply_to: str | None = None) -> dict[str, Any]:
        return self._envelope(
            "error",
            {
                "code": code,
                "message": _truncate(message) or "An unspecified error occurred.",
                "in_reply_to": in_reply_to,
            },
        )

    def _envelope(self, message_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        message = {
            "v": CONTRACT_VERSION,
            "id": str(uuid4()),
            "ts": self._now(),
            "type": message_type,
            "payload": payload,
        }

        # The brain does not get to emit a frame that violates its own contract.
        WS_MESSAGE_ADAPTER.validate_python(message)
        return message


def _truncate(message: str | None, limit: int = MAX_ERROR_MESSAGE_LENGTH) -> str | None:
    """Keeps prose inside the length the schema allows.

    An over-long message would fail validation wholesale, and a rejected error frame tells the UI
    nothing about the fault that produced it.
    """
    if message is None:
        return None

    return message[:limit]


def _or_none(prose: str | None) -> str | None:
    """Blank prose is silence, and silence is spelled None.

    This is about model output, not about hiding a caller's mistake. A provider that returns "" or a
    lone newline has said nothing, and the contract has exactly one spelling for that. Passing the
    empty string through would fail validation and take down a turn over a model being quiet.
    """
    if prose is None or not prose.strip():
        return None

    return prose
