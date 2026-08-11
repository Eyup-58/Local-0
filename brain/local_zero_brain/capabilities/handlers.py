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


def read_text_file(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write_text_file(path: str, content: str) -> None:
    Path(path).write_text(content, encoding="utf-8")


def delete_file(path: str) -> None:
    Path(path).unlink()


def build_registry(workspace: Path, wide_root: Path | None = None) -> CapabilityRegistry:
    """The M2 registry.

    ``wide_root`` exists to exercise decision 1 of the M2 plan: a capability may legitimately declare
    a root far wider than the workspace, and the guard's answer to that is escalation to
    ``destructive`` rather than a refusal to let it be declared. Production callers pass only the
    workspace; the wider case is what M5 will look like.
    """
    read_roots = (workspace, wide_root) if wide_root is not None else (workspace,)

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
        ]
    )
