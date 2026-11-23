-- Per-user Linear OAuth grants (multi-workspace: one row per account_key + workspace org_slug).
CREATE TABLE IF NOT EXISTS linear_grants (
  account_key TEXT NOT NULL,
  org_slug TEXT NOT NULL,
  org_name TEXT NOT NULL,
  access_token TEXT NOT NULL,
  expires_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (account_key, org_slug)
);
CREATE INDEX IF NOT EXISTS linear_grants_expiry ON linear_grants(expires_at);

CREATE TABLE IF NOT EXISTS linear_oauth_states (
  state_hash TEXT PRIMARY KEY,
  account_key TEXT NOT NULL,
  verifier TEXT NOT NULL,
  redirect_uri TEXT NOT NULL,
  return_to TEXT NOT NULL DEFAULT '/',
  expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS linear_oauth_states_expiry ON linear_oauth_states(expires_at);
