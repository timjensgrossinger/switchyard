import json
import time
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from shared.catalog_refresh import CatalogRefresher, _tier_from_cost
from shared.db import Database


@pytest.fixture
def temp_db():
    """Isolated temporary database instance."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(db_path=Path(tmpdir) / "test.db")
        yield db


def test_get_tier_returns_none_on_empty_db(temp_db):
    """get_tier_for_model returns None when model_catalog has no litellm_refresh rows."""
    cr = CatalogRefresher()
    result = cr.get_tier_for_model("gpt-5-mini", temp_db)
    assert result is None


def test_do_refresh_upserts_rows(temp_db):
    """_do_refresh() writes model rows with correct tier and url_source to model_catalog."""
    sample_data = {
        "gpt-5-mini": {"input_cost_per_token": 0.00000015},
        "gpt-4": {"input_cost_per_token": 0.00003},
        "gpt-4o": {"input_cost_per_token": 0.000005},
    }
    fake_response = json.dumps(sample_data).encode()

    mock_resp = MagicMock()
    mock_resp.read.return_value = fake_response
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    cr = CatalogRefresher()
    with patch("urllib.request.urlopen", return_value=mock_resp):
        cr._do_refresh(temp_db)

    assert cr.get_tier_for_model("gpt-5-mini", temp_db) is None
    assert cr.get_tier_for_model("gpt-4", temp_db) is None

    with temp_db.conn() as conn:
        row = conn.execute(
            "SELECT url_source FROM model_catalog WHERE model_id = ? AND source = ?",
            ("gpt-5-mini", CatalogRefresher.SOURCE_NAME),
        ).fetchone()
    assert row is not None
    assert row[0] == CatalogRefresher.LITELLM_URL


def test_refresh_if_stale_skips_when_fresh(temp_db):
    """refresh_if_stale() does not spawn a thread when cache is still valid."""
    now = int(time.time())
    future = now + 60 * 60
    with temp_db.conn() as conn:
        conn.execute(
            "INSERT INTO model_catalog (model_id, provider, tier, cost, last_seen, source, stale_until) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("sentinel", CatalogRefresher.PROVIDER_NAME, "low", 0.0, now, CatalogRefresher.SOURCE_NAME, future),
        )

    cr = CatalogRefresher()
    with patch("threading.Thread") as mock_thread:
        cr.refresh_if_stale(temp_db)
        mock_thread.assert_not_called()


def test_refresh_if_stale_triggers_when_no_cache(temp_db):
    """refresh_if_stale() spawns a daemon thread when no litellm_refresh rows exist."""
    cr = CatalogRefresher()
    spawned = []

    original_thread = __import__("threading").Thread

    def capture_thread(*args, **kwargs):
        t = original_thread(*args, **kwargs)
        spawned.append(t)
        return t

    with patch("threading.Thread", side_effect=capture_thread):
        cr.refresh_if_stale(temp_db)

    assert len(spawned) == 1
    assert spawned[0].daemon is True


def test_tier_from_cost_thresholds():
    """Global price data is enrichment only, never a routing classifier."""
    assert _tier_from_cost(0.0000005) == "unknown"
    assert _tier_from_cost(0.000005) == "unknown"
    assert _tier_from_cost(0.00001) == "unknown"
