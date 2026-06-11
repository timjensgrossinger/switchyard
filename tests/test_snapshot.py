from __future__ import annotations

import subprocess
from pathlib import Path

from shared.snapshot import FileSnapshot


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def test_file_snapshot_detects_modified_created_and_deleted(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")

    tracked = repo / "tracked.py"
    deleted = repo / "deleted.py"
    tracked.write_text("print('before')\n", encoding="utf-8")
    deleted.write_text("print('gone soon')\n", encoding="utf-8")
    _git(repo, "add", "tracked.py", "deleted.py")

    snapshot = FileSnapshot(str(repo))
    snapshot.take()

    tracked.write_text("print('after')\n", encoding="utf-8")
    deleted.unlink()
    created = repo / "created.py"
    created.write_text("print('new')\n", encoding="utf-8")

    diffs = {Path(diff.path).name: diff for diff in snapshot.diff_since()}

    assert diffs["tracked.py"].change_type == "modified"
    assert diffs["created.py"].change_type == "created"
    assert diffs["deleted.py"].change_type == "deleted"
    assert "+print('after')" in diffs["tracked.py"].diff
    assert "+print('new')" in diffs["created.py"].diff
    assert "-print('gone soon')" in diffs["deleted.py"].diff


def test_file_snapshot_ignores_preexisting_untracked_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")

    tracked = repo / "tracked.py"
    tracked.write_text("print('tracked')\n", encoding="utf-8")
    _git(repo, "add", "tracked.py")

    existing_untracked = repo / "existing.py"
    existing_untracked.write_text("print('already here')\n", encoding="utf-8")

    snapshot = FileSnapshot(str(repo))
    snapshot.take()

    diffs = {Path(diff.path).name: diff for diff in snapshot.diff_since()}

    assert "existing.py" not in diffs


def test_file_snapshot_detects_preexisting_untracked_file_changes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")

    tracked = repo / "tracked.py"
    tracked.write_text("print('tracked')\n", encoding="utf-8")
    _git(repo, "add", "tracked.py")

    existing_untracked = repo / "existing.py"
    existing_untracked.write_text("print('before')\n", encoding="utf-8")

    snapshot = FileSnapshot(str(repo))
    snapshot.take()

    existing_untracked.write_text("print('after')\n", encoding="utf-8")
    diffs = {Path(diff.path).name: diff for diff in snapshot.diff_since()}

    assert diffs["existing.py"].change_type == "modified"


def test_file_snapshot_skips_symlink_targets_outside_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")

    tracked = repo / "tracked.py"
    tracked.write_text("print('tracked')\n", encoding="utf-8")
    _git(repo, "add", "tracked.py")

    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")
    link = repo / "leak.py"
    link.symlink_to(outside)

    snapshot = FileSnapshot(str(repo))
    snapshot.take()

    diffs = snapshot.diff_since(target_file=str(link))

    assert diffs == []
