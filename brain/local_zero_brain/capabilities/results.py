"""What a capability hands back when it read something.

Most capabilities do a thing and have nothing to say about it: ``write_text_file`` writes, and the
tool log recording that it finished is the whole story. A capability that *reads* is different -
the answer is the point, and before this existed there was nowhere to put it. ``_execute`` called
the handler and dropped the return value on the floor.

**A table rather than free text.** A process list and a game library are rows and columns, and
paraphrasing them into a 500-character log line throws away the thing the user asked for.

**Every cell is a string, decided once, here.** These values come from outside the system - a
process name, a game title, a path someone else chose - which makes them untrusted text by
docs/SECURITY.md section 2. Converting at the boundary means nothing downstream is holding a number
it might compute with, and the UI renders text nodes rather than deciding how to format a float.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

#: Mirrors contracts/ws.schema.json. Named here rather than imported from the contract model so a
#: handler can be truncated without the capability layer depending on the wire format.
MAX_ROWS = 200

#: Long enough for a full path, short enough that 200 rows stay a sane frame.
MAX_CELL_LENGTH = 256


@dataclass(frozen=True, slots=True)
class ResultTable:
    """Rows a capability read, with the header they belong under.

    Frozen: the table is what the handler found, and a caller that could edit it after the fact
    could put a value under a heading the handler never chose.
    """

    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    #: True when there were more rows than fit. Carried rather than inferred from ``len(rows) ==
    #: MAX_ROWS``, which would be a guess that is wrong for a machine with exactly 200 processes.
    truncated: bool

    @staticmethod
    def of(columns: Sequence[str], rows: Iterable[Sequence[object]]) -> ResultTable:
        """Builds a table, stringifying cells and truncating to what the contract allows.

        Truncation happens here rather than at the wire, so the flag is set by the code that knows
        it dropped something. A frame trimmed to fit downstream would arrive saying ``truncated:
        false`` about a list that had been cut.
        """
        header = tuple(str(column) for column in columns)

        kept: list[tuple[str, ...]] = []
        truncated = False

        for row in rows:
            if len(kept) >= MAX_ROWS:
                truncated = True
                break

            kept.append(tuple(str(cell)[:MAX_CELL_LENGTH] for cell in row))

        return ResultTable(columns=header, rows=tuple(kept), truncated=truncated)
