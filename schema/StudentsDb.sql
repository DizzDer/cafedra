-- StudentsDb.sqlite3
PRAGMA foreign_keys=ON;

CREATE TABLE students (id TEXT PRIMARY KEY, name TEXT NOT NULL, program TEXT NOT NULL, year INTEGER NOT NULL, supervisor TEXT NOT NULL, email TEXT NOT NULL, status TEXT NOT NULL);
