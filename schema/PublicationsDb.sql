-- PublicationsDb.sqlite3
PRAGMA foreign_keys=ON;

CREATE TABLE authors (publication_id TEXT REFERENCES publications(id) ON DELETE CASCADE, student_id TEXT NOT NULL, PRIMARY KEY(publication_id,student_id));

CREATE TABLE publications (id TEXT PRIMARY KEY, title TEXT NOT NULL, journal TEXT NOT NULL, year INTEGER NOT NULL, doi TEXT, vak INTEGER NOT NULL DEFAULT 0, scopus INTEGER NOT NULL DEFAULT 0, verified INTEGER NOT NULL DEFAULT 0);

CREATE UNIQUE INDEX unique_doi ON publications(doi) WHERE doi IS NOT NULL AND doi <> '';
