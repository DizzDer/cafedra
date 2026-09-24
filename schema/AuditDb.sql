-- AuditDb.sqlite3
PRAGMA foreign_keys=ON;

CREATE TABLE assistant_proposals (id TEXT PRIMARY KEY, session_hash TEXT NOT NULL, payload TEXT NOT NULL, expires REAL NOT NULL, result TEXT);

CREATE TABLE audit_events (id TEXT PRIMARY KEY, module TEXT, action TEXT, record_id TEXT, created_at TEXT);

CREATE TABLE documents (id TEXT PRIMARY KEY, number TEXT UNIQUE NOT NULL, kind TEXT NOT NULL, student_id TEXT NOT NULL, created_at TEXT NOT NULL, language TEXT NOT NULL, snapshot TEXT NOT NULL);

CREATE TABLE organization_settings (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
