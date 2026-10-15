CREATE TABLE IF NOT EXISTS users (
  google_sub TEXT PRIMARY KEY,
  email TEXT NOT NULL,
  first_signed_in INTEGER NOT NULL,
  last_signed_in INTEGER NOT NULL,
  sign_in_count INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  google_sub TEXT NOT NULL REFERENCES users(google_sub) ON DELETE CASCADE,
  expires_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS sessions_expires_at_idx ON sessions(expires_at);
