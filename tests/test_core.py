from decimal import Decimal

import pytest

from accounting_agent.core import (
    assert_balanced,
    document_status,
    draft_journal,
    duplicate_candidates,
    money,
    reconcile,
    settlement_proposal,
    validate_document,
)


def doc(**changes):
    d = {
        "id": "d1",
        "client_id": "c1",
        "type": "invoice",
        "vendor": "Acme",
        "invoice_number": "I-1",
        "date": "2025-06-01",
        "net": "100.00",
        "tax": "4.00",
        "total": "104.00",
        "tax_rate": "0.04",
        "currency": "USD",
        "line_items": [{"net": "100.00", "tax": "4.00"}],
    }
    d.update(changes)
    return d


def ctx(**changes):
    c = {
        "today": "2025-06-30",
        "known_vendors": ["Acme"],
        "category": {"account": "5000", "confidence": ".99"},
    }
    c.update(changes)
    return c


def rules(d=None, **context):
    return {i["rule_id"] for i in validate_document(d or doc(), ctx(**context))}


def bank(**changes):
    b = {
        "id": "b1",
        "client_id": "c1",
        "date": "2025-06-01",
        "amount": "-104.00",
        "currency": "USD",
        "description": "Acme I-1",
    }
    b.update(changes)
    return b


@pytest.mark.parametrize("value", [1.2, True, None, "NaN", "Infinity", "1.001", "bad"])
def test_invalid_money(value):
    with pytest.raises(ValueError):
        money(value)


def test_decimal_and_balanced():
    assert money("0.10") + money("0.20") == Decimal(".30")
    j = draft_journal(doc(), "5000")
    assert j["lines"] == [
        {"account": "5000", "debit": "104.00", "credit": "0.00"},
        {"account": "2100", "debit": "0.00", "credit": "104.00"},
    ]
    assert assert_balanced(j["lines"])
    for bad in [
        [],
        [{"account": "a", "debit": "1", "credit": "0"}],
        [{"account": "a", "debit": "-1", "credit": "-1"}],
        [{"account": "a", "debit": "1", "credit": "1"}],
    ]:
        with pytest.raises(ValueError):
            assert_balanced(bad)
    with pytest.raises(ValueError):
        draft_journal(doc(total="105"), "5000")
    assert assert_balanced(draft_journal(doc(direction="income"), "4000")["lines"])
    assert assert_balanced(draft_journal(doc(direction="credit_note"), "5000")["lines"])
    assert len(draft_journal(doc(), "5000", recoverable_tax=True)["lines"]) == 3


def test_all_rules_clean_and_each_failure():
    assert rules() == set()
    assert "A01" in rules(existing_documents=[doc(id="copy")])
    assert "A02" in rules(doc(net="99"))
    assert "A02" in rules(doc(line_items=[{"net": "90", "tax": "4"}]))
    assert "A02" in rules(doc(line_items=[{"net": "100", "tax": "3"}]))
    assert "A03" in rules(journal=[{"account": "a", "debit": "1", "credit": "0"}])
    assert "A03" not in rules(journal=draft_journal(doc(), "5000"))
    assert "A04" in rules(lock_date="2025-06-01")
    assert "A04" in rules(doc(date="2026-01-01"))
    assert "A04" in rules(doc(date="invalid"))
    assert "A05" in rules(doc(tax_rate=".2"))
    assert "A05" in rules(doc(tax_rate=None))
    assert "A06" in rules(doc(currency="EUR"))
    assert "A06" not in rules(
        doc(currency="EUR"),
        supported_currencies=["USD", "EUR"],
        fx_rates={"2025-06-01:EUR": "1.10"},
    )
    assert "A06" in rules(
        doc(currency="EUR"),
        supported_currencies=["USD", "EUR"],
        fx_rates={"2025-06-01:EUR": "0"},
    )
    assert "A07" in rules(vendor_history=["20", "25", "30"])
    assert "A07" not in rules(vendor_history=["100"])
    assert "A08" in rules(known_vendors=[])
    assert "A09" in rules(doc(invoice_number=""))
    assert "A10" in rules(category={"account": "5000", "confidence": ".3"})
    assert "A10" in rules(
        category={"account": "5000", "confidence": ".99", "conflicts_history": True}
    )
    assert "A11" in rules(unmatched_bank_lines=[bank()])
    assert "A11" not in rules(unmatched_bank_lines=[bank(date="2025-06-20")])
    assert "A12" in rules(bank=bank(date="2025-01-01"))
    assert "A12" not in rules(bank=bank())
    assert document_status([]) == "Ready"
    assert document_status([{"severity": "Major"}]) == "Needs review"
    assert document_status([{"severity": "Critical"}]) == "Blocked"


def test_duplicates_and_tenant_isolation():
    assert duplicate_candidates(doc(), [doc(id="d2")])
    assert duplicate_candidates(
        doc(hash="abc"), [doc(id="d2", hash="abc", total="999")]
    )
    assert not duplicate_candidates(doc(), [doc(id="d2", invoice_number="different")])
    with pytest.raises(ValueError):
        duplicate_candidates(doc(), [doc(client_id="c2")])
    with pytest.raises(ValueError):
        reconcile(bank(), [doc(client_id="c2")])
    with pytest.raises(ValueError):
        validate_document(doc(), ctx(bank=bank(client_id="c2")))


def test_precision_first_matching():
    assert reconcile(bank(), [doc()])["auto_match"]["document_id"] == "d1"
    assert not reconcile(bank(), [doc(), doc(id="d2")])["auto_match"]
    assert not reconcile(bank(amount="104"), [doc()])["candidates"]
    assert not reconcile(bank(currency="EUR"), [doc()])["candidates"]
    assert not reconcile(bank(amount="-104.01"), [doc()])["candidates"]
    assert not reconcile(bank(date="2025-06-20"), [doc()])["candidates"]
    assert not reconcile(bank(description="Other"), [doc()])["candidates"]
    assert not reconcile(bank(), [doc(settled=True)])["candidates"]
    assert reconcile(bank(description="Acme", reference="I-1"), [doc()])["auto_match"]
    assert reconcile(bank(amount="104"), [doc(direction="credit_note")])["auto_match"]


def test_compound_partial_fees_explicit():
    proposal = settlement_proposal([bank(amount="-208")], [doc(), doc(id="d2")])
    assert proposal["method"] == "compound"
    assert proposal["requires_approval"] and proposal["status"] == "review_required"
    assert assert_balanced(proposal["lines"])
    partial = settlement_proposal([bank(amount="-50")], [doc()])
    assert partial["remaining"] == "54.00" and partial["difference"] == "0.00"
    assert assert_balanced(partial["lines"])
    fee = settlement_proposal([bank(amount="-105")], [doc()], difference_account="6100")
    assert fee["difference"] == "1.00" and fee["lines"][-1]["debit"] == "1.00"
    with pytest.raises(ValueError):
        settlement_proposal([bank(amount="-105")], [doc()])
    with pytest.raises(ValueError):
        settlement_proposal([bank(), bank()], [doc()])
    with pytest.raises(ValueError):
        settlement_proposal([bank(currency="EUR")], [doc()])
    with pytest.raises(ValueError):
        settlement_proposal([bank(amount="104")], [doc()])
    with pytest.raises(ValueError):
        settlement_proposal([bank(client_id="c2")], [doc()])
    assert assert_balanced(
        settlement_proposal(
            [bank(amount="103")], [doc(direction="income")], difference_account="6100"
        )["lines"]
    )


def test_tax_breakdown_math_and_missing_tenant():
    assert "A02" not in rules(
        doc(tax_breakdown=[{"rate": ".04", "amount": "4", "taxable_amount": "100"}])
    )
    assert "A02" in rules(
        doc(tax_breakdown=[{"rate": ".04", "amount": "3", "taxable_amount": "100"}])
    )
    assert "A02" in rules(
        doc(tax_breakdown=[{"rate": ".04", "amount": "4", "taxable_amount": "90"}])
    )
    assert "A09" in rules(doc(client_id=""))


def test_settlement_credit_note_and_mixed_clearing_rejected():
    refund = settlement_proposal([bank(amount="104")], [doc(direction="credit_note")])
    assert refund["lines"][1] == {
        "account": "2100",
        "debit": "0.00",
        "credit": "104.00",
    }
    with pytest.raises(ValueError):
        settlement_proposal(
            [bank(amount="208")],
            [doc(direction="income"), doc(id="d2", direction="credit_note")],
        )
    assert reconcile(bank(matched=True), [doc()])["auto_match"] is None


@pytest.mark.parametrize(
    "bad,expected",
    [
        ({"net": None}, "A02"),
        ({"net": "NaN"}, "A02"),
        ({"total": "-104"}, "A02"),
        ({"date": None}, "A04"),
        ({"tax_rate": "nonsense"}, "A05"),
        ({"tax_rate": "NaN"}, "A05"),
        ({"currency": "JPY"}, "A06"),
        ({"vendor": ""}, "A09"),
        ({"total": None}, "A09"),
    ],
)
def test_malformed_document_rule_branches(bad, expected):
    assert expected in rules(doc(**bad))


@pytest.mark.parametrize(
    "context,expected",
    [
        ({"duplicate": True}, "A01"),
        ({"journal": {"client_id": "foreign", "lines": []}}, "A03"),
        ({"journal": None}, "A03"),
        ({"vendor_history": ["bad"]}, "A07"),
        ({"category": {}}, "A10"),
        ({"category": {"account": "5000", "confidence": "NaN"}}, "A10"),
        ({"category": {"account": "5000", "confidence": "bad"}}, "A10"),
        ({"bank": bank(date="invalid")}, "A12"),
    ],
)
def test_context_invalid_branches(context, expected):
    assert expected in rules(**context)


def test_boundary_rule_branches():
    assert "A04" not in rules(lock_date="2025-05-31", today="2025-06-01")
    assert "A07" not in rules(vendor_history=["34.67"])
    assert "A07" in rules(vendor_history=["34.66"])
    assert "A09" not in rules(doc(type="receipt", invoice_number=""))
    assert "A10" not in rules(category={"account": "5000", "confidence": ".85"})
    assert "A11" not in rules(today="2025-06-15", unmatched_bank_lines=[bank()])
    assert "A11" in rules(today="2025-06-16", unmatched_bank_lines=[bank()])
    assert "A12" not in rules(bank=bank(date="2025-07-31"))
    assert "A12" in rules(bank=bank(date="2025-08-01"))
    assert "A06" in rules(
        doc(currency="EUR"),
        supported_currencies=["USD", "EUR"],
        fx_rates={"2025-06-01:EUR": "bad"},
    )


def test_duplicate_fuzzy_nonmatch_and_missing_values():
    assert (
        duplicate_candidates(
            doc(vendor="Acme Company"), [doc(id="d2", vendor="Acme Compny")]
        )[0]["kind"]
        == "near"
    )
    assert not duplicate_candidates(doc(), [doc(id="d2", total="bad")])
    assert not duplicate_candidates(doc(), [doc()])
    assert not duplicate_candidates(doc(), [doc(id="d2", vendor="Other Vendor")])
    assert not duplicate_candidates(doc(), [doc(id="d2", date="2025-06-04")])


def test_matching_date_and_name_boundaries():
    assert reconcile(bank(date="2025-06-06"), [doc()])["candidates"]
    assert not reconcile(bank(date="2025-06-07"), [doc()])["candidates"]
    assert reconcile(bank(date="2025-06-25"), [doc(due_date="2025-06-25")])[
        "auto_match"
    ]
    assert not reconcile(bank(), [doc(duplicate=True)])["candidates"]
    assert not reconcile(bank(amount="0"), [doc(total="0")])["candidates"]
    assert reconcile(bank(description="Reference I-1"), [doc()])["candidates"]
    assert not reconcile(bank(description="Reference I-10"), [doc()])["candidates"]


def test_settlement_remaining_error_branches():
    for banks, docs in [
        ([], [doc()]),
        ([bank()], []),
        ([bank(matched=True)], [doc()]),
        ([bank()], [doc(settled=True)]),
        ([bank(amount="-208")], [doc(), doc()]),
    ]:
        with pytest.raises(ValueError):
            settlement_proposal(banks, docs)
    short_fee = settlement_proposal(
        [bank(amount="-103")], [doc()], difference_account="5500"
    )
    assert (
        short_fee["remaining"] == "0.00" and short_fee["lines"][-1]["credit"] == "1.00"
    )
    assert document_status([{"severity": "Minor"}]) == "Ready"


def test_extreme_decimal_and_bad_tax_fail_closed():
    with pytest.raises(ValueError):
        money("1e999")
    assert "A02" in rules(
        doc(tax_breakdown=[{"rate": "bad", "amount": "4", "taxable_amount": "100"}])
    )
