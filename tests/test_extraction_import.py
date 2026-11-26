import json

import httpx
import pytest

from accounting_agent.bank_import import import_csv, import_ofx
from accounting_agent.extraction import categorize, extract_document
from accounting_agent.providers import Provider, ProviderConfig, ProviderError


def test_extract_evidence(tmp_path):
    p = tmp_path / "invoice.txt"
    p.write_text(
        "Invoice\nVendor: Acme\nInvoice number: INV-1\nDate: 2025-01-01\nCurrency: USD\nNet: 100.00\nTax: 20.00\nTotal: 120.00\n"
    )
    result = extract_document(p, "a")
    assert result["values"]["total"] == "120.00"
    assert all(f["grounded"] for f in result["fields"].values())
    assert not result["issues"]


def test_conflicting_and_missing(tmp_path):
    p = tmp_path / "bad.txt"
    p.write_text("Invoice\nVendor: Acme\nVendor: Evil\nTotal: NaN")
    r = extract_document(p, "a")
    assert r["issues"]
    assert r["fields"]["vendor"]["confidence"] == 0


def test_category_isolation_priority():
    coa = [{"code": "500", "name": "Supplies"}]
    assert categorize("a", {"vendor": "V"}, coa, {"V": "500"})["source"] == "manual"
    h = [{"client_id": "b", "vendor": "V", "account": "500", "approved": True}]
    assert categorize("a", {"vendor": "V"}, coa, approved_history=h)["account"] is None
    h[0]["client_id"] = "a"
    assert (
        categorize("a", {"vendor": "V"}, coa, approved_history=h)["source"]
        == "approved-history"
    )
    h[0]["invalidated"] = True
    assert categorize("a", {"vendor": "V"}, coa, approved_history=h)["account"] is None


def test_provider_tool_free_budget():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        assert "tools" not in body
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"account":"500"}'}}],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 10,
                    "total_tokens": 30,
                },
            },
        )

    p = Provider(
        ProviderConfig(api_key="test", model="mock", client_cost_cap="0.4"),
        httpx.MockTransport(handler),
    )
    r, u = p.complete("a", "categorize", {"client_id": "a"})
    assert u["tokens"] == 30
    with pytest.raises(ProviderError):
        p.complete("a", "test", {"client_id": "b"})
    with pytest.raises(ProviderError):
        for _ in range(10):
            p.complete("a", "test", {"client_id": "a"})
    assert len(calls) <= 3


def test_csv_strict():
    r = import_csv(
        b"date,amount,currency,description\n2025-01-01,-12.34,USD,Acme\n", "a"
    )
    assert r[0]["amount"] == "-12.34"
    for bad in [
        b"date,amount,currency,description\n2025-01-01,NaN,USD,X",
        b"date,date,amount,currency,description\n2025-01-01,2025-01-01,1,USD,X",
        b"date,amount,currency,description\n2025-02-30,1,USD,X",
    ]:
        with pytest.raises(ValueError):
            import_csv(bad, "a")


def test_ofx_safe():
    data = b"<OFX><CURDEF>USD</CURDEF><STMTTRN><DTPOSTED>20250101120000</DTPOSTED><TRNAMT>-1.00</TRNAMT><FITID>abc</FITID><NAME>Acme</NAME></STMTTRN></OFX>"
    assert import_ofx(data, "a")[0]["id"] == "abc"
    with pytest.raises(ValueError):
        import_ofx(b'<!DOCTYPE x [<!ENTITY a "x">]><OFX/>', "a")
    with pytest.raises(ValueError):
        import_ofx(data.replace(b"</STMTTRN>", b""), "a")


def test_provider_persistent_reservations(tmp_path):
    config = ProviderConfig(
        api_key="x",
        model="mock",
        client_cost_cap="0.2",
        budget_db=str(tmp_path / "budget.db"),
    )
    handler = httpx.MockTransport(
        lambda r: httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )
    )
    Provider(config, handler).complete("a", "x", {})
    with pytest.raises(ProviderError):
        Provider(config, handler).complete("a", "x", {})
    Provider(config, handler).complete("b", "x", {})


def test_low_confidence_retry_and_fabricated_evidence(tmp_path):
    p = tmp_path / "sample.txt"
    p.write_text(
        "Invoice\nVendor: Acme Limited\nDate: 2025-01-01\nTotal: 20.00\nCurrency: USD\n"
    )

    class MockProvider:
        calls = 0

        def complete(self, client_id, task, payload):
            self.calls += 1
            fields = {
                k: {
                    "value": v,
                    "page": 1,
                    "confidence": 0.1 if self.calls == 1 else 0.99,
                    "evidence": e,
                }
                for k, v, e in [
                    ("vendor", "Acme", "Vendor: Acme Limited"),
                    ("date", "2025-01-01", "Date: 2025-01-01"),
                    ("total", "20.00", "Total: 20.00"),
                    ("currency", "USD", "Currency: USD"),
                ]
            }
            return {"type": "invoice", "fields": fields}, {
                "cost": "0.01",
                "tokens": 1,
                "calls": 1,
            }

    provider = MockProvider()
    r = extract_document(p, "a", backend="llm", provider=provider)
    assert provider.calls == 2
    assert r["usage"]["cost"] == "0.02"
    assert not r["fields"]["vendor"]["grounded"]
