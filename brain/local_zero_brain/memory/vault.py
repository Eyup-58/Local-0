"""Reading the Obsidian vault: which files are notes, what is in them, and what may be trusted.

The vault is the trusted half of memory, chosen by the user when this milestone was planned. It is
markdown a human wrote, in a directory they already keep open, which is the whole appeal: memory
that can be read and corrected without this product's help.

**Location decides trust. Frontmatter can only take it away.**

That asymmetry is the load-bearing rule of M4.5, and it exists because the obvious design fails in a
way that is invisible until it is pointed out: whoever writes a file writes its frontmatter. If
`source: user` were enough to be trusted, then an agent able to write a note could stamp its own
text as the user's and read it back next session as their own words - docs/SECURITY.md section 2's
structural break, rebuilt from the inside. What an agent does *not* control is which folder it may
write to, because the guard's ``allowed_roots`` decides that. So the folder is the signal, and the
frontmatter is corroboration that may downgrade and never upgrade.

The two folder sets do not nest, deliberately. An untrusted folder inside a trusted one would turn
this lookup into a most-specific-prefix problem, and one prefix bug silently promotes agent text.

Everything here fails closed: an unknown folder is untrusted, the vault root is untrusted, and an
unset or missing vault means memory is **off** rather than broken - the telemetry panel and the
approval flow do not depend on it and must keep working without it.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal

#: Where the vault is. Read from the environment, never hardcoded - a user-specific path in source
#: is a path that is wrong for everybody else, and this repository is going public.
VAULT_ENV = "OBSIDIAN_VAULT_PATH"

#: Folders a human writes by hand. In M4.5 phase 6 the guard refuses every capability write here.
TRUSTED_FOLDERS = frozenset({"Memory", "Projects", "Knowledge", "System"})

#: Folders Local Zero writes to. Everything read from them is untrusted, whatever it says about
#: itself, and promotion out of them is a human moving a file.
AGENT_FOLDERS = frozenset({"LocalZero", "Conversations", "Archive"})

#: Obsidian's own directories. `.trash` holds notes the user deleted; indexing it would put their
#: deleted thoughts back into recall.
SKIPPED_DIRECTORIES = frozenset({".obsidian", ".trash", ".git"})

#: Long enough to hold a section, short enough that several fit in a prompt budget.
MAX_CHUNK_CHARS = 1200

Trust = Literal["trusted", "untrusted"]

_FENCE = "---"


def _noop(_: str) -> None:
    return None


@dataclass(frozen=True, slots=True)
class NoteMetadata:
    """The note's frontmatter, as flat scalars.

    Parsed by hand rather than with PyYAML. The metadata this product writes is seven flat keys, and
    a dependency to read seven flat keys is not earned - particularly one whose full grammar can
    construct objects. Anything this parser does not understand is skipped and logged, which for a
    hand-edited vault is the behaviour that keeps one bad line from costing a whole note.
    """

    fields: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    @property
    def source(self) -> str | None:
        return self.fields.get("source")

    @property
    def status(self) -> str | None:
        return self.fields.get("status")

    @property
    def note_type(self) -> str | None:
        return self.fields.get("type")


def vault_root() -> Path | None:
    """The configured vault, or None when there is not one to read."""
    configured = os.environ.get(VAULT_ENV)
    if not configured:
        return None

    root = Path(configured)
    return root if root.is_dir() else None


def parse_note(text: str, *, log: Callable[[str], None] = _noop) -> tuple[NoteMetadata, str]:
    """Splits a note into its frontmatter and its body."""
    if not text.startswith(f"{_FENCE}\n") and not text.startswith(f"{_FENCE}\r\n"):
        return NoteMetadata(), text

    lines = text.splitlines()
    try:
        closing = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == _FENCE)
    except StopIteration:
        # An unterminated fence. Treating the rest as metadata would silently empty the note, so the
        # whole thing stays body and the note is merely metadata-less.
        log("frontmatter fence is not closed; the note is being read as body only")
        return NoteMetadata(), text

    parsed: dict[str, str] = {}
    for line in lines[1:closing]:
        if not line.strip():
            continue

        key, separator, value = line.partition(":")
        if not separator or not key.strip() or key.startswith((" ", "\t", "-")):
            # Nested structures and list items are not supported and are not guessed at.
            log(f"frontmatter line is not a scalar and was skipped: {line.strip()[:60]}")
            continue

        parsed[key.strip()] = value.strip().strip("\"'")

    return NoteMetadata(MappingProxyType(parsed)), "\n".join(lines[closing + 1 :])


def trust_of(relative_path: Path, metadata: NoteMetadata) -> Trust:
    """Where this note's text may go. See the module docstring - this is the rule."""
    if metadata.source == "agent":
        return "untrusted"

    parts = relative_path.parts
    if len(parts) < 2:
        # A note loose in the vault root. Nobody has said what it is, so it is not trusted.
        return "untrusted"

    return "trusted" if parts[0] in TRUSTED_FOLDERS else "untrusted"


def iter_notes(root: Path) -> Iterator[Path]:
    """Every markdown file in the vault, skipping Obsidian's own directories."""
    for path in sorted(root.rglob("*.md")):
        relative = path.relative_to(root)
        if any(part in SKIPPED_DIRECTORIES for part in relative.parts):
            continue

        yield path


def chunk_body(body: str) -> list[tuple[str | None, str]]:
    """Splits a note into (heading, text) pieces.

    Returns plain tuples rather than chunk objects on purpose: this function does not know whether
    the note is trusted, and a function that constructed either type would be one edit away from
    constructing the wrong one. The index stores the trust alongside the text, and the two search
    functions - one per type - are where a chunk object is finally built.
    """
    sections: list[tuple[str | None, list[str]]] = [(None, [])]

    for line in body.splitlines():
        if line.startswith("#"):
            sections.append((line.strip(), []))
        else:
            sections[-1][1].append(line)

    chunks: list[tuple[str | None, str]] = []
    for heading, lines in sections:
        text = "\n".join(lines).strip()
        if heading is None and not text:
            continue

        joined = f"{heading}\n{text}".strip() if heading else text
        chunks.extend((heading, piece) for piece in _split_to_size(joined))

    return chunks


def _split_to_size(text: str) -> list[str]:
    """Breaks a long section on whitespace, so a chunk is never cut mid-word.

    Truncating instead would lose the end of every long section without saying so, which is the kind
    of quiet data loss that only shows up as memory that mysteriously forgets the second half of
    things.
    """
    if len(text) <= MAX_CHUNK_CHARS:
        return [text] if text else []

    pieces: list[str] = []
    remaining = text

    while len(remaining) > MAX_CHUNK_CHARS:
        cut = remaining.rfind(" ", 0, MAX_CHUNK_CHARS)
        if cut <= 0:
            cut = MAX_CHUNK_CHARS

        pieces.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()

    if remaining:
        pieces.append(remaining)

    return pieces
