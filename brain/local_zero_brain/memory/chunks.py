"""The two kinds of remembered text, and the reason they are two types rather than one flag.

docs/SECURITY.md section 2, invariants 1-3. The tempting design is one ``Chunk`` with a ``trusted:
bool`` on it, and it fails in a specific, known way: the flag has to be checked at every use, and a
single missed check is the whole exploit. There is no missed check available here, because there is
no shared type to forget to check - the planner's context assembly takes ``TrustedChunk`` and the
reader takes ``UntrustedChunk``, and neither will accept the other.

**The field names differ on purpose.** ``TrustedChunk(**vars(untrusted))`` is how a conversion gets
written by somebody in a hurry who believes these are one shape with two labels. It raises, because
they are not one shape: trusted text knows which note the user wrote it in, untrusted text knows
where it was fetched from, and those are genuinely different facts rather than the same fact renamed.

Both are frozen. Trust is decided once, where the text enters the system, and a chunk whose label
could be edited afterwards is a re-labelling waiting to happen.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TrustedChunk:
    """Text the user wrote themselves. The only kind the planner may see.

    In M4.5 this comes from a hand-written note in the vault. Nothing Local Zero produces is ever
    stamped this way: an agent-written note is untrusted no matter which folder it sits in, because
    a model that could author its own trusted context could author its own instructions.
    """

    #: Where the user wrote it, relative to the vault. Shown when the answer cites its source.
    note_path: str
    text: str
    written_at: str


@dataclass(frozen=True, slots=True)
class UntrustedChunk:
    """Text from anywhere else. Fetched pages, file contents, telemetry strings, agent notes.

    It reaches the reader and the user. It does not reach anything that can act - not wrapped in
    delimiters, not prefixed with a warning, not summarised first. Summarising is not laundering:
    a summary of an instruction is still an instruction.
    """

    #: A URL, a path, ``telemetry:<field>`` - whatever identifies the origin to a human reading it.
    source: str
    text: str
    fetched_at: str
