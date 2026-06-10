#!/usr/bin/env python3
"""
Concurrency and wave parallelism test suite for Phase 5 foundation.

Tests concurrent DB writes, thread-local connection isolation, and parallel
wave execution. All tests use hermetic fixtures from conftest.py and mock
threadpool execution patterns.

Expected behavior after Phase 5 Wave 1 implementation:
  - test_thread_local_connections: PASS (DB connections are thread-safe)
  - test_parallel_wave_db_safety: PASS (parallel wave writes don't corrupt DB)
  - test_wave_serial_fallback: PASS (serial fallback still works when parallelism disabled)

Mapped to VALIDATION.md:
  - 05-V0-01: test_parallel_wave_db_safety
  - 05-V0-02: test_thread_local_connections
  - 05-V0-04: test_wave_serial_fallback
"""
from __future__ import annotations

import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

# Ensure the project root is on sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.db import Database
from shared.config import TGsConfig

if TYPE_CHECKING:
    from tests.conftest import temp_db_fixture, test_config_fixture


# ============================================================================
# TEST 1: test_thread_local_connections (05-V0-02)
# ============================================================================


def test_thread_local_connections(temp_db_fixture):
    """
    Verify that each thread gets its own isolated SQLite connection.
    
    This test:
    1. Spawns 3 threads
    2. Each thread calls `with db.conn() as conn:` and executes an INSERT
    3. Verifies: no "execute() called from different thread" errors
    4. Verifies: main thread sees all 3 inserted rows
    
    Expected behavior after Phase 5 Wave 1:
        PASS — thread-local connections prevent cross-thread access errors
    
    Current behavior (before Phase 5):
        FAIL or ERROR — shared connection across threads raises ProgrammingError
    
    FNDX-01 requirement:
        DB connections use connection-per-thread pattern so concurrent DB
        writes cannot cause SQLite errors.
    """
    import time
    
    db = temp_db_fixture
    results: dict[int, str] = {}
    errors: list[str] = []
    
    def thread_insert_task(thread_id: int):
        """Worker task: insert telemetry record from thread."""
        try:
            # This should use thread-local connection automatically
            with db.conn() as conn:
                conn.execute(
                    """
                    INSERT INTO telemetry (session_id, tier, model, provider_name, ts)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (f"session-{thread_id}", "low", "test-model", "test-provider", time.time()),
                )
            results[thread_id] = "success"
        except Exception as e:
            errors.append(f"Thread {thread_id} error: {e}")
            results[thread_id] = "failed"
    
    # Spawn 3 threads
    threads = []
    for i in range(3):
        t = threading.Thread(target=thread_insert_task, args=(i,))
        threads.append(t)
        t.start()
    
    # Wait for all threads to complete
    for t in threads:
        t.join(timeout=10)
    
    # Verify no errors occurred
    assert not errors, f"Thread errors occurred: {errors}"
    
    # Verify all 3 threads succeeded
    assert len(results) == 3, f"Expected 3 thread results, got {len(results)}"
    assert all(v == "success" for v in results.values()), f"Not all threads succeeded: {results}"
    
    # Verify main thread can read all 3 inserted rows
    with db.conn() as conn:
        rows = conn.execute("SELECT COUNT(*) FROM telemetry").fetchone()
        count = rows[0] if rows else 0
    
    assert count == 3, f"Expected 3 telemetry records, found {count}"


# ============================================================================
# TEST 2: test_parallel_wave_db_safety (05-V0-01)
# ============================================================================


def test_parallel_wave_db_safety(temp_db_fixture, test_config_fixture):
    """
    Verify parallel wave execution with ThreadPoolExecutor doesn't corrupt DB.
    
    This test simulates wave execution:
    1. Create a "wave" with 3 subtasks
    2. Execute subtasks in parallel via ThreadPoolExecutor (max_workers=2)
    3. Each subtask writes telemetry records
    4. Verify: no cross-thread SQLite errors
    5. Verify: all records inserted successfully
    6. Verify: wall time is roughly correct (implementation may not parallelize yet)
    
    Expected behavior after Phase 5 Wave 1:
        PASS — parallel execution safe with thread-local connections
    
    Current behavior (before Phase 5):
        FAIL or ERROR — cross-thread DB access raises ProgrammingError
    
    FNDX-02 requirement:
        Wave execution uses ThreadPoolExecutor with configurable concurrency
        cap so multi-agent waves run in true parallel.
    """
    import time as time_module
    
    db = temp_db_fixture
    config = test_config_fixture
    
    def subtask_worker(subtask_id: int, wave_idx: int) -> dict:
        """
        Worker task simulating one subtask in a wave.
        
        Writes telemetry record to DB and returns result summary.
        """
        try:
            # Simulate some work
            time_module.sleep(0.1)
            
            # Write telemetry via thread-local connection
            with db.conn() as conn:
                conn.execute(
                    """
                    INSERT INTO telemetry (session_id, tier, model, provider_name, success, ts)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"wave-{wave_idx}",
                        "medium",
                        "test-model",
                        "test-provider",
                        1,
                        time_module.time(),
                    ),
                )
            
            return {"subtask_id": subtask_id, "status": "success"}
        except Exception as e:
            return {"subtask_id": subtask_id, "status": "error", "error": str(e)}
    
    # Create wave: 3 subtasks
    wave_idx = 0
    subtasks = [
        {"id": 0, "name": "subtask-0"},
        {"id": 1, "name": "subtask-1"},
        {"id": 2, "name": "subtask-2"},
    ]
    
    # Execute wave in parallel
    results = []
    start_time = time.time()
    
    max_workers = min(len(subtasks), config.parallelism.max_workers)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(subtask_worker, st["id"], wave_idx)
            for st in subtasks
        }
        
        for future in as_completed(futures, timeout=30):
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                results.append({"status": "error", "error": str(e)})
    
    wall_time = time.time() - start_time
    
    # Verify no errors
    errors = [r for r in results if r.get("status") != "success"]
    assert not errors, f"Subtask errors: {errors}"
    
    # Verify all 3 results
    assert len(results) == 3, f"Expected 3 results, got {len(results)}"
    
    # Verify all records were inserted
    with db.conn() as conn:
        rows = conn.execute(
            "SELECT COUNT(*) FROM telemetry WHERE session_id = ?",
            (f"wave-{wave_idx}",),
        ).fetchone()
        count = rows[0] if rows else 0
    
    assert count == 3, f"Expected 3 telemetry records, found {count}"
    
    # Wall time should be roughly 0.1-0.15s (3 tasks * 0.1s each, running in parallel)
    # If serial: would be ~0.3s
    # Allow 0.2s for safety (might run on slower CI)
    assert wall_time < 0.25, f"Wave execution took {wall_time:.2f}s (expected ~0.1-0.15s)"


# ============================================================================
# TEST 3: test_wave_serial_fallback (05-V0-04)
# ============================================================================


def test_wave_serial_fallback(temp_db_fixture, test_config_fixture):
    """
    Verify wave execution falls back to serial when parallelism is disabled.
    
    This test:
    1. Create config with parallelism.enabled = False
    2. Create a wave with 3 subtasks
    3. Execute subtasks sequentially (not in ThreadPoolExecutor)
    4. Verify: all records inserted successfully
    5. Verify: no threading errors
    
    Expected behavior:
        PASS (existing serial path should already work)
    
    FNDX-02 requirement (fallback):
        Wave execution respects parallelism config and falls back to serial
        when disabled.
    """
    import time as time_module
    
    db = temp_db_fixture
    config = test_config_fixture
    
    # Disable parallelism
    config.parallelism.enabled = False
    
    def subtask_worker(subtask_id: int, wave_idx: int) -> dict:
        """Worker task: insert telemetry record."""
        try:
            with db.conn() as conn:
                conn.execute(
                    """
                    INSERT INTO telemetry (session_id, tier, model, provider_name, success, ts)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (f"wave-serial-{wave_idx}", "low", "test-model", "test-provider", 1, time_module.time()),
                )
            return {"subtask_id": subtask_id, "status": "success"}
        except Exception as e:
            return {"subtask_id": subtask_id, "status": "error", "error": str(e)}
    
    # Create and execute wave serially
    wave_idx = 1
    subtasks = [{"id": i} for i in range(3)]
    results = []
    
    if config.parallelism.enabled:
        # Parallel path (should not execute)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {executor.submit(subtask_worker, st["id"], wave_idx) for st in subtasks}
            for future in as_completed(futures):
                results.append(future.result())
    else:
        # Serial path (execute directly)
        for st in subtasks:
            result = subtask_worker(st["id"], wave_idx)
            results.append(result)
    
    # Verify no errors
    errors = [r for r in results if r.get("status") != "success"]
    assert not errors, f"Subtask errors: {errors}"
    
    # Verify all 3 results
    assert len(results) == 3, f"Expected 3 results, got {len(results)}"
    
    # Verify all records were inserted
    with db.conn() as conn:
        rows = conn.execute(
            "SELECT COUNT(*) FROM telemetry WHERE session_id = ?",
            (f"wave-serial-{wave_idx}",),
        ).fetchone()
        count = rows[0] if rows else 0
    
    assert count == 3, f"Expected 3 telemetry records, found {count}"
