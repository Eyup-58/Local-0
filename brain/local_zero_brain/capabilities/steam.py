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
from pathlib import Path

#: Where Steam records its own install directory. Under HKCU, so no elevation is needed to read it.
_REGISTRY_KEY = r"Software\Valve\Steam"
_REGISTRY_VALUE = "SteamPath"

#: `"path"   "D:\\steam"` - the key must be exactly `path`, so the word appearing inside some other
#: value is not mistaken for one.
_PATH_ENTRY = re.compile(r'^\s*"path"\s+"([^"]*)"\s*$', re.IGNORECASE)

_LIBRARY_FILE = Path("steamapps") / "libraryfolders.vdf"


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
