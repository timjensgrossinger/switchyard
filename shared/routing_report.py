"""Operator-facing routing accuracy report generator."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from shared.config import load_eval_config
from shared.routing_eval import format_markdown_report, run_eval

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DOC_PATH = REPO_ROOT / "docs" / "ROUTING_ACCURACY.md"


def _config_fingerprint(config: object) -> str:
    thresholds = getattr(config, "thresholds", None)
    payload = {
        "low_max": getattr(thresholds, "low_max", None),
        "medium_max": getattr(thresholds, "medium_max", None),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return digest[:12]


def _summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    passed = sum(1 for row in results if row.get("status") == "pass")
    failed = sum(1 for row in results if row.get("status") == "fail")
    skipped = sum(1 for row in results if row.get("status") == "skip")
    boundary = [row for row in results if "boundary" in str(row.get("reason", "")).lower()]
    executed = [row for row in results if row.get("status") in {"pass", "fail"}]
    executed_accuracy = (passed / max(len(executed), 1)) * 100.0

    by_category: dict[str, dict[str, Any]] = {}
    for row in results:
        category = str(row.get("category") or "unknown")
        bucket = by_category.setdefault(
            category,
            {"pass": 0, "fail": 0, "skip": 0, "executed_accuracy_pct": 0.0},
        )
        status = str(row.get("status") or "fail")
        if status in bucket:
            bucket[status] += 1
        executed_rows = bucket["pass"] + bucket["fail"]
        bucket["executed_accuracy_pct"] = round(
            (bucket["pass"] / max(executed_rows, 1)) * 100.0,
            1,
        )

    return {
        "fixture_count": total,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "executed_accuracy_pct": round(executed_accuracy, 1),
        "boundary_fixture_count": len(boundary),
        "boundary_skipped": len(boundary),
        "by_category": by_category,
    }


def build_routing_report(
    *,
    filter_categories: list[str] | None = None,
) -> dict[str, Any]:
    """Run routing eval and return structured report metadata."""
    config = load_eval_config()
    eval_out = run_eval(filter_categories=filter_categories, return_results=True)
    if not isinstance(eval_out, dict):
        raise RuntimeError("routing eval did not return structured results")
    results = eval_out.get("result")
    if not isinstance(results, list):
        results = []
    summary = _summarize_results(results)
    return {
        "generated_at": date.today().isoformat(),
        "config_hash": _config_fingerprint(config),
        "summary": summary,
        "results": results,
        "exit_code": int(eval_out.get("exit_code") or 0),
        "regressions": eval_out.get("regressions") or [],
    }


def render_routing_accuracy_markdown(report: dict[str, Any]) -> str:
    """Render operator markdown including eval table + trust metadata."""
    results = report.get("results")
    if not isinstance(results, list):
        results = []
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    body = format_markdown_report(results, date_str=str(report.get("generated_at") or date.today().isoformat()))

    header = [
        "# Routing accuracy (operator report)",
        "",
        "Reproducible fixture-based tier routing stats from `python3 -m shared.routing_report`.",
        "Do not commit `tests/eval/baseline.json`; regenerate this document locally or from CI artifacts.",
        "",
        f"- **Generated:** {report.get('generated_at')}",
        f"- **Config hash:** `{report.get('config_hash', 'unknown')}`",
        f"- **Fixtures:** {summary.get('fixture_count', 0)}",
        f"- **Executed accuracy:** {summary.get('executed_accuracy_pct', 0.0)}%",
        f"- **Boundary fixtures (informational):** {summary.get('boundary_fixture_count', 0)} skipped",
        "",
        "## How to refresh",
        "",
        "```bash",
        "THRENODY_TEST_MODE=1 python3 -m shared.routing_report --write-docs",
        "THRENODY_TEST_MODE=1 python3 -m shared.routing_eval",
        "```",
        "",
    ]
    return "\n".join(header) + body


def write_routing_accuracy_doc(
    path: Path | None = None,
    *,
    filter_categories: list[str] | None = None,
) -> dict[str, Any]:
    report = build_routing_report(filter_categories=filter_categories)
    target = path or DEFAULT_DOC_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_routing_accuracy_markdown(report), encoding="utf-8")
    report["written_to"] = str(target)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate Threnody routing accuracy report")
    parser.add_argument(
        "--filter",
        dest="filters",
        action="append",
        metavar="CATEGORY",
        help="Filter categories (low|medium|high|urgency|fanout). Comma-separated allowed.",
    )
    parser.add_argument(
        "--write-docs",
        action="store_true",
        help=f"Write markdown report to {DEFAULT_DOC_PATH}",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print structured JSON summary to stdout",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_DOC_PATH,
        help="Markdown output path for --write-docs",
    )
    args = parser.parse_args(argv)

    filters: list[str] | None = None
    if args.filters:
        filters = []
        for raw in args.filters:
            for part in raw.split(","):
                part = part.strip()
                if part:
                    filters.append(part)

    if args.write_docs:
        report = write_routing_accuracy_doc(args.output, filter_categories=filters)
        if args.json:
            printable = {key: value for key, value in report.items() if key != "results"}
            print(json.dumps(printable, indent=2, sort_keys=True))
        else:
            print(f"wrote {report['written_to']}")
        return int(report.get("exit_code") or 0)

    report = build_routing_report(filter_categories=filters)
    if args.json:
        printable = {key: value for key, value in report.items() if key != "results"}
        print(json.dumps(printable, indent=2, sort_keys=True))
    else:
        print(render_routing_accuracy_markdown(report))
    return int(report.get("exit_code") or 0)


if __name__ == "__main__":
    raise SystemExit(main())
