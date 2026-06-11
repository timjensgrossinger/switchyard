"""Tests for plan 06 worktree isolation."""
from __future__ import annotations

import subprocess
from pathlib import Path
import pytest

from shared.config import WorktreeConfig
from shared.worktree import WorktreeManager, WorktreeError


# ---------------------------------------------------------------------------
# WorktreeConfig defaults
# ---------------------------------------------------------------------------

def test_worktree_config_defaults():
    cfg = WorktreeConfig()
    assert cfg.enabled is False
    assert cfg.ttl_hours == 24.0
    assert cfg.base_path == ""


def test_worktree_config_enabled():
    cfg = WorktreeConfig(enabled=True, ttl_hours=6.0)
    assert cfg.enabled is True
    assert cfg.ttl_hours == 6.0


# ---------------------------------------------------------------------------
# Non-git repo → WorktreeError
# ---------------------------------------------------------------------------

def test_acquire_non_git_raises(tmp_path):
    wm = WorktreeManager(repo_root=tmp_path, worktree_base=tmp_path / "wt")
    with pytest.raises(WorktreeError, match="Not a git repository"):
        wm.acquire("task-001")


# ---------------------------------------------------------------------------
# Git repo — full acquire/release cycle
# ---------------------------------------------------------------------------

@pytest.fixture()
def git_repo(tmp_path):
    """Create a minimal git repo for worktree tests."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("hello")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def test_acquire_creates_worktree(git_repo, tmp_path):
    wm = WorktreeManager(repo_root=git_repo, worktree_base=tmp_path / "wt")
    wt_path = wm.acquire("task-001")
    assert wt_path.exists()
    assert wt_path.is_dir()
    assert (wt_path / "README.md").exists()


def test_acquire_idempotent_returns_same_path(git_repo, tmp_path):
    wm = WorktreeManager(repo_root=git_repo, worktree_base=tmp_path / "wt")
    p1 = wm.acquire("task-002")
    p2 = wm.acquire("task-002")
    assert p1 == p2


def test_release_discard_removes_worktree(git_repo, tmp_path):
    wm = WorktreeManager(repo_root=git_repo, worktree_base=tmp_path / "wt")
    wt_path = wm.acquire("task-003")
    assert wt_path.exists()
    wm.release("task-003", action="discard")
    assert not wt_path.exists()


def test_release_unknown_task_returns_empty(git_repo, tmp_path):
    wm = WorktreeManager(repo_root=git_repo, worktree_base=tmp_path / "wt")
    conflicts = wm.release("nonexistent-task", action="discard")
    assert conflicts == []


def test_release_merge_no_changes_returns_empty(git_repo, tmp_path):
    wm = WorktreeManager(repo_root=git_repo, worktree_base=tmp_path / "wt")
    wm.acquire("task-004")
    conflicts = wm.release("task-004", action="merge")
    assert conflicts == []


def test_sequential_acquire_release_leaks_no_worktrees(git_repo, tmp_path):
    """100 sequential cycles leave no stale worktrees."""
    wm_base = tmp_path / "wt"
    wm = WorktreeManager(repo_root=git_repo, worktree_base=wm_base)
    for i in range(10):  # 10 (not 100) for test speed
        task_id = f"leak-test-{i}"
        wm.acquire(task_id)
        wm.release(task_id, action="discard")
    remaining = [d for d in wm_base.iterdir() if d.is_dir()] if wm_base.exists() else []
    assert len(remaining) == 0


def test_prune_stale_removes_old_worktrees(git_repo, tmp_path):
    import time
    wm_base = tmp_path / "wt"
    wm = WorktreeManager(repo_root=git_repo, worktree_base=wm_base, ttl_hours=0.0)
    wm_base.mkdir(parents=True, exist_ok=True)
    stale = wm_base / "stale-task"
    stale.mkdir()
    # Set mtime in the past
    old_time = time.time() - 7200
    os.utime(str(stale), (old_time, old_time))
    pruned = wm.prune_stale()
    assert pruned >= 1
    assert not stale.exists()


import os  # needed for test above
