"""Independent money/isolation regression checks, not builder happy paths."""

from decimal import Decimal

import pytest

from accounting_agent.core import (
    draft_journal,
    reconcile,
    settlement_proposal,
    validate_document,
)
from accounting_agent.store import Store


def document(**changes):
    d = dict(
        id="doc",
        client_id="a",
        vendor="Acme",
        invoice_number="INV-7",
        date="2025-06-01",
        currency="USD",
        net="100.00",
        tax="4.00",
        total="104.00",
        tax_rate=".04",
        direction="expense",
    )
    d.update(changes)
    return d


def bank(**changes):
    b = dict(
        id="bank",
        client_id="a",
        date="2025-06-01",
        currency="USD",
        amount="-104.00",
        description="Acme INV-7",
    )
    b.update(changes)
    return b


def test_ny_purchase_tax_not_vat_asset():
    lines = draft_journal(document(), "5000")["lines"]
    assert not any(x["account"] == "1400" for x in lines)
    assert next(x for x in lines if x["account"] == "5000")["debit"] == "104.00"


def test_refund_settlement_clears_same_payable_as_credit_note():
    d = document(direction="credit_note")
    draft = draft_journal(d, "5000")
    refund = settlement_proposal([bank(amount="104.00")], [d])
    payable = sum(
        Decimal(x["debit"]) - Decimal(x["credit"])
        for x in draft["lines"] + refund["lines"]
        if x["account"] == "2100"
    )
    assert payable == 0, (
        "Credit-note refund must clear payable, not unrelated receivable"
    )


def test_consumed_bank_cannot_be_auto_matched_again():
    assert reconcile(bank(matched=True), [document()])["auto_match"] is None


def test_exact_ambiguous_amounts_never_auto_match():
    assert (
        reconcile(bank(), [document(), document(id="other", invoice_number="INV-8")])[
            "auto_match"
        ]
        is None
    )


def test_fx_requires_document_date_not_merely_any_rate():
    ctx = dict(
        today="2025-06-30",
        supported_currencies=["USD", "EUR"],
        fx_rates={"2025-05-31:EUR": "1.1"},
    )
    assert "A06" in {
        x["rule_id"] for x in validate_document(document(currency="EUR"), ctx)
    }


def test_sqlite_scoping_and_write_rejection(tmp_path):
    store = Store(tmp_path / "test.sqlite")
    with store.transaction("a") as tx:
        tx.put("document", "same", document())
        with pytest.raises(PermissionError):
            tx.put("document", "foreign", document(client_id="b"))
    with store.transaction("b") as tx:
        assert tx.list("document") == []
        with pytest.raises(KeyError):
            tx.get("document", "same")


@pytest.fixture
def workflow(tmp_path):
    from accounting_agent.service import Workflow

    w = Workflow(Store(tmp_path / "workflow.sqlite"))
    w.seed({"a": "Fictional A", "b": "Fictional B"})
    return w


def approved(w, **changes):
    d = w.ingest("a", document(**changes))
    d = w.correct(
        "a",
        d["id"],
        {
            "category": {
                "account": "5000",
                "confidence": "1",
                "rationale": "Human checked",
            }
        },
        1,
        "reviewer",
        "Categorized",
    )
    return w.approve("a", d["id"], d["version"], "reviewer", "Checked", True)


def test_manual_rule_ingestion(workflow):
    workflow.vendor_rule("a", "Acme", "5000", "reviewer", "Use reviewed mapping")
    d = workflow.ingest("a", document())
    assert d["category"]["account"] == "5000"


def test_approval_export_version_lock_and_idempotency(workflow):
    w = workflow
    d = approved(w)
    with pytest.raises(ValueError):
        w.export("a", d["id"], d["version"], "reviewer", "Export", False)
    with pytest.raises(KeyError):
        w.export("b", d["id"], d["version"], "reviewer", "Export", True)
    e = w.export("a", d["id"], d["version"], "reviewer", "Export", True)
    assert (
        w.export("a", d["id"], d["version"], "reviewer", "Retry", True)["id"] == e["id"]
    )
    assert len(w.list("a", "export")) == 1
    w.configure("a", {"lock_date": "2025-06-01"}, "reviewer", "Period close")
    with pytest.raises(ValueError):
        w.export("a", d["id"], d["version"], "reviewer", "Retry after close", True)


def test_correction_invalidates_learning_and_stale_export(workflow):
    w = workflow
    d = approved(w)
    changed = w.correct(
        "a",
        d["id"],
        {"category": {"account": "5100", "confidence": "1"}},
        d["version"],
        "reviewer",
        "Correction",
    )
    assert changed["approved_version"] is None and changed["status"] == "draft"
    with pytest.raises(ValueError):
        w.export("a", d["id"], d["version"], "reviewer", "Stale", True)
    with pytest.raises(ValueError):
        w.export("a", d["id"], changed["version"], "reviewer", "Unapproved", True)
    fresh = w.ingest("a", document(invoice_number="INV-8"))
    assert fresh["category"]["source"] != "approved-history"


def test_partial_reservation_cannot_overallocate_or_reuse_bank(workflow):
    w = workflow
    d = approved(w)
    w.import_bank("a", [bank(amount="-50"), bank(id="second", amount="-60")])
    w.settle(
        "a",
        ["bank"],
        [d["id"]],
        {d["id"]: "50"},
        {d["id"]: d["version"]},
        "reviewer",
        "Partial",
        confirmed=True,
    )
    with pytest.raises(ValueError):
        w.settle(
            "a",
            ["bank"],
            [d["id"]],
            {d["id"]: "50"},
            {d["id"]: d["version"]},
            "reviewer",
            "Reuse",
            confirmed=True,
        )
    with pytest.raises(ValueError):
        w.settle(
            "a",
            ["second"],
            [d["id"]],
            {d["id"]: "60"},
            {d["id"]: d["version"]},
            "reviewer",
            "Overallocate",
            confirmed=True,
        )
    assert len(w.list("a", "settlement")) == 1
    changed = w.correct(
        "a",
        d["id"],
        {"invoice_number": "INV-7-corrected"},
        d["version"],
        "reviewer",
        "Correct reference",
    )
    assert changed["status"] == "draft"
    assert w.list("a", "settlement")[0]["status"] == "invalidated"


def test_credit_note_ingestion_does_not_default_to_new_expense(workflow):
    values = document(type="credit_note")
    values.pop("direction")
    d = workflow.ingest("a", values)
    assert d["direction"] == "credit_note"


def test_duplicate_hash_document_not_auto_match_suggestion(workflow):
    w = workflow
    d = w.ingest("a", document(), extraction={"hash": "identical-source"})
    w.ingest(
        "a",
        document(invoice_number="INV-OTHER", date="2025-02-01"),
        extraction={"hash": "identical-source"},
    )
    w.import_bank("a", [bank()])
    result = w.matches("a")
    assert result[0]["auto_match"] is None, (
        f"Duplicate source {d['id']} must not auto-match"
    )


def test_api_credential_grants_and_reviewer_cannot_be_forged(workflow, monkeypatch):
    from fastapi.testclient import TestClient

    from accounting_agent import api

    w = workflow
    d = approved(w)
    monkeypatch.setattr(api, "workflow", w)
    monkeypatch.setattr(
        api, "GRANTS", {"a-secret": {"clients": ["a"], "reviewer": "actual-reviewer"}}
    )
    client = TestClient(api.app)
    headers = {"Authorization": "Bearer a-secret"}
    assert client.get("/clients/a/documents").status_code == 401
    assert client.get("/clients/b/documents", headers=headers).status_code == 403
    assert (
        client.get("/clients/b/documents/" + d["id"], headers=headers).status_code
        == 403
    )
    assert (
        client.get(
            "/clients/b/documents/" + d["id"] + "/source", headers=headers
        ).status_code
        == 403
    )
    body = {
        "version": d["version"],
        "reason": "Reviewed change",
        "changes": {"invoice_number": "edited"},
        "reviewer": "forged-reviewer",
        "client_id": "b",
    }
    response = client.post(
        "/clients/a/documents/" + d["id"] + "/correct", headers=headers, json=body
    )
    assert response.status_code == 200, response.text
    events = [a for a in w.list("a", "audit") if a["action"] == "correct"]
    assert events[-1]["reviewer"] == "actual-reviewer" or any(
        a["reviewer"] == "actual-reviewer" for a in events
    )
    assert response.json()["client_id"] == "a"
    assert (
        client.post(
            "/clients/b/documents/" + d["id"] + "/export",
            headers=headers,
            json={"version": 2, "reason": "attempt", "confirmed": True},
        ).status_code
        == 403
    )


def test_credit_note_correction_cannot_approve_expense_sign(workflow):
    w = workflow
    d = w.ingest("a", document(type="credit_note", direction="credit_note"))
    try:
        d = w.correct(
            "a",
            d["id"],
            {
                "direction": "expense",
                "category": {"account": "5000", "confidence": "1"},
            },
            d["version"],
            "reviewer",
            "Incorrect sign",
        )
    except ValueError:
        return
    with pytest.raises(ValueError):
        w.approve(
            "a", d["id"], d["version"], "reviewer", "Attempt to acknowledge", True
        )


def test_configuration_change_revokes_approval_and_allocations(workflow):
    w = workflow
    d = approved(w)
    w.import_bank("a", [bank()])
    w.settle(
        "a",
        ["bank"],
        [d["id"]],
        {d["id"]: "104"},
        {d["id"]: d["version"]},
        "reviewer",
        "Settlement",
        confirmed=True,
    )
    w.configure(
        "a", {"config": {"category_threshold": ".9"}}, "reviewer", "Update policy"
    )
    updated = w.get("a", "document", d["id"])
    assert updated["approved_version"] is None and updated["version"] > d["version"]
    assert w.list("a", "settlement")[0]["status"] == "invalidated"
    with pytest.raises(ValueError):
        w.export("a", d["id"], d["version"], "reviewer", "Stale export", True)
