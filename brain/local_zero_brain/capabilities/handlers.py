"""Every registered capability, and nothing else may run.

M2 opened with three example capabilities, one per side effect, existing so the guard had something
real to be proven against. M4.5 added memory's three, and M5 the ones that answer a question about
the machine. The originals are boring on purpose: ``delete_file`` taking a ``path`` is the same
shape SECURITY.md section 5 already uses for its approval payload example, so the document and the
code say the same thing.

**A handler never sees a raw argument.** By the time one runs, the guard has validated the schema,
canonicalised every path and proven containment, and the handler is called with the resolved values.
That is why these functions are three lines each: everything that could go wrong has already been
decided somewhere a test can reach it.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Annotated

import psutil
from pydantic import Field, field_validator

from local_zero_brain.capabilities.registry import (
    Capability,
    CapabilityArgs,
    CapabilityRegistry,
    PathArgument,
)
from local_zero_brain.capabilities.results import ResultTable
from local_zero_brain.capabilities import steam

_BYTES_PER_MB = 1024 * 1024

#: The only thing launch_application will start. See LaunchApplicationArgs for why.
_EXECUTABLE_SUFFIX = ".exe"

#: Absolute, under the Windows directory, rather than the bare name.
#:
#: ``explorer.exe`` alone is resolved by CreateProcess's search order, and a directory earlier in
#: that order holding a file of that name decides what launches - the classic binary-planting
#: vector. Found by /threat-check on this capability, which M5 requires per capability rather than
#: once at the milestone boundary; the bare name was in the first version of open_folder.
#:
#: ``SystemRoot`` rather than a hardcoded ``C:\\Windows``: the drive letter is not guaranteed, and a
#: path that is wrong on somebody else's machine is a capability that silently does nothing there.
_EXPLORER = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "explorer.exe")

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


class ListProcessesArgs(CapabilityArgs):
    """No arguments at all.

    Not even a filter. A name to match on would be a string from the model reaching an enumeration,
    and the whole list is 200 rows at most anyway - there is nothing here for a filter to save.
    """


class OpenFolderArgs(CapabilityArgs):
    path: PathArgument


class LaunchApplicationArgs(CapabilityArgs):
    path: PathArgument

    @field_validator("path")
    @classmethod
    def must_be_an_executable(cls, value: str) -> str:
        """Only ``.exe``, and this is not fussiness about file types.

        CreateProcess cannot run a ``.bat`` or ``.cmd`` directly - Windows hands it to the command
        interpreter. So launching one **is** a shell invocation, arriving by the back door that red
        line 3 closes at the front, with an argument a model chose. ``.lnk`` is worse: a shortcut
        names its own target and arguments, so containing the path the guard checked would say
        nothing about what actually ran.

        Checked on the argument rather than in the handler: step 2 is where an invalid argument is
        supposed to die, and a handler that re-checked would be the second place to keep in step.
        """
        if not value.lower().endswith(_EXECUTABLE_SUFFIX):
            raise ValueError(
                f"only {_EXECUTABLE_SUFFIX} may be launched. A batch file is run by the command "
                "interpreter and a shortcut names its own target, so neither is contained by the "
                "path checked here"
            )

        return value


def launch_application(path: str) -> None:
    """Starts a program.

    One argument: the executable, and nothing after it. Arguments a model composed would be a
    command line in everything but name, and there is no request yet that needs them - when there
    is, it is a deliberate change with its own validation rather than a parameter that was always
    quietly there.

    ``shell=False`` with a list, so nothing between here and CreateProcess parses the string.
    """
    subprocess.Popen([path], shell=False)  # noqa: S603 - argv list, no shell, .exe enforced above


def _launch_roots() -> tuple[Path, ...]:
    """Where a program may be launched from: the program directories, and Steam's libraries.

    Games are the reason for the second half - they live wherever Steam was pointed, which on this
    machine is ``d:\\steam``, and a launcher restricted to Program Files would refuse every one of
    them. The libraries are discovered at startup from Steam's own files rather than guessed.

    Every root here is outside the workspace, so the escalation rule raises a launch to
    ``destructive`` and it stops for a human showing the resolved path. Breadth costs an approval.
    """
    directories = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("ProgramW6432"),
    ]

    roots: list[Path] = []
    seen: set[Path] = set()

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        directories.append(str(Path(local_app_data) / "Programs"))

    for directory in [*directories, *(str(library) for library in steam.library_paths())]:
        if not directory:
            continue

        resolved = Path(directory).resolve()
        # A root that does not exist is dropped rather than declared: containment against a path
        # nothing can reach is a rule that reads as protection and enforces nothing.
        if resolved in seen or not resolved.is_dir():
            continue

        seen.add(resolved)
        roots.append(resolved)

    return tuple(roots)


def list_processes() -> ResultTable:
    """What is running, as name, pid, CPU and working set.

    **Metadata only.** ``psutil`` reads what Windows already publishes about a process; nothing here
    opens a handle to another process's address space, which is the M5 exit criterion that rules
    out the obvious alternative implementations.

    A process that exits mid-enumeration is skipped rather than fatal - the list is a snapshot of a
    moving thing, and a scan that failed because something closed while it ran would fail most of
    the time on a busy machine.
    """
    rows: list[tuple[str, int, str, int]] = []

    for process in psutil.process_iter(["name", "pid", "cpu_percent", "memory_info"]):
        try:
            info = process.info
            memory = info.get("memory_info")
            rows.append(
                (
                    info.get("name") or "(unnamed)",
                    info["pid"],
                    f"{info.get('cpu_percent') or 0.0:.1f}",
                    round((memory.rss if memory else 0) / _BYTES_PER_MB),
                )
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            # Exited between the enumeration and the read, or belongs to another user. Neither is
            # an error: it is a row we do not have, and the alternative is a capability that fails
            # whenever the machine is busy.
            continue

    # Heaviest first. A 200-row cap has to drop something, and the processes worth seeing are the
    # ones using the machine rather than the ones that sort early by name.
    rows.sort(key=lambda row: row[3], reverse=True)

    return ResultTable.of(("name", "pid", "cpu_percent", "memory_mb"), rows)


def open_folder(path: str) -> None:
    """Opens a folder in Explorer.

    ``ArgumentList``-equivalent: a list of arguments with no shell between here and the process, so
    there is no command line for a path to be interpolated into. Red line 3 - and a folder name is
    exactly the kind of string that would carry an ampersand into a shell that honoured it.
    """
    subprocess.Popen([_EXPLORER, path], shell=False)  # noqa: S603 - argv list, no shell


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
    workspace: Path,
    wide_root: Path | None = None,
    *,
    vault: Path | None = None,
    launch_roots: tuple[Path, ...] | None = None,
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
                name="list_processes",
                args_schema=ListProcessesArgs,
                side_effect="read",
                # None, and this capability is the reason that is now expressible. It takes no path,
                # so step 3 has nothing to resolve and any root declared here would never be
                # consulted - a containment claim no code enforces.
                allowed_roots=(),
                handler=list_processes,
            ),
            Capability(
                name="open_folder",
                args_schema=OpenFolderArgs,
                side_effect="write",
                allowed_roots=(workspace,),
                handler=open_folder,
            ),
            Capability(
                name="launch_application",
                args_schema=LaunchApplicationArgs,
                side_effect="destructive",
                # Discovered rather than hardcoded, and overridable so a test can bound it to a
                # directory it controls instead of whatever this machine has installed.
                allowed_roots=launch_roots if launch_roots is not None else _launch_roots(),
                handler=launch_application,
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
