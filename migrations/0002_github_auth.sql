-- GitHub credentials are server-side in D1; opaque browser tokens are hashed.
CREATE TABLE IF NOT EXISTS github_sessions (
  token_hash TEXT PRIMARY KEY,
  github_id INTEGER NOT NULL,
  login TEXT NOT NULL,
  access_token TEXT NOT NULL,
  expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS github_sessions_expiry ON github_sessions(expires_at);

CREATE TABLE IF NOT EXISTS github_oauth_states (
  state_hash TEXT PRIMARY KEY,
  verifier TEXT NOT NULL,
  redirect_uri TEXT NOT NULL,
  expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS github_oauth_states_expiry ON github_oauth_states(expires_at);
