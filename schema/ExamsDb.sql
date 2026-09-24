-- ExamsDb.sqlite3
PRAGMA foreign_keys=ON;

CREATE TABLE exams (id TEXT PRIMARY KEY, student_id TEXT NOT NULL, subject TEXT NOT NULL, semester INTEGER NOT NULL, attempt INTEGER NOT NULL, exam_date TEXT, grade INTEGER, status TEXT NOT NULL);

CREATE UNIQUE INDEX exam_attempt ON exams(student_id,subject,semester,attempt);
