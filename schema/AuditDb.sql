-- AuditDb.sqlite3
PRAGMA foreign_keys=ON;

CREATE TABLE audit_events (id TEXT PRIMARY KEY, module TEXT, action TEXT, record_id TEXT, created_at TEXT);
