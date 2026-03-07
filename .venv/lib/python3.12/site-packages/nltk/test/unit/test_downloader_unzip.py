import os
import sys
import zipfile
from pathlib import Path

import pytest

from nltk.downloader import ErrorMessage, _unzip_iter


def _make_zip(file_path: Path, members: dict[str, bytes]) -> None:
    """
    Create a ZIP file at file_path, with the given arcname->content mapping.
    """
    with zipfile.ZipFile(file_path, "w") as zf:
        for arcname, content in members.items():
            zf.writestr(arcname, content)


def _run_unzip_iter(zip_path: Path, extract_root: Path, verbose: bool = False):
    """
    Convenience wrapper that runs _unzip_iter and returns the list of yielded
    messages (if any).
    """
    return list(_unzip_iter(str(zip_path), str(extract_root), verbose=verbose))


class TestSecureUnzip:
    """
    Tests for secure ZIP extraction behaviour in nltk.downloader._unzip_iter.

    These tests are specifically designed so that:

    * On the old implementation (using zf.extractall(root) without checks),
      the "Zip-Slip" tests will fail because they rely on the new behaviour
      (yielding ErrorMessage for escaping entries, and NOT writing outside
      the extraction root).

    * On the new implementation, the tests pass, because the new path
      validation prevents escapes and emits the expected ErrorMessage.
    """

    def test_normal_relative_paths_are_extracted(self, tmp_path: Path) -> None:
        """
        A ZIP with only safe, relative paths should fully extract under the
        given root, and should not yield any ErrorMessage.
        """
        zip_path = tmp_path / "safe.zip"
        extract_root = tmp_path / "extract"

        members = {
            "pkg/file.txt": b"hello",
            "pkg/subdir/other.txt": b"world",
        }
        _make_zip(zip_path, members)

        messages = _run_unzip_iter(zip_path, extract_root, verbose=False)

        # No ErrorMessage should be yielded for valid archives.
        assert not any(isinstance(m, ErrorMessage) for m in messages)

        assert (extract_root / "pkg" / "file.txt").read_bytes() == b"hello"
        assert (extract_root / "pkg" / "subdir" / "other.txt").read_bytes() == b"world"

    def test_zip_slip_with_parent_directory_component_is_blocked(
        self, tmp_path: Path
    ) -> None:
        """
        An entry containing ``..`` that would escape the target directory
        must not be written outside the extraction root, and must cause
        _unzip_iter to yield an ErrorMessage.

        On the old implementation (extractall(root)), this test will fail
        because the file outside the root is actually created and no
        ErrorMessage is yielded.
        """
        zip_path = tmp_path / "zip_slip_parent.zip"
        extract_root = tmp_path / "extract"

        # This is the path that would be written if "../outside.txt" is
        # extracted with extractall(root).
        outside_target = (extract_root / ".." / "outside.txt").resolve()

        members = {
            "pkg/good.txt": b"ok",
            # This would escape extract_root if not validated.
            "../outside.txt": b"evil",
        }
        _make_zip(zip_path, members)

        messages = _run_unzip_iter(zip_path, extract_root, verbose=False)

        # With the secure implementation, the escaping entry must be blocked
        # and reported as an ErrorMessage.
        err_msgs = [m for m in messages if isinstance(m, ErrorMessage)]
        assert (
            err_msgs
        ), "Expected an ErrorMessage for a Zip-Slip parent-directory attempt"

        # Check that the message text indicates it was blocked. Adjust to the
        # exact wording if needed.
        combined_messages = " ".join(str(m.message) for m in err_msgs)
        assert "Zip Slip" in combined_messages and "blocked" in combined_messages

        # The escaping path must not have been written.
        assert not outside_target.exists()

        # The safe entry should still be extracted under the root.
        assert (extract_root / "pkg" / "good.txt").read_bytes() == b"ok"

    @pytest.mark.skipif(
        sys.platform.startswith("win"),
        reason="Absolute POSIX paths are not meaningful on Windows",
    )
    def test_zip_slip_with_absolute_posix_path_is_blocked(self, tmp_path: Path) -> None:
        """
        An entry with an absolute POSIX path (e.g. ``/tmp/evil``) must not be
        extracted as-is; it should not overwrite arbitrary filesystem paths,
        and should result in an ErrorMessage.

        On the old implementation (extractall(root)), this test will fail
        because the absolute path gets created without any ErrorMessage.
        """
        zip_path = tmp_path / "zip_slip_abs_posix.zip"
        extract_root = tmp_path / "extract"

        # Choose a unique location in /tmp to avoid interfering with real files.
        absolute_target = Path("/tmp") / f"nltk_zip_slip_test_{os.getpid()}"

        try:
            members = {
                "pkg/good.txt": b"ok",
                # This absolute path should never be created by the secure
                # implementation.
                str(absolute_target): b"evil",
            }
            _make_zip(zip_path, members)

            messages = _run_unzip_iter(zip_path, extract_root, verbose=False)

            err_msgs = [m for m in messages if isinstance(m, ErrorMessage)]
            assert (
                err_msgs
            ), "Expected an ErrorMessage for absolute-path Zip-Slip attempt"

            combined_messages = " ".join(str(m.message) for m in err_msgs)
            assert "Zip Slip" in combined_messages and "blocked" in combined_messages

            # Absolute path must not be created by the secure implementation.
            assert not absolute_target.exists()

            # Safe entry must still be extracted under extract_root.
            assert (extract_root / "pkg" / "good.txt").read_bytes() == b"ok"
        finally:
            # Best-effort cleanup if the implementation under test behaves
            # incorrectly and creates the file.
            if absolute_target.exists():
                try:
                    absolute_target.unlink()
                except OSError:
                    # Ignore cleanup errors: file may not exist or be locked.
                    pass

    def test_entries_resolved_outside_root_are_blocked_via_symlink(
        self, tmp_path: Path
    ) -> None:
        """
        If there is a pre-existing symlink below the extraction root that
        points outside the root, writing through that symlink should not
        be allowed to escape the root.

        This test documents the desired hardening to defend against this
        class of attacks.
        """
        if not hasattr(os, "symlink"):
            pytest.skip("Symlinks not supported on this platform")

        zip_path = tmp_path / "zip_slip_symlink.zip"
        extract_root = tmp_path / "extract"
        outside_dir = tmp_path / "outside_dir"
        outside_dir.mkdir()
        outside_target = outside_dir / "evil.txt"

        members = {
            "pkg/good.txt": b"ok",
            "dir_link/evil.txt": b"evil",
        }
        _make_zip(zip_path, members)

        extract_root.mkdir()
        # Pre-existing symlink inside extract_root pointing *outside* it.
        os.symlink(outside_dir, extract_root / "dir_link")

        messages = _run_unzip_iter(zip_path, extract_root, verbose=False)

        # Desired property:
        assert not outside_target.exists()
        assert (extract_root / "pkg" / "good.txt").read_bytes() == b"ok"
        # Should yield an ErrorMessage for the blocked symlink escape
        assert any(isinstance(m, ErrorMessage) for m in messages)

    def test_bad_zipfile_yields_errormessage(self, tmp_path: Path) -> None:
        """
        A corrupt or non-zip file should cause _unzip_iter to yield an
        ErrorMessage instead of raising an unhandled exception.
        """
        zip_path = tmp_path / "not_a_zip.txt"
        zip_path.write_bytes(b"this is not a zip archive")
        extract_root = tmp_path / "extract"

        messages = _run_unzip_iter(zip_path, extract_root, verbose=False)

        assert any(isinstance(m, ErrorMessage) for m in messages)

        # If the implementation chooses to create the root directory at all,
        # it should not leave partially extracted content.
        if extract_root.exists():
            assert not any(extract_root.iterdir())

    def test_unzip_iter_verbose_writes_to_stdout(self, capsys, tmp_path: Path) -> None:
        """
        When verbose=True, _unzip_iter should write a status line to stdout.
        This checks that existing user-visible behaviour is preserved.
        """
        zip_path = tmp_path / "verbose.zip"
        extract_root = tmp_path / "extract"

        members = {"pkg/file.txt": b"data"}
        _make_zip(zip_path, members)

        _run_unzip_iter(zip_path, extract_root, verbose=True)
        captured = capsys.readouterr()
        assert "Unzipping" in captured.out
