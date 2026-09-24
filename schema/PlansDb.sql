-- PlansDb.sqlite3
PRAGMA foreign_keys=ON;

CREATE TABLE plan_items (id TEXT PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE, activity TEXT NOT NULL, due_date TEXT, status TEXT NOT NULL, source_type TEXT, source_id TEXT);

CREATE TABLE plans (id TEXT PRIMARY KEY, student_id TEXT NOT NULL, academic_year TEXT NOT NULL, version INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);

CREATE UNIQUE INDEX plan_version ON plans(student_id,academic_year,version);
