"""Where Steam keeps things, read from Steam's own files.

Two capabilities need this and neither may guess: ``launch_application`` uses the libraries as
containment roots, because games do not live under Program Files, and ``scan_games`` reads the
installed titles out of them.

**Parsed by hand rather than with a VDF library.** The same reasoning as ``memory/vault.py``'s
frontmatter parser: what is needed here is one key out of a flat block, and a dependency whose full
grammar can construct objects is not earned for that. Anything this parser does not understand is
skipped.

**Everything fails closed.** No Steam, no registry key, an unreadable or malformed
``libraryfolders.vdf`` - each returns nothing rather than a guess. A root that does not exist would
silently change what a capability may run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: Where Steam records its own install directory. Under HKCU, so no elevation is needed to read it.
_REGISTRY_KEY = r"Software\Valve\Steam"
_REGISTRY_VALUE = "SteamPath"

#: `"path"   "D:\\steam"` - the key must be exactly `path`, so the word appearing inside some other
#: value is not mistaken for one.
_PATH_ENTRY = re.compile(r'^\s*"path"\s+"([^"]*)"\s*$', re.IGNORECASE)

#: `"name"		"Counter-Strike 2"` - a quoted key and a quoted value on one line, which is all
#: this format is at the level we read it.
_KEY_VALUE = re.compile(r'^\s*"([^"]+)"\s+"([^"]*)"\s*$')

_LIBRARY_FILE = Path("steamapps") / "libraryfolders.vdf"


@dataclass(frozen=True, slots=True)
class InstalledApp:
    """One entry Steam records as installed.

    **Everything here is untrusted text.** A title is whatever the publisher put in the store, and
    an installdir is a folder name; both reach the user as display cells and neither is ever routed
    back into the planner.

    Frozen, because a scan is a snapshot of what was on disk when it ran.
    """

    app_id: str
    name: str
    install_dir: str
    #: None when Steam recorded something that is not a number. Not zero - zero is the claim that a
    #: game takes no space, and a gap is the honest rendering of "Steam did not say".
    size_bytes: int | None


def read_manifest(path: Path) -> InstalledApp | None:
    """One ``appmanifest_*.acf``, or None when it cannot be read as one.

    **Only ``AppState``'s own keys are read.** The file nests: ``InstalledDepots`` carries a ``size``
    per depot, and a depot block can carry its own ``name``. A parser matching a key anywhere in the
    file would report a depot's name as the game's, so brace depth is tracked and anything below the
    top level is skipped.

    A manifest with no name is skipped rather than shown as a blank row - Steam writes these for
    things mid-install, and a nameless entry in a game list is a row the user cannot act on.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    fields: dict[str, str] = {}
    depth = 0

    for line in text.splitlines():
        stripped = line.strip()

        if stripped == "{":
            depth += 1
            continue
        if stripped == "}":
            depth -= 1
            continue

        # Depth 1 is inside AppState and nowhere deeper.
        if depth != 1:
            continue

        match = _KEY_VALUE.match(line)
        if match is not None:
            fields[match.group(1).lower()] = match.group(2)

    name = fields.get("name")
    app_id = fields.get("appid")
    if not name or not app_id:
        return None

    raw_size = fields.get("sizeondisk", "")
    return InstalledApp(
        app_id=app_id,
        name=name,
        install_dir=fields.get("installdir", ""),
        size_bytes=int(raw_size) if raw_size.isdigit() else None,
    )


def installed_apps() -> tuple[InstalledApp, ...]:
    """Everything Steam records as installed, across every library.

    **Read-only, structurally.** This opens files for reading and there is no write path in it,
    which is what the M5 exit criterion asks for rather than promises.

    What Steam records includes tools and redistributables, not only games, and they are reported
    as they are found. Filtering by a hardcoded list of app ids would be a classifier that rots the
    first time Valve ships something new, and inventing a games-versus-tools judgement is not this
    function's to make.
    """
    apps: list[InstalledApp] = []

    for library in library_paths():
        try:
            manifests = sorted((library / "steamapps").glob("appmanifest_*.acf"))
        except OSError:
            # A library on a drive that went away between discovery and here.
            continue

        apps.extend(app for manifest in manifests if (app := read_manifest(manifest)) is not None)

    return tuple(apps)


def install_root() -> Path | None:
    """Steam's install directory, or None when it is not installed.

    Read from the registry rather than assumed to be under Program Files: this machine has it on
    ``d:/steam``, which is ordinary - Steam is installed wherever there was room.
    """
    try:
        import winreg
    except ImportError:
        # Not Windows. Nothing else in this product runs there either, but a module that raises on
        # import would take the whole registry down with it.
        return None

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REGISTRY_KEY) as key:
            value, _ = winreg.QueryValueEx(key, _REGISTRY_VALUE)
    except OSError:
        # No key, no value, or no permission. Steam is not installed as far as this is concerned.
        return None

    if not isinstance(value, str) or not value:
        return None

    root = Path(value)
    return root if root.is_dir() else None


def library_paths_in(vdf: Path) -> tuple[Path, ...]:
    """Every library directory named in one ``libraryfolders.vdf``.

    Split out from :func:`library_paths` so the parser can be tested against a file rather than
    against whatever this machine happens to have installed.
    """
    try:
        text = vdf.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ()

    found: list[Path] = []
    for line in text.splitlines():
        match = _PATH_ENTRY.match(line)
        if match is None:
            continue

        raw = match.group(1)
        if not raw:
            continue

        # VDF escapes backslashes. Unescaping is the whole of what this format needs from us.
        found.append(Path(raw.replace("\\\\", "\\")))

    return tuple(found)


def library_paths() -> tuple[Path, ...]:
    """Every Steam library on this machine that actually exists.

    The install root is included even when the vdf does not name it: it is a library, and older
    layouts leave it implicit. Duplicates are collapsed, order preserved, and a directory that is
    listed but absent - an unplugged drive - is dropped rather than handed to a capability as a
    root it can never reach.
    """
    root = install_root()
    if root is None:
        return ()

    candidates = [root, *library_paths_in(root / _LIBRARY_FILE)]

    seen: set[Path] = set()
    libraries: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen or not resolved.is_dir():
            continue

        seen.add(resolved)
        libraries.append(resolved)

    return tuple(libraries)
