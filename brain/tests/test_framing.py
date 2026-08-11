"""NDJSON framing on the brain's side of the pipe."""

from __future__ import annotations

from local_zero_brain.ipc.framing import LineAssembler, encode_line


def test_each_line_is_returned_as_one_message() -> None:
    assembler = LineAssembler()

    lines = assembler.feed(b'{"a":1}\n{"b":2}\n')

    assert lines == ['{"a":1}', '{"b":2}']


def test_a_message_split_across_chunks_is_reassembled() -> None:
    assembler = LineAssembler()

    assert assembler.feed(b'{"a":') == []
    assert assembler.feed(b'1}\n') == ['{"a":1}']


def test_a_partial_message_is_not_returned_until_its_newline_arrives() -> None:
    assembler = LineAssembler()

    assert assembler.feed(b'{"a":1}') == []


def test_carriage_returns_are_stripped() -> None:
    assembler = LineAssembler()

    assert assembler.feed(b'{"a":1}\r\n') == ['{"a":1}']


def test_blank_lines_are_not_surfaced() -> None:
    assembler = LineAssembler()

    assert assembler.feed(b'\n\n{"a":1}\n\n') == ['{"a":1}']


def test_an_oversized_line_is_dropped_counted_and_recovered_from() -> None:
    """The stream resynchronises on the next newline instead of the connection being torn down."""
    dropped = 0

    def count() -> None:
        nonlocal dropped
        dropped += 1

    assembler = LineAssembler(on_oversized=count, max_line_bytes=16)

    lines = assembler.feed(b"x" * 64 + b"\n" + b'{"a":1}' + b"\n")

    assert dropped == 1
    assert lines == ['{"a":1}']


def test_undecodable_bytes_are_dropped_and_counted() -> None:
    """Decoding is strict: a message that decodes to something other than what was sent is exactly
    the quiet corruption the contract exists to prevent."""
    dropped = 0

    def count() -> None:
        nonlocal dropped
        dropped += 1

    assembler = LineAssembler(on_undecodable=count)

    lines = assembler.feed(b"\xff\xfe invalid \n" + b'{"a":1}' + b"\n")

    assert dropped == 1
    assert lines == ['{"a":1}']


def test_encoded_lines_carry_no_byte_order_mark() -> None:
    encoded = encode_line('{"a":1}')

    assert not encoded.startswith(b"\xef\xbb\xbf")
    assert encoded.endswith(b"\n")


def test_an_encoded_line_survives_a_round_trip() -> None:
    assembler = LineAssembler()

    assert assembler.feed(encode_line('{"a":1}')) == ['{"a":1}']
