"""The turn state and tool log frames the brain sends to drive the core and the caption line.

These two messages are the ones that make the orchestration-center HUD possible without inventing
anything, so the tests here are mostly about what the brain is *not* allowed to send: a caption it
made up to fill a silence, an empty string standing in for having nothing to say, or a frame a
browser tab could have forged.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from local_zero_brain.contracts.ws import CLIENT_MESSAGE_ADAPTER, WS_MESSAGE_ADAPTER
from local_zero_brain.ws.messages import MAX_CAPTION_LENGTH, WsMessageFactory

STAMP = "2026-08-12T18:24:11.418Z"


@pytest.fixture
def factory() -> WsMessageFactory:
    return WsMessageFactory(now=lambda: STAMP)


def test_a_turn_state_frame_validates_against_the_contract(factory: WsMessageFactory) -> None:
    frame = factory.turn_state(state="speaking", since=STAMP, caption="41 notes are indexed.")

    assert frame["type"] == "turn.state"
    assert frame["payload"]["state"] == "speaking"
    assert frame["payload"]["caption"] == "41 notes are indexed."
    # The envelope validates on the way out; this asserts the round trip rather than trusting it.
    WS_MESSAGE_ADAPTER.validate_python(frame)


def test_having_nothing_to_say_travels_as_null_not_as_empty_prose(factory: WsMessageFactory) -> None:
    """A quiet model must not arrive looking like a caption that failed to render."""
    for quiet in ("", "   ", "\n", None):
        frame = factory.turn_state(state="listening", since=STAMP, caption=quiet)

        assert frame["payload"]["caption"] is None, f"{quiet!r} should have become null"


def test_an_empty_caption_is_refused_by_the_contract_itself() -> None:
    """The factory normalises blank prose, and the contract refuses it regardless."""
    with pytest.raises(ValidationError):
        WS_MESSAGE_ADAPTER.validate_python(
            {
                "v": 1,
                "id": "4f8b1c05-6d92-4a37-b0e5-71c8a29d3f6e",
                "ts": STAMP,
                "type": "turn.state",
                "payload": {"state": "speaking", "since": STAMP, "caption": "", "detail": None},
            }
        )


def test_an_over_long_caption_is_cut_rather_than_dropped(factory: WsMessageFactory) -> None:
    """A rejected frame would lose the whole turn; a trimmed one still says what happened."""
    frame = factory.turn_state(state="speaking", since=STAMP, caption="x" * (MAX_CAPTION_LENGTH + 400))

    assert len(frame["payload"]["caption"]) == MAX_CAPTION_LENGTH
    WS_MESSAGE_ADAPTER.validate_python(frame)


def test_an_unknown_turn_state_is_not_a_message(factory: WsMessageFactory) -> None:
    with pytest.raises(ValidationError):
        factory.turn_state(state="daydreaming", since=STAMP)  # type: ignore[arg-type]


def test_a_tool_log_line_validates_and_keeps_its_status(factory: WsMessageFactory) -> None:
    frame = factory.tool_log(
        at=STAMP, capability="memory.search", message="3 chunks matched.", status="ok"
    )

    assert frame["payload"]["capability"] == "memory.search"
    assert frame["payload"]["status"] == "ok"
    WS_MESSAGE_ADAPTER.validate_python(frame)


def test_a_failed_tool_call_is_not_rounded_up_to_ok(factory: WsMessageFactory) -> None:
    frame = factory.tool_log(at=STAMP, capability="net.fetch", message="Refused.", status="failed")

    assert frame["payload"]["status"] == "failed"


def test_an_unknown_tool_status_is_refused(factory: WsMessageFactory) -> None:
    with pytest.raises(ValidationError):
        factory.tool_log(at=STAMP, capability="net.fetch", message="Done.", status="done")  # type: ignore[arg-type]


def test_a_tool_message_that_lost_its_text_still_says_something(factory: WsMessageFactory) -> None:
    """min_length is 1, so an empty message would fail the envelope and lose the line entirely."""
    frame = factory.tool_log(at=STAMP, capability="net.fetch", message="", status="ok")

    assert frame["payload"]["message"]
    WS_MESSAGE_ADAPTER.validate_python(frame)


@pytest.mark.parametrize("message_type", ["turn.state", "tool.log"])
def test_the_ui_cannot_send_these(message_type: str) -> None:
    """Both are brain -> ui only.

    A tab that could send turn.state would be able to paint any state it liked onto the core, and a
    tab that could send tool.log could write lines into the record of what actually ran. Neither is
    in ClientMessage, and this is the test that keeps them out of it.
    """
    payloads = {
        "turn.state": {"state": "speaking", "since": STAMP, "caption": "trust me", "detail": None},
        "tool.log": {"at": STAMP, "capability": "net.fetch", "message": "ok", "status": "ok"},
    }
    frame = {
        "v": 1,
        "id": "7c5d9e30-2b81-4f6a-8d47-c0193fa5be82",
        "ts": STAMP,
        "type": message_type,
        "payload": payloads[message_type],
    }

    # It is a valid frame outbound...
    WS_MESSAGE_ADAPTER.validate_python(frame)
    # ...and not one the UI may originate.
    with pytest.raises(ValidationError):
        CLIENT_MESSAGE_ADAPTER.validate_python(frame)
