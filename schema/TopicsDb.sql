-- TopicsDb.sqlite3
PRAGMA foreign_keys=ON;

CREATE TABLE topic_history (id TEXT PRIMARY KEY, topic_id TEXT NOT NULL REFERENCES topics(id) ON DELETE CASCADE, old_title TEXT, new_title TEXT, changed_at TEXT);

CREATE TABLE topics (id TEXT PRIMARY KEY, student_id TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL, approved_on TEXT);

CREATE UNIQUE INDEX one_topic ON topics(student_id);
