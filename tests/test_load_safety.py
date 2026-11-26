"""Synthetic local load/isolation exercise, not a human productivity benchmark."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier
from time import perf_counter

import pytest

from accounting_agent.core import assert_balanced
from accounting_agent.service import Workflow
from accounting_agent.store import Store


def fixture_doc(cid, index):
    return {
        "client_id": cid,
        "type": "invoice",
        "vendor": f"Synthetic Supplies {index % 4}",
        "invoice_number": f"{cid}-LOAD-{index:03d}",
        "date": "2025-06-01",
        "currency": "USD",
        "net": "100.00",
        "tax": "8.00",
        "total": "108.00",
        "tax_rate": ".08",
        "direction": "expense",
        "line_items": [{"description": "Materials", "net": "100.00", "tax": "8.00"}],
    }


def test_200_drafts_five_clients_and_atomic_duplicate_allocation(tmp_path):
    # Explicit database_url=None still consults environment: prevent accidental
    # use of a connected database. This test intentionally measures SQLite only.
    previous = os.environ.pop("DATABASE_URL", None)
    try:
        workflow = Workflow(Store(tmp_path / "synthetic-load.sqlite"))
        clients = {f"load-{i}": f"Fictional load client {i}" for i in range(5)}
        workflow.seed(clients)
        started = perf_counter()
        documents = {}
        for cid in clients:
            for vendor_index in range(4):
                workflow.vendor_rule(
                    cid,
                    f"Synthetic Supplies {vendor_index}",
                    "5000",
                    "load-reviewer",
                    "Synthetic fixture approved mapping",
                )
            documents[cid] = [
                workflow.ingest(cid, fixture_doc(cid, i)) for i in range(40)
            ]
        ingest_elapsed = perf_counter() - started
        reviewed = exports = 0
        for cid in clients:
            for original in documents[cid][:5]:
                corrected = workflow.correct(
                    cid,
                    original["id"],
                    {
                        "category": {
                            "account": "5000",
                            "confidence": "1",
                            "rationale": "Reviewed synthetic material purchase",
                            "source": "manual",
                        }
                    },
                    original["version"],
                    "load-reviewer",
                    "Explicit category review in test",
                )
                approved = workflow.approve(
                    cid,
                    corrected["id"],
                    corrected["version"],
                    "load-reviewer",
                    "Reviewed all synthetic major warnings",
                    acknowledge_issues=True,
                )
                exported = workflow.export(
                    cid,
                    approved["id"],
                    approved["version"],
                    "load-reviewer",
                    "Confirmed synthetic mock export",
                    confirmed=True,
                )
                repeated = workflow.export(
                    cid,
                    approved["id"],
                    approved["version"],
                    "load-reviewer",
                    "Repeat same synthetic mock export",
                    confirmed=True,
                )
                assert exported["idempotency_key"] == repeated["idempotency_key"]
                assert_balanced(exported["journal"]["lines"])
                reviewed += 1
                exports += 1
            assert len(workflow.list(cid, "export")) == 5
        leakage_attempts = 0
        for cid in clients:
            assert len(workflow.list(cid, "document")) == 40
            for other in clients:
                if other == cid:
                    continue
                with pytest.raises(KeyError):
                    workflow.get(cid, "document", documents[other][0]["id"])
                leakage_attempts += 1
            for d in workflow.list(cid, "document"):
                assert d["client_id"] == cid
                assert_balanced(d["journal"]["lines"])
        # Race two explicit reviewer confirmations for the same bank/document.
        # SQLite BEGIN IMMEDIATE serializes the check + write atomically.
        cid = next(iter(clients))
        d = workflow.get(cid, "document", documents[cid][0]["id"])
        bank = {
            "id": "race-bank",
            "client_id": cid,
            "date": "2025-06-02",
            "currency": "USD",
            "amount": "-108.00",
            "description": d["vendor"],
        }
        workflow.import_bank(cid, [bank])
        barrier = Barrier(2)

        def allocate():
            barrier.wait(timeout=10)
            try:
                result = workflow.settle(
                    cid,
                    [bank["id"]],
                    [d["id"]],
                    {d["id"]: "108.00"},
                    {d["id"]: d["version"]},
                    "load-reviewer",
                    "Confirmed concurrency regression",
                    confirmed=True,
                )
                return {"result": "approved", "id": result["id"]}
            except ValueError as exc:
                return {"result": "rejected", "reason": str(exc)}

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: allocate(), range(2)))
        assert sorted(o["result"] for o in outcomes) == ["approved", "rejected"]
        settlements = workflow.list(cid, "settlement")
        assert len(settlements) == 1
        assert_balanced(settlements[0]["lines"])
        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "dataset": "synthetic generated financial values, not document OCR",
            "backend": "SQLite local fallback (not PostgreSQL RLS)",
            "clients": 5,
            "draft_documents": 200,
            "reviewed_corrected_approved": reviewed,
            "mock_exports": exports,
            "idempotent_export_retries": exports,
            "balanced_draft_checks": 200,
            "cross_client_read_attempts_rejected": leakage_attempts,
            "cross_client_leaks_observed": 0,
            "duplicate_allocation_race": {
                "contenders": 2,
                "approved": 1,
                "rejected": 1,
            },
            "ingest_seconds": round(ingest_elapsed, 3),
            "total_seconds": round(perf_counter() - started, 3),
            "scope_limits": [
                "Single local machine; not production scalability",
                "No LLM/provider requests, retries, cost caps or OCR in this test",
                "SQLite isolation predicates are not PostgreSQL RLS",
                "Reviewer confirmations simulated by test code; no measured human time-saving claim",
            ],
            "result": "passed",
        }
        if os.getenv("WRITE_LOAD_REPORT") == "1":
            target = (
                Path(__file__).resolve().parents[1] / "reports" / "load-safety.json"
            )
            target.parent.mkdir(exist_ok=True)
            target.write_text(json.dumps(report, indent=2) + "\n")
    finally:
        if previous is not None:
            os.environ["DATABASE_URL"] = previous


def test_credit_note_direction_cannot_be_reclassified_as_expense():
    from accounting_agent.core import draft_journal

    for direction in ("expense", "income"):
        document = fixture_doc("credit-note-test", 0)
        document.update(type="credit_note", direction=direction)
        with pytest.raises(ValueError, match="Credit-note document"):
            draft_journal(document, "5000")
