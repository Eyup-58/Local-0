"""Finding Steam's libraries, which two things need and neither may guess at.

``launch_application`` needs them as containment roots - games do not live under Program Files -
and ``scan_games`` needs them to read from. Both are wrong in a specific way if this module invents
a path: a root that does not exist silently widens or narrows what may run.

Everything here fails closed. No Steam, no registry key, an unreadable or malformed
``libraryfolders.vdf``: each returns nothing rather than a guess, and the capabilities that depend
on it simply have one less place they may touch.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from local_zero_brain.capabilities import steam


class TestReadingLibraryFolders:
    def test_it_reads_the_paths_out_of_a_real_vdf(self, tmp_path: Path) -> None:
        vdf = tmp_path / "libraryfolders.vdf"
        vdf.write_text(
            '"libraryfolders"\n'
            "{\n"
            '\t"0"\n'
            "\t{\n"
            '\t\t"path"\t\t"D:\\\\steam"\n'
            '\t\t"label"\t\t""\n'
            "\t}\n"
            '\t"1"\n'
            "\t{\n"
            '\t\t"path"\t\t"E:\\\\SteamLibrary"\n'
            "\t}\n"
            "}\n",
            encoding="utf-8",
        )

        found = steam.library_paths_in(vdf)

        assert [str(path) for path in found] == [r"D:\steam", r"E:\SteamLibrary"]

    def test_a_missing_file_yields_nothing(self, tmp_path: Path) -> None:
        assert steam.library_paths_in(tmp_path / "absent.vdf") == ()

    def test_a_malformed_file_yields_nothing_rather_than_raising(self, tmp_path: Path) -> None:
        """A vault-style hand parser meets a file it does not understand. The honest answer is no
        libraries, not a crash in a capability's containment roots."""
        vdf = tmp_path / "libraryfolders.vdf"
        vdf.write_text("this is not a vdf at all {{{{", encoding="utf-8")

        assert steam.library_paths_in(vdf) == ()

    def test_a_path_key_inside_a_string_value_is_not_mistaken_for_one(self, tmp_path: Path) -> None:
        """The parser matches a `path` key, not the word appearing anywhere."""
        vdf = tmp_path / "libraryfolders.vdf"
        vdf.write_text('"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"label"\t\t"my path is here"\n\t}\n}\n', encoding="utf-8")

        assert steam.library_paths_in(vdf) == ()


class TestOnThisMachine:
    """Steam is installed here, at d:/steam. These run against it rather than a fixture."""

    def test_the_install_root_is_found_or_absent(self) -> None:
        root = steam.install_root()

        if root is None:
            pytest.skip("Steam is not installed on this machine")

        assert root.is_dir()

    def test_the_libraries_include_the_install_root(self) -> None:
        if steam.install_root() is None:
            pytest.skip("Steam is not installed on this machine")

        libraries = steam.library_paths()

        assert libraries, "Steam is installed but no library was found"
        assert all(path.is_dir() for path in libraries)


APPMANIFEST = """"AppState"
{
\t"appid"\t\t"730"
\t"name"\t\t"Counter-Strike 2"
\t"installdir"\t\t"Counter-Strike Global Offensive"
\t"SizeOnDisk"\t\t"38654705664"
\t"InstalledDepots"
\t{
\t\t"731"
\t\t{
\t\t\t"manifest"\t\t"664520166269"
\t\t\t"size"\t\t"29212173"
\t\t\t"name"\t\t"a depot pretending to be the game"
\t\t}
\t}
}
"""


class TestReadingAnAppManifest:
    def test_it_reads_the_fields_that_matter(self, tmp_path: Path) -> None:
        manifest = tmp_path / "appmanifest_730.acf"
        manifest.write_text(APPMANIFEST, encoding="utf-8")

        app = steam.read_manifest(manifest)

        assert app is not None
        assert app.app_id == "730"
        assert app.name == "Counter-Strike 2"
        assert app.install_dir == "Counter-Strike Global Offensive"
        assert app.size_bytes == 38654705664

    def test_a_nested_key_does_not_shadow_the_top_level_one(self, tmp_path: Path) -> None:
        """`InstalledDepots` carries its own `size`, and a depot here carries its own `name`.

        A parser that matched a key anywhere in the file would report the depot's name as the
        game's. Depth is tracked so only AppState's own keys are read.
        """
        manifest = tmp_path / "appmanifest_730.acf"
        manifest.write_text(APPMANIFEST, encoding="utf-8")

        app = steam.read_manifest(manifest)

        assert app is not None
        assert app.name == "Counter-Strike 2"
        assert app.size_bytes == 38654705664

    def test_a_manifest_missing_a_name_is_skipped(self, tmp_path: Path) -> None:
        manifest = tmp_path / "appmanifest_1.acf"
        manifest.write_text('"AppState"\n{\n\t"appid"\t\t"1"\n}\n', encoding="utf-8")

        assert steam.read_manifest(manifest) is None

    def test_an_unreadable_manifest_is_skipped_rather_than_fatal(self, tmp_path: Path) -> None:
        assert steam.read_manifest(tmp_path / "absent.acf") is None

    def test_a_non_numeric_size_reads_as_unknown_rather_than_zero(self, tmp_path: Path) -> None:
        """Zero would render as a game taking no space, which is a claim. None is a gap."""
        manifest = tmp_path / "appmanifest_2.acf"
        manifest.write_text(
            '"AppState"\n{\n\t"appid"\t\t"2"\n\t"name"\t\t"Thing"\n\t"SizeOnDisk"\t\t"lots"\n}\n',
            encoding="utf-8",
        )

        app = steam.read_manifest(manifest)

        assert app is not None
        assert app.size_bytes is None


class TestScanningThisMachine:
    def test_it_finds_what_is_actually_installed(self) -> None:
        if not steam.library_paths():
            pytest.skip("Steam is not installed on this machine")

        installed = steam.installed_apps()

        assert installed, "Steam is installed with app manifests present but nothing was found"
        assert all(app.name for app in installed)
        assert all(app.app_id for app in installed)
