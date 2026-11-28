"""Transactional tenant-scoped JSON store. SQLite is LOCAL fallback, not RLS.
PostgreSQL sessions use a non-owner role and FORCE ROW LEVEL SECURITY.
"""

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


class Store:
    def __init__(self, path=None, database_url=None):
        self.url = database_url or os.getenv("DATABASE_URL")
        self.path = str(path or os.getenv("LEDGERLINE_DB", "data/ledgerline.sqlite3"))
        if not self.url:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self.path) as db:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS records (client_id TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(client_id,kind,id))"
                )

    @contextmanager
    def transaction(self, client_id):
        if not client_id or not isinstance(client_id, str):
            raise ValueError("Explicit tenant required")
        if self.url:
            import psycopg

            db = psycopg.connect(self.url)
            try:
                with db.transaction():
                    row = db.execute(
                        "SELECT r.rolsuper,r.rolbypassrls,current_user = pg_get_userbyid(c.relowner) FROM pg_roles r CROSS JOIN pg_class c WHERE r.rolname=current_user AND c.oid='records'::regclass"
                    ).fetchone()
                    if not row or any(row):
                        raise PermissionError(
                            "Application database role must be non-owner, non-superuser and NOBYPASSRLS"
                        )
                    db.execute(
                        "SELECT set_config('app.client_id', %s, true)", (client_id,)
                    )
                    # Serializes all financial mutations per tenant, including allocations/export.
                    db.execute(
                        "SELECT pg_advisory_xact_lock(hashtext(%s))", (client_id,)
                    )
                    yield Session(db, client_id, True)
            finally:
                db.close()
        else:
            db = sqlite3.connect(self.path, timeout=30)
            try:
                db.execute("BEGIN IMMEDIATE")
                yield Session(db, client_id, False)
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()


class Session:
    def __init__(self, db, client_id, postgres):
        self.db, self.client_id, self.postgres = db, client_id, postgres

    def execute(self, sql, params=()):
        return self.db.execute(sql.replace("?", "%s") if self.postgres else sql, params)

    def get(self, kind, identifier):
        row = self.execute(
            "SELECT payload FROM records WHERE client_id=? AND kind=? AND id=?",
            (self.client_id, kind, identifier),
        ).fetchone()
        if not row:
            raise KeyError("Not found in authorized client")
        return row[0] if isinstance(row[0], dict) else json.loads(row[0])

    def list(self, kind):
        rows = self.execute(
            "SELECT payload FROM records WHERE client_id=? AND kind=? ORDER BY id",
            (self.client_id, kind),
        ).fetchall()
        return [r[0] if isinstance(r[0], dict) else json.loads(r[0]) for r in rows]

    def put(self, kind, identifier, value):
        if value.get("client_id") != self.client_id:
            raise PermissionError("Cross-client write rejected")
        self.execute(
            "INSERT INTO records(client_id,kind,id,payload) VALUES(?,?,?,?) ON CONFLICT(client_id,kind,id) DO UPDATE SET payload=excluded.payload",
            (self.client_id, kind, identifier, encoded(value)),
        )

    def audit(self, action, subject, reviewer, reason, before=None, after=None):
        identifier = str(uuid.uuid4())
        self.put(
            "audit",
            identifier,
            {
                "id": identifier,
                "client_id": self.client_id,
                "action": action,
                "subject": subject,
                "reviewer": reviewer,
                "reason": reason,
                "before": before,
                "after": after,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
