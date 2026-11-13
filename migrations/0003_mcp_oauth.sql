CREATE TABLE IF NOT EXISTS mcp_oauth_clients (
  client_id TEXT PRIMARY KEY,
  redirect_uris TEXT NOT NULL UNIQUE,
  expires_at INTEGER
);
CREATE INDEX IF NOT EXISTS mcp_oauth_clients_expiry ON mcp_oauth_clients(expires_at);

CREATE TABLE IF NOT EXISTS mcp_oauth_states (
  state_hash TEXT PRIMARY KEY,
  client_id TEXT NOT NULL,
  redirect_uri TEXT NOT NULL,
  client_state TEXT NOT NULL,
  challenge TEXT NOT NULL,
  github_verifier TEXT NOT NULL,
  private_consent INTEGER,
  issuer TEXT NOT NULL,
  expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS mcp_oauth_states_expiry ON mcp_oauth_states(expires_at);

CREATE TABLE IF NOT EXISTS mcp_oauth_grants (
  grant_id TEXT PRIMARY KEY,
  code_hash TEXT UNIQUE,
  access_hash TEXT UNIQUE,
  refresh_hash TEXT UNIQUE,
  client_id TEXT NOT NULL,
  redirect_uri TEXT NOT NULL,
  challenge TEXT NOT NULL,
  issuer TEXT NOT NULL,
  github_id INTEGER NOT NULL,
  github_token TEXT NOT NULL,
  private_consent INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  code_expires_at INTEGER NOT NULL,
  access_expires_at INTEGER
);
CREATE INDEX IF NOT EXISTS mcp_oauth_grants_expiry ON mcp_oauth_grants(expires_at);
CREATE INDEX IF NOT EXISTS mcp_oauth_grants_github ON mcp_oauth_grants(github_id);

CREATE TABLE IF NOT EXISTS mcp_oauth_spent_refresh (
  refresh_hash TEXT PRIMARY KEY,
  grant_id TEXT NOT NULL REFERENCES mcp_oauth_grants(grant_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS mcp_oauth_spent_grant ON mcp_oauth_spent_refresh(grant_id);
CREATE TRIGGER IF NOT EXISTS mcp_oauth_remember_refresh
AFTER UPDATE OF refresh_hash ON mcp_oauth_grants
WHEN OLD.refresh_hash IS NOT NULL
BEGIN
  INSERT INTO mcp_oauth_spent_refresh VALUES (OLD.refresh_hash, OLD.grant_id);
END;
