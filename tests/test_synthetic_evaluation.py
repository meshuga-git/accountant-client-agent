import json
from decimal import Decimal
from pathlib import Path

from accounting_agent.evaluation import _equal, evaluate_rule_fixtures
from accounting_agent.synthetic import CLIENTS, COA


def test_money_comparison_uses_decimal():
    assert _equal("total", "01.00", "1")
    assert not _equal("total", "NaN", "1")
    assert not _equal("total", None, "1")
    assert _equal("vendor", " Vendor ", "vendor")


def test_all_rule_fixture_probes_pass():
    results = evaluate_rule_fixtures()
    assert len(results) == 12
    assert all(v["pass"] for v in results.values()), results


def test_chart_and_clients_unique():
    assert len({c["id"] for c in CLIENTS}) == 3
    assert len({a["code"] for a in COA}) == len(COA)
    assert {"1000", "2100", "5000"} <= {a["code"] for a in COA}


def test_generated_corpus_invariants():
    root = Path(__file__).resolve().parents[1] / "demo_data"
    if not (root / "manifest.json").exists():
        import pytest

        pytest.skip("Generate corpus with ledgerline generate first")
    m = json.loads((root / "manifest.json").read_text())
    truth = json.loads((root / "ground_truth.json").read_text())
    assert len(m["documents"]) == 412
    assert sum(d["adversarial"] for d in m["documents"]) == 10
    assert sum(d["degraded"] for d in m["documents"]) == 36
    for client in CLIENTS:
        assert {
            d["month"] for d in m["documents"] if d["client_id"] == client["id"]
        } == set(range(1, 13))
    assert all(
        d["split"] == ("dev" if d["month"] <= 9 else "heldout") for d in m["documents"]
    )
    assert all((root / d["path"]).is_file() for d in m["documents"])
    for b in truth["bank_transactions"]:
        assert Decimal(b["amount"]) < 0
        assert all(
            truth["documents"][x]["fields"]["client_id"] == b["client_id"]
            for x in b["truth_documents"]
        )
        if b["truth_kind"] in ("exact", "compound"):
            assert abs(Decimal(b["amount"])) == sum(
                Decimal(truth["documents"][x]["fields"]["total"])
                for x in b["truth_documents"]
            )


def test_rendered_line_items_extract_as_grounded_json(tmp_path):
    from accounting_agent.extraction import extract_document
    from accounting_agent.synthetic import _render, _text

    d = {
        "type": "invoice",
        "vendor": "Example",
        "invoice_number": "FIX1",
        "date": "2025-01-01",
        "due_date": "2025-01-02",
        "currency": "USD",
        "net": "100.00",
        "tax": "8.00",
        "tax_rate": "0.08",
        "total": "108.00",
        "lines": [
            {
                "description": "Professional services monthly purchase",
                "net": "100.00",
                "tax": "8.00",
                "total": "108.00",
            }
        ],
    }
    p = tmp_path / "invoice.pdf"
    _render(p, _text(d))
    result = extract_document(p, "fixture")
    assert result["values"]["lines"] == d["lines"]
    assert result["fields"]["lines"]["grounded"]
    assert not result["issues"]
