"""
Pytest suite: validates all tests/eval/*.json fixtures against the fixture schema.

Uses shared/routing_eval._validate_fixture() — no external dependencies.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import shared.routing_eval as routing_eval
from shared.routing_eval import _validate_fixture, load_fixtures, EVAL_DIR, SCHEMA_PATH


# ---------------------------------------------------------------------------
# Schema file
# ---------------------------------------------------------------------------

def test_schema_json_exists():
    assert SCHEMA_PATH.exists(), f"schema.json not found at {SCHEMA_PATH}"


def test_schema_json_is_valid_json():
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        data = json.load(f)
    assert "$schema" in data
    assert "properties" in data


def test_schema_fanout_enum_matches_runtime_validator():
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        data = json.load(f)
    fanout_enum = data["properties"]["expected"]["properties"]["fanout_expected"]["enum"]
    assert fanout_enum == list(routing_eval.VALID_FANOUT)


# ---------------------------------------------------------------------------
# Directory layout
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("subdir", ["low_tier", "medium_tier", "high_tier", "urgency", "fanout"])
def test_eval_subdir_exists(subdir):
    assert (EVAL_DIR / subdir).is_dir(), f"tests/eval/{subdir}/ not found"


def test_fanout_dir_has_gitkeep():
    gitkeep = EVAL_DIR / "fanout" / ".gitkeep"
    assert gitkeep.exists(), "tests/eval/fanout/.gitkeep missing"


# ---------------------------------------------------------------------------
# Fixture corpus validity
# ---------------------------------------------------------------------------


def _fixture_paths() -> list[Path]:
    paths: list[Path] = []
    for subdir in ("low_tier", "medium_tier", "high_tier", "urgency"):
        paths.extend(sorted((EVAL_DIR / subdir).glob("*.json")))
    return paths


def test_eval_corpus_has_json_fixtures():
    fixture_paths = _fixture_paths()
    assert fixture_paths, "Expected at least one eval fixture JSON file"


@pytest.mark.parametrize("path", _fixture_paths(), ids=lambda p: str(p.relative_to(EVAL_DIR)))
def test_fixture_valid_json(path: Path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assert isinstance(data, dict)


@pytest.mark.parametrize("path", _fixture_paths(), ids=lambda p: str(p.relative_to(EVAL_DIR)))
def test_fixture_passes_schema(path: Path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    errors = _validate_fixture(data)
    rel = path.relative_to(EVAL_DIR)
    assert errors == [], f"{rel} schema errors:\n" + "\n".join(errors)


# ---------------------------------------------------------------------------
# _validate_fixture unit tests
# ---------------------------------------------------------------------------

def test_validate_fixture_valid():
    fixture = {
        "id": "valid-fixture",
        "category": "low_tier",
        "tags": ["simple"],
        "prompt": "Rename foo.py to bar.py",
        "expected": {
            "tier": "low",
            "score_min": 0.0,
            "score_max": 0.50,
        },
    }
    assert _validate_fixture(fixture) == []


def test_validate_fixture_missing_required_field():
    fixture = {
        "id": "no-prompt",
        "category": "low_tier",
        "tags": ["simple"],
        "expected": {"tier": "low", "score_min": 0.0, "score_max": 0.5},
    }
    errors = _validate_fixture(fixture)
    assert any("prompt" in e for e in errors)


def test_validate_fixture_invalid_tier():
    fixture = {
        "id": "bad-tier",
        "category": "low_tier",
        "tags": ["test"],
        "prompt": "Do something",
        "expected": {"tier": "med", "score_min": 0.0, "score_max": 0.5},
    }
    errors = _validate_fixture(fixture)
    assert any("tier" in e for e in errors)


def test_validate_fixture_score_range_inverted():
    fixture = {
        "id": "inverted-range",
        "category": "low_tier",
        "tags": ["test"],
        "prompt": "Do something",
        "expected": {"tier": "low", "score_min": 0.8, "score_max": 0.2},
    }
    errors = _validate_fixture(fixture)
    assert any("score_min" in e and "score_max" in e for e in errors)


def test_validate_fixture_extra_top_level_key():
    fixture = {
        "id": "extra-key",
        "category": "low_tier",
        "tags": ["test"],
        "prompt": "Do something",
        "expected": {"tier": "low", "score_min": 0.0, "score_max": 0.5},
        "unexpected_field": "value",
    }
    errors = _validate_fixture(fixture)
    assert any("unexpected" in e for e in errors)


def test_validate_fixture_urgency_expected_boolean():
    fixture = {
        "id": "urgency-bad-type",
        "category": "urgency",
        "tags": ["urgency"],
        "prompt": "prod is down fix now",
        "expected": {
            "tier": "high",
            "score_min": 0.6,
            "score_max": 1.0,
            "urgency_expected": "yes",
        },
    }
    errors = _validate_fixture(fixture)
    assert any("urgency_expected" in e for e in errors)


def test_validate_fixture_fanout_invalid_value():
    fixture = {
        "id": "bad-fanout",
        "category": "medium_tier",
        "tags": ["fanout"],
        "prompt": "Do multi-file refactor",
        "expected": {
            "tier": "medium",
            "score_min": 0.3,
            "score_max": 0.8,
            "fanout_expected": "parallel",
        },
    }
    errors = _validate_fixture(fixture)
    assert any("fanout_expected" in e for e in errors)


# ---------------------------------------------------------------------------
# load_fixtures
# ---------------------------------------------------------------------------

def test_load_fixtures_returns_list():
    fixtures = load_fixtures()
    assert isinstance(fixtures, list)
    assert len(fixtures) >= 4, "Expected at least 4 seed fixtures"


def test_load_fixtures_category_filter():
    fixtures = load_fixtures(category="low_tier")
    assert all(f["category"] == "low_tier" for f in fixtures)


def test_load_fixtures_parse_error_has_clear_message(tmp_path, monkeypatch):
    eval_dir = tmp_path / "tests" / "eval"
    category_dir = eval_dir / "low_tier"
    category_dir.mkdir(parents=True)
    bad_fixture = category_dir / "bad.json"
    bad_fixture.write_text('{"id": "broken",', encoding="utf-8")
    monkeypatch.setattr(routing_eval, "EVAL_DIR", eval_dir)

    with pytest.raises(RuntimeError, match=r"Failed to parse eval fixture 'tests/eval/low_tier/bad\.json'"):
        routing_eval.load_fixtures(category="low_tier")


def test_all_loaded_fixtures_pass_validation():
    fixtures = load_fixtures()
    for fixture in fixtures:
        src = fixture.pop("_source", "unknown")
        errors = _validate_fixture(fixture)
        fixture["_source"] = src
        assert errors == [], f"{src} schema errors:\n" + "\n".join(errors)
