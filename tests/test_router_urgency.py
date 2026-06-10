import re
from shared.config import TGsConfig
from shared.router import TaskRouter


def test_classify_includes_urgency_fields():
    """D-07/D-08: RoutingDecision must expose urgency fields without breaking existing fields."""
    cfg = TGsConfig()
    r = TaskRouter(cfg)
    decision = r.classify("just a small change")

    # existing fields still present
    assert hasattr(decision, "score")
    assert hasattr(decision, "reason")

    # new urgency explainability surface
    assert hasattr(decision, "urgency_score")
    assert isinstance(decision.urgency_score, float)
    assert hasattr(decision, "matched_urgency_signals")
    assert isinstance(decision.matched_urgency_signals, list)
    # default should be 0.0 for non-urgent prompts
    assert decision.urgency_score == 0.0


def test_soft_implied_urgency_detected():
    """D-01/D-02: Softer implied urgency like "by EOD" and "ASAP" raises urgency_score."""
    cfg = TGsConfig()
    r = TaskRouter(cfg)
    prompt = "Please finish this by EOD — we need it ASAP."
    decision = r.classify(prompt)

    assert decision.urgency_score > 0.0
    # matched signals should mention at least eod or asap
    matched = " ".join(decision.matched_urgency_signals).lower()
    assert re.search(r"eod|asap|by eod", matched)


def test_excluded_phrases_do_not_raise_urgency():
    """D-03: Phrases like 'quick question', 'review', 'refactor' do not trigger urgency."""
    cfg = TGsConfig()
    r = TaskRouter(cfg)
    prompt = "Quick question: could you review this refactor?"
    decision = r.classify(prompt)

    assert decision.urgency_score == 0.0
    assert decision.matched_urgency_signals == []
