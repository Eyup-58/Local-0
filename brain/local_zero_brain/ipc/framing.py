"""Newline-delimited JSON framing for the pipe.

Kept separate from the pipe itself so the rules that matter - length limits, encoding strictness,
resynchronisation after a bad line - can be tested without a Windows handle in the way.
"""

from __future__ import annotations

from collections.abc import Callable

#: The longest line that will be assembled. Without a ceiling, a peer that never sends a newline
#: makes the reader allocate until the process dies. Validate at the boundary, and a length limit
#: is part of that. Matches the system layer's Ndjson.MaxLineBytes.
MAX_LINE_BYTES = 1024 * 1024

_LINE_FEED = 0x0A
_CARRIAGE_RETURN = 0x0D


class LineAssembler:
    """Turns a stream of byte chunks into complete UTF-8 lines.

    Two failures are handled rather than raised, because one malformed message is not a reason to
    tear down a connection:

    * A line longer than :data:`MAX_LINE_BYTES` is discarded and the rest of it skipped, so the
      next newline resynchronises the stream.
    * Bytes that are not valid UTF-8 are discarded. Decoding is strict on purpose - a message that
      decodes to something other than what was sent is exactly the quiet corruption this contract
      exists to prevent, and silently substituting replacement characters would produce one.

    Both call the counter callbacks they were given, so a drop is always observable.
    """

    def __init__(
        self,
        *,
        on_oversized: Callable[[], None] = lambda: None,
        on_undecodable: Callable[[], None] = lambda: None,
        max_line_bytes: int = MAX_LINE_BYTES,
    ) -> None:
        self._buffer = bytearray()
        self._skipping_oversized = False
        self._on_oversized = on_oversized
        self._on_undecodable = on_undecodable
        self._max_line_bytes = max_line_bytes

    def feed(self, chunk: bytes) -> list[str]:
        """Consumes a chunk and returns whatever complete lines it completed."""
        lines: list[str] = []

        for byte in chunk:
            if byte != _LINE_FEED:
                self._accumulate(byte)
                continue

            line = self._finish_line()
            if line is not None:
                lines.append(line)

        return lines

    def _accumulate(self, byte: int) -> None:
        if self._skipping_oversized:
            return

        if len(self._buffer) >= self._max_line_bytes:
            self._skipping_oversized = True
            self._buffer.clear()
            self._on_oversized()
            return

        self._buffer.append(byte)

    def _finish_line(self) -> str | None:
        if self._skipping_oversized:
            self._skipping_oversized = False
            return None

        raw = bytes(self._buffer)
        self._buffer.clear()

        # Tolerate a CRLF sender without leaving a stray carriage return inside the JSON.
        if raw.endswith(b"\r"):
            raw = raw[:-1]

        if not raw:
            return None

        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            self._on_undecodable()
            return None


def encode_line(message: str) -> bytes:
    """Encodes one message for the wire: UTF-8, no BOM, terminated by a single newline."""
    return message.encode("utf-8") + b"\n"
