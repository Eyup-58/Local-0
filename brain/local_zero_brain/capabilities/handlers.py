"""The three example capabilities, one per side effect.

ROADMAP M2 asks for exactly this: one ``read``, one ``write``, one ``destructive``, existing so the
guard has something real to be proven against. They are boring on purpose. ``delete_file`` taking a
``path`` is the same shape SECURITY.md section 5 already uses for its approval payload example, so
the document and the code say the same thing.

**A handler never sees a raw argument.** By the time one runs, the guard has validated the schema,
canonicalised every path and proven containment, and the handler is called with the resolved values.
That is why these functions are three lines each: everything that could go wrong has already been
decided somewhere a test can reach it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import Field

from local_zero_brain.capabilities.registry import (
    Capability,
    CapabilityArgs,
    CapabilityRegistry,
    PathArgument,
)

#: Enough for a note, far short of enough to fill a disk by accident. A bound exists because an
#: unbounded write is a denial-of-service with extra steps.
MAX_CONTENT_LENGTH = 64 * 1024


class ReadTextFileArgs(CapabilityArgs):
    path: PathArgument


class WriteTextFileArgs(CapabilityArgs):
    path: PathArgument
    content: Annotated[str, Field(max_length=MAX_CONTENT_LENGTH)]


class DeleteFileArgs(CapabilityArgs):
    path: PathArgument


class MemoryWriteArgs(CapabilityArgs):
    path: PathArgument
    content: Annotated[str, Field(max_length=MAX_CONTENT_LENGTH)]


class MemoryArchiveArgs(CapabilityArgs):
    path: PathArgument


class MemoryForgetArgs(CapabilityArgs):
    path: PathArgument


def read_text_file(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write_text_file(path: str, content: str) -> None:
    Path(path).write_text(content, encoding="utf-8")


def delete_file(path: str) -> None:
    Path(path).unlink()


#: Written by the handler, never by the caller. See ``memory_write``.
AGENT_FRONTMATTER = "---\nsource: agent\n---\n\n"

_FENCE = "---"


def memory_write(path: str, content: str) -> None:
    """Writes an agent note, stamping its origin rather than accepting one.

    The content is the model's. The frontmatter is not, and that asymmetry is the point: trust in
    the vault is decided by folder, and ``source: agent`` is the corroborating stamp that keeps a
    note untrusted even after a human moves it somewhere trusted. A note able to supply its own
    ``source`` could be born claiming to be the user's writing.

    Any frontmatter block the content arrives with is dropped. Not merged, not validated - dropped,
    because merging means deciding which of two ``source`` values wins, and that decision is exactly
    what must not be available to the side that could be lying.
    """
    Path(path).write_text(AGENT_FRONTMATTER + _without_frontmatter(content), encoding="utf-8")


def memory_archive(path: str, archive_dir: Path) -> None:
    """Moves a note out of recall without destroying it.

    Archiving is what "forget that" does by default. The note stops being recalled because
    ``Archive/`` holds no live memory, and it stays on disk where the user can read it, move it
    back, or delete it themselves.

    ``archive_dir`` is bound by ``build_registry`` rather than passed by the caller. A destination
    the model supplied would be a second path to contain, and there is no reason for it to be
    variable: there is one archive.

    A name that is already taken gets a numeric suffix. ``replace`` would otherwise overwrite the
    previous note of that name, which would make archiving destroy a memory - the one thing this
    operation exists not to do.
    """
    archive_dir.mkdir(parents=True, exist_ok=True)
    source = Path(path)

    target = archive_dir / source.name
    attempt = 1
    while target.exists():
        target = archive_dir / f"{source.stem}-{attempt}{source.suffix}"
        attempt += 1

    source.replace(target)


def _without_frontmatter(content: str) -> str:
    if not content.startswith(f"{_FENCE}\n") and not content.startswith(f"{_FENCE}\r\n"):
        return content

    lines = content.splitlines()
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == _FENCE:
            return "\n".join(lines[index + 1 :]).lstrip("\n")

    return content


def build_registry(
    workspace: Path, wide_root: Path | None = None, *, vault: Path | None = None
) -> CapabilityRegistry:
    """The registry.

    ``wide_root`` exists to exercise decision 1 of the M2 plan: a capability may legitimately declare
    a root far wider than the workspace, and the guard's answer to that is escalation to
    ``destructive`` rather than a refusal to let it be declared.

    ``vault`` adds the three memory capabilities, and their roots are the whole of their safety:

    * ``memory_write`` may write only inside ``LocalZero/``, so an agent note cannot be created in a
      folder the index would read as the user's own writing;
    * ``memory_archive`` may move only what is inside ``LocalZero/`` - archiving takes a memory out
      of recall, and a model able to archive the user's notes could retire whatever stood in its
      way;
    * ``memory_forget`` may delete only inside ``Archive/``, which makes forgetting a two-step:
      archive first, then delete what is archived. A single call that removed a live note would make
      "forget that" unrecoverable on the first try.
    """
    read_roots = (workspace, wide_root) if wide_root is not None else (workspace,)

    memory: list[Capability] = []
    if vault is not None:
        agent_notes = vault / "LocalZero"
        archive = vault / "Archive"

        memory = [
            Capability(
                name="memory_write",
                args_schema=MemoryWriteArgs,
                side_effect="write",
                allowed_roots=(agent_notes,),
                handler=memory_write,
            ),
            Capability(
                name="memory_archive",
                args_schema=MemoryArchiveArgs,
                side_effect="write",
                allowed_roots=(agent_notes,),
                handler=lambda path: memory_archive(path, archive),
            ),
            Capability(
                name="memory_forget",
                args_schema=MemoryForgetArgs,
                side_effect="destructive",
                allowed_roots=(archive,),
                handler=delete_file,
            ),
        ]

    return CapabilityRegistry(
        [
            Capability(
                name="read_text_file",
                args_schema=ReadTextFileArgs,
                side_effect="read",
                allowed_roots=read_roots,
                handler=read_text_file,
            ),
            Capability(
                name="write_text_file",
                args_schema=WriteTextFileArgs,
                side_effect="write",
                allowed_roots=(workspace,),
                handler=write_text_file,
            ),
            Capability(
                name="delete_file",
                args_schema=DeleteFileArgs,
                side_effect="destructive",
                allowed_roots=(workspace,),
                handler=delete_file,
            ),
            *memory,
        ]
    )
