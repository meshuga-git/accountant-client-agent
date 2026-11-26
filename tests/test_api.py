import pytest
from fastapi.testclient import TestClient

from accounting_agent import api
from accounting_agent.service import Workflow
from accounting_agent.store import Store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "workflow", Workflow(Store(tmp_path / "test.sqlite")))
    monkeypatch.setattr(api, "STORAGE", tmp_path / "uploads")
    with TestClient(api.app) as c:
        yield c


def auth(token="demo-accountant-token"):
    return {"Authorization": "Bearer " + token}


def test_auth_and_health(client):
    assert client.get("/health").json()["posting"] == "disabled"
    assert client.get("/clients").status_code == 401
    assert len(client.get("/clients", headers=auth("demo-cafe-token")).json()) == 1
    assert (
        client.get(
            "/clients/designer/documents", headers=auth("demo-cafe-token")
        ).status_code
        == 403
    )
    assert (
        client.get("/clients/cafe/documents/foreign", headers=auth()).status_code == 404
    )


def test_end_to_end_and_lock_recheck(client):
    raw = b"Invoice\nVendor: Demo Supply\nInvoice number: X-123\nDate: 2025-10-10\nNet: 100.00\nTax: 8.00\nTax rate: 0.08\nTotal: 108.00\nCurrency: USD\n"
    r = client.post(
        "/clients/cafe/upload",
        headers=auth(),
        files={"file": ("invoice.txt", raw, "text/plain")},
    )
    assert r.status_code == 200
    # The worker runs asynchronously; deterministic join for test only.
    import time

    for _ in range(100):
        job = client.get("/clients/cafe/jobs/" + r.json()["id"], headers=auth()).json()
        if job["status"] in ("complete", "failed"):
            break
        time.sleep(0.01)
    assert job["status"] == "complete", job
    prefix = "/clients/cafe/documents/" + job["document_id"]
    assert client.get(prefix, headers=auth()).status_code == 200
    r = client.post(
        prefix + "/correct",
        headers=auth(),
        json={
            "version": 1,
            "reason": "Reviewed source",
            "changes": {
                "category": {
                    "account": "5000",
                    "confidence": "1",
                    "rationale": "Supplies",
                    "source": "manual",
                }
            },
        },
    )
    assert r.status_code == 200
    assert (
        client.post(
            prefix + "/approve",
            headers=auth(),
            json={
                "version": 2,
                "reason": "Vendor and source verified",
                "acknowledge_issues": True,
            },
        ).status_code
        == 200
    )
    body = {"version": 2, "reason": "Confirmed local mock", "confirmed": True}
    export = client.post(prefix + "/export", headers=auth(), json=body)
    assert export.status_code == 200
    assert (
        client.post(prefix + "/export", headers=auth(), json=body).json()
        == export.json()
    )
    client.post(
        "/clients/cafe/configuration",
        headers=auth(),
        json={"changes": {"lock_date": "2025-12-31"}, "reason": "Close period"},
    )
    assert client.post(prefix + "/export", headers=auth(), json=body).status_code == 409
    assert (
        client.get(
            "/clients/designer/documents/" + job["document_id"], headers=auth()
        ).status_code
        == 404
    )


def test_configuration_invalidates_approval(client):
    w = api.workflow
    d = w.ingest(
        "cafe",
        {
            "vendor": "X",
            "date": "2025-10-01",
            "invoice_number": "X",
            "net": "100",
            "tax": "8",
            "total": "108",
            "tax_rate": ".08",
            "currency": "USD",
        },
    )
    d = w.correct(
        "cafe",
        d["id"],
        {"category": {"account": "5000", "confidence": "1"}},
        1,
        "r",
        "review",
    )
    w.approve("cafe", d["id"], 2, "r", "review all", True)
    w.configure("cafe", {"config": {"allowed_tax_rates": ["0"]}}, "r", "change rates")
    current = w.get("cafe", "document", d["id"])
    assert current["approved_version"] is None and current["version"] == 3
    with pytest.raises(ValueError):
        w.export("cafe", d["id"], 3, "r", "export", True)
