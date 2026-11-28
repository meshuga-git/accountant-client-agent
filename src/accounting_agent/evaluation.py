"""Reproducible synthetic evaluation; no target attainment is presumed."""

from __future__ import annotations

import json
import statistics
import time
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

KEY_FIELDS = ("vendor", "date", "invoice_number", "net", "tax", "total", "currency")


def _equal(field, actual, expected):
    if field in ("net", "tax", "total"):
        try:
            return Decimal(str(actual)) == Decimal(str(expected))
        except InvalidOperation:
            return False
    return (
        str(actual or "").strip().casefold() == str(expected or "").strip().casefold()
    )


def run_evaluation(
    corpus_dir, output_dir=None, backend="offline", provider=None, split="heldout"
):
    """Evaluate actual extraction; matching/duplicates isolated on labeled inputs.

    Warm history uses dev labels treated as simulated accountant-approved records,
    not model predictions or held-out labels. Stage metrics are not end-to-end claims.
    """
    from .core import (
        assert_balanced,
        draft_journal,
        duplicate_candidates,
        reconcile,
    )
    from .extraction import categorize, extract_document

    root = Path(corpus_dir)
    manifest = json.loads((root / "manifest.json").read_text())
    truth = json.loads((root / "ground_truth.json").read_text())
    chosen = [r for r in manifest["documents"] if split == "all" or r["split"] == split]
    history = [
        {
            "client_id": r["client_id"],
            "vendor": truth["documents"][r["id"]]["fields"]["vendor"],
            "account": truth["documents"][r["id"]]["account"],
            "approved": True,
            "date": truth["documents"][r["id"]]["fields"]["date"],
        }
        for r in manifest["documents"]
        if r["split"] == "dev"
        and not r["adversarial"]
        and not truth["documents"][r["id"]]["duplicate_of"]
        and not truth["documents"][r["id"]]["issues"]
    ]
    hits = Counter()
    counts = Counter()
    cold = warm = 0
    category_n = 0
    failures = []
    latencies = []
    cost = Decimal("0")
    tokens = 0
    abstentions = 0
    adversarial_rejected = 0
    adversarial_n = 0
    degraded_hits = Counter()
    degraded_counts = Counter()
    predictions = {}
    for r in chosen:
        started = time.perf_counter()
        gold = truth["documents"][r["id"]]
        try:
            result = extract_document(
                root / r["path"], r["client_id"], backend=backend, provider=provider
            )
            values = result.get("values", {})
            predictions[r["id"]] = values
        except Exception as exc:
            failures.append(
                {
                    "id": r["id"],
                    "stage": "extraction",
                    "error": f"{type(exc).__name__}: {exc}"[:300],
                }
            )
            result = {}
            values = {}
        latencies.append(time.perf_counter() - started)
        usage = result.get("usage", {}) or {}
        cost += Decimal(str(usage.get("cost_usd", usage.get("cost", 0)) or 0))
        tokens += int(usage.get("total_tokens", usage.get("tokens", 0)) or 0)
        if r["adversarial"]:
            adversarial_n += 1
            safe = not all(values.get(k) for k in ("vendor", "date", "total"))
            adversarial_rejected += int(safe)
            if not safe:
                failures.append(
                    {
                        "id": r["id"],
                        "stage": "adversarial",
                        "error": "Non-invoice unexpectedly produced mandatory values",
                    }
                )
            continue
        for field in KEY_FIELDS:
            counts[field] += 1
            ok = _equal(field, values.get(field), gold["fields"][field])
            hits[field] += ok
            if r["degraded"]:
                degraded_counts[field] += 1
                degraded_hits[field] += ok
            if not ok:
                failures.append(
                    {
                        "id": r["id"],
                        "stage": "extraction",
                        "field": field,
                        "expected": gold["fields"][field],
                        "actual": values.get(field),
                    }
                )
        if not gold["duplicate_of"]:
            category_n += 1
            # Categorization stage uses labeled field values so OCR failures do not
            # confound the separately reported category quality measurement.
            doc = gold["fields"]
            try:
                c = categorize(
                    r["client_id"],
                    doc,
                    manifest["chart_of_accounts"],
                    {},
                    [],
                    provider=provider if backend != "offline" else None,
                )
                w = categorize(
                    r["client_id"],
                    doc,
                    manifest["chart_of_accounts"],
                    {},
                    [
                        h
                        for h in history
                        if h["client_id"] == r["client_id"] and h["date"] < doc["date"]
                    ],
                    provider=provider if backend != "offline" else None,
                )
                cold += c.get("account") == gold["account"]
                warm += w.get("account") == gold["account"]
                abstentions += not bool(c.get("account"))
                for cat in (c, w):
                    u = cat.get("usage", {}) or {}
                    cost += Decimal(str(u.get("cost_usd", u.get("cost", 0)) or 0))
                    tokens += int(u.get("total_tokens", u.get("tokens", 0)) or 0)
            except Exception as exc:
                failures.append(
                    {"id": r["id"], "stage": "categorization", "error": str(exc)[:300]}
                )
    ids = {r["id"] for r in chosen}
    base = [
        v["fields"]
        for k, v in truth["documents"].items()
        if v["fields"] and not v["duplicate_of"]
    ]
    duplicate_n = duplicate_found = 0
    bank_n = eligible = auto_n = correct = 0
    bank_failures = []
    drafts = unbalanced = invalid_totals = 0
    for r in chosen:
        g = truth["documents"][r["id"]]
        if g["duplicate_of"]:
            duplicate_n += 1
            found = duplicate_candidates(
                g["fields"], [d for d in base if d["client_id"] == r["client_id"]]
            )
            duplicate_found += any(f["document_id"] == g["duplicate_of"] for f in found)
        if g["fields"] and not g["duplicate_of"]:
            try:
                journal = draft_journal(g["fields"], g["account"])
                assert_balanced(journal["lines"])
                drafts += 1
            except ValueError:
                if Decimal(g["fields"]["net"]) + Decimal(g["fields"]["tax"]) != Decimal(
                    g["fields"]["total"]
                ):
                    invalid_totals += 1
                else:
                    unbalanced += 1
    for b in truth["bank_transactions"]:
        bdocs = b["truth_documents"]
        if split != "all" and bdocs and not any(i in ids for i in bdocs):
            continue
        if split == "dev" and not bdocs:
            continue
        bank_n += 1
        eligible += b["truth_kind"] == "exact"
        docs = [d for d in base if d["client_id"] == b["client_id"]]
        result = reconcile(b, docs)
        auto = result["auto_match"]
        if auto:
            auto_n += 1
            ok = b["truth_kind"] == "exact" and bdocs == [auto["document_id"]]
            correct += ok
            if not ok:
                bank_failures.append(
                    {
                        "bank_id": b["id"],
                        "predicted": auto["document_id"],
                        "expected": bdocs,
                        "kind": b["truth_kind"],
                    }
                )

    def ratio(n, d):
        return round(n / d, 6) if d else None

    report = {
        "schema_version": 1,
        "dataset_seed": manifest["seed"],
        "split": split,
        "backend": backend,
        "synthetic_only": True,
        "documents_evaluated": len(chosen),
        "metrics": {
            "extraction_per_field": {k: ratio(hits[k], counts[k]) for k in KEY_FIELDS},
            "extraction_micro_accuracy": ratio(
                sum(hits.values()), sum(counts.values())
            ),
            "degraded_extraction_per_field": {
                k: ratio(degraded_hits[k], degraded_counts[k]) for k in KEY_FIELDS
            },
            "category_cold_accuracy": ratio(cold, category_n),
            "category_warm_accuracy": ratio(warm, category_n),
            "category_count": category_n,
            "category_cold_abstentions": abstentions,
            "auto_match_precision": ratio(correct, auto_n),
            "auto_match_recall_exact_eligible": ratio(correct, eligible),
            "auto_matches": auto_n,
            "exact_eligible_bank_lines": eligible,
            "bank_lines": bank_n,
            "duplicate_recall": ratio(duplicate_found, duplicate_n),
            "duplicate_cases": duplicate_n,
            "balanced_drafts": drafts,
            "unbalanced_drafts": unbalanced,
            "invalid_totals_rejected": invalid_totals,
            "adversarial_mandatory_field_abstention": ratio(
                adversarial_rejected, adversarial_n
            ),
            "adversarial_cases": adversarial_n,
            "cost_usd": str(cost),
            "cost_per_document_usd": str(cost / len(chosen) if chosen else 0),
            "total_tokens": tokens,
            "latency_seconds_mean": round(statistics.mean(latencies), 5)
            if latencies
            else None,
            "latency_seconds_p95": round(
                sorted(latencies)[min(len(latencies) - 1, int(len(latencies) * 0.95))],
                5,
            )
            if latencies
            else None,
        },
        "failures": failures,
        "wrong_matches": bank_failures,
        "limits": [
            "Synthetic templated English corpus only; not a real-world accuracy claim.",
            "Matching score weights were adjusted after observing this held-out split: these are reused development diagnostics, NOT untouched held-out validation. A fresh independent corpus is required.",
            "Offline extraction reads PDF text layers. Raster receipts require configured vision provider and conservatively abstain offline.",
            "Cold category and matching stage metrics use labeled normalized fields; they are not end-to-end OCR pipeline metrics.",
            "Warm category history is simulated accountant-approved dev labels strictly earlier than the evaluated document; no held-out labels enter history.",
            "Exact-eligible recall excludes compound, fee, partial and missing-document cases; these require reviewed settlement.",
            "Duplicate metric uses labeled normalized fields, not degraded extraction outputs.",
            "Offline provider cost is zero; live LLM cost and extraction target unverified without configured model run.",
            "Rule branch tests are in the test suite; no corpus-wide A01-A12 sensitivity claim or manual time-saving claim.",
            "Precision is undefined (null) if no auto matches; no result is silently converted into a pass.",
        ],
    }
    report["rule_fixture_results"] = evaluate_rule_fixtures()
    report["targets"] = {
        "extraction_97pct": report["metrics"]["extraction_micro_accuracy"] is not None
        and report["metrics"]["extraction_micro_accuracy"] >= 0.97,
        "category_cold_85pct": (report["metrics"]["category_cold_accuracy"] or 0)
        >= 0.85,
        "category_warm_93pct": (report["metrics"]["category_warm_accuracy"] or 0)
        >= 0.93,
        "match_precision_99pct": (report["metrics"]["auto_match_precision"] or 0)
        >= 0.99,
        "match_recall_85pct": (
            report["metrics"]["auto_match_recall_exact_eligible"] or 0
        )
        >= 0.85,
        "duplicate_recall_95pct": (report["metrics"]["duplicate_recall"] or 0) >= 0.95,
        "zero_unbalanced": unbalanced == 0,
    }
    output = Path(output_dir or root / "reports")
    output.mkdir(parents=True, exist_ok=True)
    (output / f"eval-{split}-{backend}.json").write_text(json.dumps(report, indent=2))
    lines = (
        [
            "# Synthetic accounting evaluation",
            f"Backend: `{backend}`. Split: `{split}` (reused development diagnostics, not untouched). Documents: {len(chosen)}.",
            "",
            "## Measured stage metrics",
            "```json",
            json.dumps(report["metrics"], indent=2),
            "```",
            "## Target checks",
            "```json",
            json.dumps(report["targets"], indent=2),
            "```",
            "## Rule probes",
            "```json",
            json.dumps(report["rule_fixture_results"], indent=2),
            "```",
            "## Limits",
        ]
        + ["- " + s for s in report["limits"]]
        + [
            "",
            "## Failures",
            f"{len(failures)} field/stage failures; {len(bank_failures)} wrong automatic matches. Full detail in companion JSON.",
        ]
    )
    (output / f"eval-{split}-{backend}.md").write_text("\n".join(lines))
    return report


def evaluate_rule_fixtures():
    """Independent positive/negative branch probes, not corpus sensitivity estimates."""
    from copy import deepcopy

    from .core import validate_document

    base = {
        "id": "fixture",
        "client_id": "fixture-client",
        "vendor": "Fixture Vendor",
        "date": "2025-10-01",
        "due_date": "2025-10-03",
        "invoice_number": "FIX-1",
        "type": "invoice",
        "direction": "expense",
        "currency": "USD",
        "net": "100.00",
        "tax": "8.00",
        "tax_rate": "0.08",
        "total": "108.00",
        "lines": [{"net": "100.00", "tax": "8.00", "total": "108.00"}],
    }
    ctx = {
        "today": "2025-10-20",
        "known_vendors": ["Fixture Vendor"],
        "category": {"account": "5000", "confidence": "1"},
        "base_currency": "USD",
        "supported_currencies": ["USD", "EUR"],
        "allowed_tax_rates": ["0", "0.08"],
        "vendor_history": ["100.00", "108.00", "110.00"],
    }
    cases = {}
    for n in range(1, 13):
        rule = f"A{n:02}"
        doc = deepcopy(base)
        context = deepcopy(ctx)
        if n == 1:
            context["existing_documents"] = [{**base, "id": "earlier-copy"}]
        if n == 2:
            doc["total"] = "109.00"
        if n == 3:
            context["journal"] = {
                "client_id": "fixture-client",
                "lines": [
                    {"account": "5000", "debit": "100.00", "credit": "0.00"},
                    {"account": "2100", "debit": "0.00", "credit": "99.00"},
                ],
            }
        if n == 4:
            context["lock_date"] = "2025-10-01"
        if n == 5:
            doc["tax_rate"] = "0.09"
        if n == 6:
            doc["currency"] = "EUR"
        if n == 7:
            context["vendor_history"] = ["1.00", "2.00", "3.00"]
        if n == 8:
            context["known_vendors"] = []
        if n == 9:
            doc.pop("invoice_number")
        if n == 10:
            context["category"]["confidence"] = "0.20"
        if n == 11:
            context["unmatched_bank_lines"] = [
                {"id": "old", "client_id": "fixture-client", "date": "2025-10-01"}
            ]
        if n == 12:
            context["bank"] = {
                "id": "distant",
                "client_id": "fixture-client",
                "date": "2025-01-01",
            }
        positive = {i["rule_id"] for i in validate_document(doc, context)}
        negative = {i["rule_id"] for i in validate_document(base, ctx)}
        cases[rule] = {
            "positive_detected": rule in positive,
            "negative_clear": rule not in negative,
            "pass": rule in positive and rule not in negative,
        }
    return cases
