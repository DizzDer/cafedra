-- AttestationDb.sqlite3
PRAGMA foreign_keys=ON;

CREATE TABLE attestations (id TEXT PRIMARY KEY, student_id TEXT NOT NULL, academic_year TEXT NOT NULL, due_date TEXT NOT NULL, status TEXT NOT NULL, result TEXT);

CREATE TABLE notifications (id TEXT PRIMARY KEY, attestation_id TEXT REFERENCES attestations(id) ON DELETE CASCADE, threshold INTEGER NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL, is_read INTEGER NOT NULL DEFAULT 0, UNIQUE(attestation_id,threshold));

CREATE UNIQUE INDEX yearly_attestation ON attestations(student_id,academic_year);
