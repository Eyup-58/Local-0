"""The first capabilities that act on the machine rather than on the vault.

M2 built the guard against three example capabilities and M4.5 added memory's three. These are the
first that answer a question about the running system, which puts two of the milestone's exit
criteria on them directly: game and process reads are **strictly read-only**, and **no capability
reads or writes another process's memory**.

The second is the one worth being precise about. ``psutil`` reads what Windows already publishes
about a process - a name, a pid, accumulated CPU time, working set size. It does not open a handle
to another process's address space, and nothing here asks it to.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from local_zero_brain.audit import AuditLog
from local_zero_brain.capabilities.guard import Allowed, Denied, Guard, Invocation, Pending
from local_zero_brain.capabilities.handlers import build_registry
from local_zero_brain.capabilities.results import MAX_ROWS, ResultTable


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    return root


@pytest.fixture
def guard(tmp_path: Path, workspace: Path) -> Guard:
    return Guard(
        registry=build_registry(workspace),
        workspace=workspace,
        audit=AuditLog(tmp_path / "audit.jsonl"),
    )


class TestListProcesses:
    def test_it_returns_a_table_of_what_is_running(self, guard: Guard) -> None:
        capability = guard._registry.get("list_processes")  # noqa: SLF001

        table = capability.handler()

        assert isinstance(table, ResultTable)
        assert table.columns == ("name", "pid", "cpu_percent", "memory_mb")
        assert table.rows, "nothing was running, which cannot be true"

    def test_this_test_process_is_in_it(self, guard: Guard) -> None:
        """A shape assertion alone would pass on an empty-ish table of the wrong thing."""
        capability = guard._registry.get("list_processes")  # noqa: SLF001

        table = capability.handler()

        assert str(os.getpid()) in [row[1] for row in table.rows]

    def test_it_is_bounded(self, guard: Guard) -> None:
        """A machine with 400 processes must not put 400 rows on the wire."""
        capability = guard._registry.get("list_processes")  # noqa: SLF001

        table = capability.handler()

        assert len(table.rows) <= MAX_ROWS

    def test_it_needs_no_approval(self, guard: Guard) -> None:
        """A read that touches no path has nothing to escalate: it runs and reports."""
        verdict = guard.evaluate(Invocation("list_processes", {}))

        assert isinstance(verdict, Allowed)
        assert verdict.effective_side_effect == "read"

    def test_it_declares_no_roots_because_it_takes_no_path(self, guard: Guard) -> None:
        """The invariant this capability is the reason for: roots the guard cannot consult would be
        a containment claim nothing enforces."""
        capability = guard._registry.get("list_processes")  # noqa: SLF001

        assert capability.allowed_roots == ()

    def test_an_argument_it_does_not_declare_is_refused(self, guard: Guard) -> None:
        """Step 2 still applies to a capability whose schema is nearly empty."""
        verdict = guard.evaluate(Invocation("list_processes", {"path": "C:/Windows"}))

        assert isinstance(verdict, Denied)

    def test_every_cell_is_a_string(self, guard: Guard) -> None:
        """A pid is a number on this machine and untrusted display text on the wire. Converting at
        the boundary is what keeps the UI from formatting one."""
        capability = guard._registry.get("list_processes")  # noqa: SLF001

        table = capability.handler()

        assert all(isinstance(cell, str) for row in table.rows for cell in row)


class TestOpenFolder:
    def test_it_is_contained_by_its_roots(self, guard: Guard, tmp_path: Path) -> None:
        verdict = guard.evaluate(Invocation("open_folder", {"path": str(tmp_path / "elsewhere")}))

        assert isinstance(verdict, Denied)

    def test_a_folder_in_the_workspace_reaches_approval(self, guard: Guard, workspace: Path) -> None:
        """A write inside the workspace is not escalated, but it is still a write: it stops for a
        human rather than running on the model's say-so."""
        (workspace / "notes").mkdir()

        verdict = guard.evaluate(Invocation("open_folder", {"path": str(workspace / "notes")}))

        assert isinstance(verdict, Pending)
        assert verdict.side_effect == "write"

    def test_the_resolved_path_is_what_the_user_would_see(self, guard: Guard, workspace: Path) -> None:
        """Approval shows the resolved path, not the string the model wrote - red line 4."""
        (workspace / "notes").mkdir()
        crooked = str(workspace / "notes" / ".." / "notes")

        verdict = guard.evaluate(Invocation("open_folder", {"path": crooked}))

        assert isinstance(verdict, Pending)
        assert verdict.resolved_args["path"] == str((workspace / "notes").resolve())

    def test_traversal_out_of_the_workspace_is_refused(self, guard: Guard, workspace: Path) -> None:
        verdict = guard.evaluate(
            Invocation("open_folder", {"path": str(workspace / ".." / ".." / "Windows")})
        )

        assert isinstance(verdict, Denied)


class TestTheExplorerBinaryIsNotResolvedThroughPath:
    """Found by running /threat-check on this capability, which M5 requires per capability.

    ``open_folder`` launched ``explorer.exe`` by bare name, which CreateProcess resolves by
    searching - and a directory earlier in the search order holding a file of that name is the
    classic binary-planting vector. The launched program is decided by an absolute path under the
    Windows directory instead, so what runs does not depend on PATH.
    """

    def test_the_explorer_path_is_absolute(self) -> None:
        from local_zero_brain.capabilities.handlers import _EXPLORER

        assert Path(_EXPLORER).is_absolute(), "a bare name is resolved by searching, not by us"

    def test_it_lives_under_the_windows_directory(self) -> None:
        from local_zero_brain.capabilities.handlers import _EXPLORER

        windows = Path(os.environ.get("SystemRoot", r"C:\Windows")).resolve()
        assert Path(_EXPLORER).resolve().is_relative_to(windows)

    def test_it_is_the_file_that_is_actually_there(self) -> None:
        """An absolute path to something absent would be a different bug wearing the same fix."""
        from local_zero_brain.capabilities.handlers import _EXPLORER

        assert Path(_EXPLORER).exists()
