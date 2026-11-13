-- Schema für die Arbeitszeiten-Web-App (SQLite)
-- Migration aus Microsoft Access (Arbeitszeiten.accdb)

PRAGMA foreign_keys = ON;

-- Stammdaten der studentischen Hilfskräfte
CREATE TABLE IF NOT EXISTS students (
    id                   INTEGER PRIMARY KEY,
    legacy_zugangswert   INTEGER UNIQUE,        -- alter Access-Schlüssel (Nachvollziehbarkeit)
    first_name           TEXT,
    last_name            TEXT,
    email                TEXT,
    project              TEXT,
    supervisor           TEXT,
    contract_start       TEXT,                  -- ISO YYYY-MM-DD
    contract_end         TEXT,
    monthly_target_hours REAL,                  -- Monatssoll in Stunden (anpassbar)
    active               INTEGER NOT NULL DEFAULT 1,
    is_doctoral          INTEGER NOT NULL DEFAULT 0
);

-- Login-Konten
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    student_id    INTEGER REFERENCES students(id) ON DELETE SET NULL,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'student',   -- student | supervisor | admin
    active        INTEGER NOT NULL DEFAULT 1
);

-- Eine Zeile pro Person und Arbeitstag
CREATE TABLE IF NOT EXISTS time_entries (
    id           INTEGER PRIMARY KEY,
    student_id   INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    work_date    TEXT NOT NULL,                 -- YYYY-MM-DD
    start_at     TEXT,                          -- HH:MM
    end_at       TEXT,                          -- HH:MM
    is_manual    INTEGER NOT NULL DEFAULT 0,
    note         TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    UNIQUE (student_id, work_date)
);

CREATE INDEX IF NOT EXISTS idx_entries_student_date
    ON time_entries (student_id, work_date);

-- Beliebig viele Pausen pro Tag
CREATE TABLE IF NOT EXISTS breaks (
    id          INTEGER PRIMARY KEY,
    entry_id    INTEGER NOT NULL REFERENCES time_entries(id) ON DELETE CASCADE,
    break_start TEXT,                           -- HH:MM
    break_end   TEXT                            -- HH:MM (NULL = Pause läuft)
);

CREATE INDEX IF NOT EXISTS idx_breaks_entry ON breaks (entry_id);

-- Abwesenheiten (Urlaub, Krankheit, Sonstiges)
CREATE TABLE IF NOT EXISTS absences (
    id          INTEGER PRIMARY KEY,
    student_id  INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    type        TEXT NOT NULL,                  -- urlaub | krank | sonstiges
    start_date  TEXT NOT NULL,                  -- YYYY-MM-DD
    end_date    TEXT NOT NULL,                  -- YYYY-MM-DD
    note        TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_absences_student ON absences (student_id, start_date);

-- Revisions-/Änderungsprotokoll (ersetzt tbltracking)
CREATE TABLE IF NOT EXISTS audit_log (
    id            INTEGER PRIMARY KEY,
    ts            TEXT NOT NULL,
    actor_user_id INTEGER,
    entity        TEXT NOT NULL,
    entity_id     INTEGER,
    action        TEXT NOT NULL,
    details       TEXT
);
