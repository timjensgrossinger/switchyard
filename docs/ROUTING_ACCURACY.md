# Routing accuracy (operator report)

Reproducible fixture-based tier routing stats from `python3 -m shared.routing_report`.
Do not commit `tests/eval/baseline.json`; regenerate this document locally or from CI artifacts.

- **Generated:** 2026-06-10
- **Config hash:** `c00783868c47`
- **Fixtures:** 34
- **Executed accuracy:** 100.0%
- **Boundary fixtures (informational):** 2 skipped

## How to refresh

```bash
THRENODY_TEST_MODE=1 python3 -m shared.routing_report --write-docs
THRENODY_TEST_MODE=1 python3 -m shared.routing_eval
```
# Threnody Routing Eval Report

**Date:** 2026-06-10  
**Fixtures:** 34  
**Accuracy:** 94.1%  

| Status | Count |
|--------|-------|
| Pass | 32 |
| Fail | 0 |
| Skip | 2 |

## Category Accuracy

| Category | Pass | Fail | Skip | Executed Accuracy |
|----------|------|------|------|-------------------|
| high_tier | 8 | 0 | 2 | 100.0% |
| low_tier | 10 | 0 | 0 | 100.0% |
| medium_tier | 11 | 0 | 0 | 100.0% |
| urgency | 3 | 0 | 0 | 100.0% |
