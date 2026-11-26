"""Opt-in REAL Postgres policy test; never substituted by SQLite filtering."""

import os

import pytest


@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="Real PostgreSQL nonowner TEST_DATABASE_URL not provided",
)
def test_actual_rls_read_write_and_missing_context():
    import uuid

    import psycopg

    from accounting_agent.store import Store

    url = os.environ["TEST_DATABASE_URL"]
    store = Store(database_url=url)
    ident = str(uuid.uuid4())
    with store.transaction("rls-a") as tx:
        tx.put("test", ident, {"id": ident, "client_id": "rls-a"})
    with store.transaction("rls-b") as tx:
        # Deliberately bypass repository tenant predicate: DB itself must filter.
        assert (
            tx.db.execute(
                "SELECT payload FROM records WHERE id=%s", (ident,)
            ).fetchall()
            == []
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            tx.db.execute(
                "INSERT INTO records(client_id,kind,id,payload) VALUES('rls-a','test',%s,%s)",
                (ident + "x", '{"client_id":"rls-a"}'),
            )
    with psycopg.connect(url) as db:
        assert (
            db.execute("SELECT payload FROM records WHERE id=%s", (ident,)).fetchall()
            == []
        )
    with store.transaction("rls-a") as tx:
        tx.db.execute("DELETE FROM records WHERE id=%s", (ident,))
