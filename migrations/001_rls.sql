-- Execute with database-owner migration credentials, NEVER app credentials.
-- Use this only for synthetic local data; replace password outside demo.
CREATE ROLE ledgerline_app LOGIN PASSWORD 'synthetic-local-only' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
CREATE TABLE records (
 client_id text NOT NULL,
 kind text NOT NULL,
 id text NOT NULL,
 payload jsonb NOT NULL,
 PRIMARY KEY(client_id,kind,id),
 CHECK (payload->>'client_id' = client_id)
);
ALTER TABLE records ENABLE ROW LEVEL SECURITY;
ALTER TABLE records FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON records
 USING (client_id = current_setting('app.client_id',true))
 WITH CHECK (client_id = current_setting('app.client_id',true));
GRANT SELECT,INSERT,UPDATE,DELETE ON records TO ledgerline_app;
-- Non-owner application role cannot alter policies or SET ROLE to owner.
