#!/usr/bin/env python3
"""G04-T04 -- Standalone demonstration runner.

Prints the actual evaluation output (routing decisions, per-case metrics,
aggregate metrics, quality-gate results) by running the golden dataset
against an in-memory SQLite database seeded with the G04-T04 evaluation
fixture.

Per mission §10 closing remark:

  "Include a concise demonstration showing actual evaluation output and
   routing decisions."

Usage:

    python scripts/run_g04_t04_evaluation.py

The script exits 0 if all quality gates pass and there are no routing or
intent mismatches. Otherwise it exits 1 (visible quality-gate failure
per mission §6).
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Make src/ importable for direct invocation.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "integration"))

# Force test profile BEFORE any settings are imported.
os.environ.setdefault("SYNAPSE_ENV", "test")
os.environ.setdefault("SYNAPSE_DB_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SYNAPSE_AUTH_MODE", "development")
os.environ.setdefault("SYNAPSE_DEV_API_KEYS", "test-key-reader")
os.environ.setdefault("SYNAPSE_ADMIN_API_KEYS", "test-key-admin")
os.environ.setdefault("SYNAPSE_CORS_ORIGINS", "http://localhost:3000")
os.environ.setdefault("SYNAPSE_CORS_ALLOW_CREDENTIALS", "true")
os.environ.setdefault("SYNAPSE_RATE_LIMIT_PER_MINUTE", "10000")
os.environ.setdefault("SYNAPSE_RATE_LIMIT_BURST", "1000")
os.environ.setdefault("SYNAPSE_LOG_LEVEL", "WARNING")


async def _main() -> int:
    # Lazy imports (after env vars are set).
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from test_g04_t04_evaluation import _seed_evaluation_fixture

    from synapse.evaluation import run_evaluation
    from synapse.storage import models  # noqa: F401
    from synapse.storage.base import Base

    eng = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(eng, expire_on_commit=False)

    async with factory() as session:
        await _seed_evaluation_fixture(session)
        report = await run_evaluation(session)

    # Pretty-print the report.
    print("=" * 78)
    print(f"G04-T04 EVALUATION REPORT  (dataset v{report.dataset_version})")
    print("=" * 78)
    print(f"Cases                     : {report.case_count}")
    print(f"All quality gates passed  : {report.all_quality_gates_passed}")
    print(f"Failed gates              : {len(report.failed_gates)}")
    print(f"Routing mismatches        : {len(report.routing_mismatches)}")
    print(f"Intent mismatches         : {len(report.intent_mismatches)}")
    print()

    print("-" * 78)
    header = (
        f"{'CASE':<14} "
        f"{'PATH':<5} "
        f"{'INTENT':<26} "
        f"{'P@5':>6} "
        f"{'R@5':>6} "
        f"{'MRR':>6} "
        f"{'CIT':>5} "
        f"{'EV%':>5} "
        f"{'COV%':>5} "
        f"{'DUR(ms)':>8}"
    )
    print(header)
    print("-" * 78)
    for cm in report.case_metrics:
        print(
            f"{cm.case_id:<14} "
            f"{cm.routing_decision.path.value:<5} "
            f"{cm.routing_decision.intent.value:<26} "
            f"{cm.precision_at_5:>6.2f} "
            f"{cm.recall_at_5:>6.2f} "
            f"{cm.mrr:>6.2f} "
            f"{cm.citation_validity:>5.2f} "
            f"{cm.evidence_grounded_finding_rate * 100:>5.1f} "
            f"{cm.finding_coverage * 100:>5.1f} "
            f"{cm.resource_report.duration_seconds[1] * 1000:>8.2f}"
        )
    print("-" * 78)
    print()

    print("Aggregate metrics:")
    for k, v in sorted(report.aggregate_metrics.items()):
        print(f"  {k:<48} {v:.4f}")
    print()

    if report.failed_gates:
        print("Failed quality gates (visible failures per mission §6):")
        for g in report.failed_gates:
            print(f"  ✗ {g.name:<40} | {g.detail}")
        print()

    if report.routing_mismatches:
        print("Routing mismatches:")
        for m in report.routing_mismatches:
            print(f"  ⚠ {m}")
        print()

    if report.intent_mismatches:
        print("Intent mismatches:")
        for m in report.intent_mismatches:
            print(f"  ⚠ {m}")
        print()

    print("=" * 78)
    if (
        report.all_quality_gates_passed
        and not report.routing_mismatches
        and not report.intent_mismatches
    ):
        print("RESULT: PASS — all quality gates green, routing and intent match expected.")
        return 0
    print("RESULT: FAIL — see failed gates / mismatches above.")
    return 1


def main() -> int:
    return asyncio.run(_main())


if __name__ == "__main__":
    sys.exit(main())
