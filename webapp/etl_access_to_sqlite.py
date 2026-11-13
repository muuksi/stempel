"""
Migration: Microsoft Access (Arbeitszeiten.accdb) -> SQLite der Web-App.

- liest die Tabellen mit `mdbtools` (mdb-export)
- korrigiert das Access-Zeitformat (1899-12-30 HH:MM) zu HH:MM
- kombiniert Datum (Tagdatum) + Uhrzeiten
- legt für jede Person ein Login-Konto an (Zufallspasswort)

Aufruf:
    python3 etl_access_to_sqlite.py "/pfad/Arbeitszeiten.accdb"
"""
from __future__ import annotations

import csv
import io
import os
import secrets
import sqlite3
import subprocess
import sys
from datetime import datetime

from werkzeug.security import generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
DB_PATH = os.path.join(INSTANCE_DIR, "arbeitszeiten.sqlite")
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")

os.makedirs(INSTANCE_DIR, exist_ok=True)


def mdb_export(accdb: str, table: str) -> list[dict]:
    out = subprocess.check_output(["mdb-export", accdb, table])
    return list(csv.DictReader(io.StringIO(out.decode("utf-8", "replace"))))


def parse_date(value: str | None) -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%m/%d/%y %H:%M:%S", "%m/%d/%y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_hhmm(value: str | None) -> str | None:
    """Access speichert Uhrzeiten als 1899-12-30 HH:MM:SS -> 'HH:MM'."""
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%m/%d/%y %H:%M:%S", "%m/%d/%y %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).strftime("%H:%M")
        except ValueError:
            continue
    return None


def make_username(first: str, last: str, taken: set[str]) -> str:
    first = (first or "x").strip().lower()
    last = (last or "y").strip().lower().replace(" ", "")
    base = (first[:1] + last) or "user"
    username, i = base, 2
    while username in taken:
        username = f"{base}{i}"
        i += 1
    taken.add(username)
    return username


def main(accdb: str) -> None:
    print(f"Lese Access-Datei: {accdb}")
    students = mdb_export(accdb, "Studenten")
    zeiten = mdb_export(accdb, "Zeiten")

    # Zielschema frisch anlegen
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    db = sqlite3.connect(DB_PATH)
    db.executescript(open(SCHEMA_PATH).read())

    taken: set[str] = set()
    credentials: list[tuple[str, str]] = []
    legacy_to_id: dict[str, int] = {}

    for s in students:
        legacy = int(s["ZugangWert"]) if s.get("ZugangWert", "").strip() else None
        first = (s.get("Vorname") or "").strip()
        last = (s.get("Name") or "").strip()
        cur = db.execute(
            "INSERT INTO students (legacy_zugangswert, first_name, last_name, email,"
            " project, supervisor, contract_start, contract_end, monthly_target_hours,"
            " active, is_doctoral) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                legacy, first, last, (s.get("E-Mail") or "").strip() or None,
                (s.get("Projekt") or "").strip() or None,
                (s.get("Betreuer") or "").strip() or None,
                parse_date(s.get("Vertragsbeginn")), parse_date(s.get("Vertragsende")),
                float(s["RefSt"]) if (s.get("RefSt") or "").strip() else None,
                int(s.get("Aktiv") or 0), int(s.get("Doktorand") or 0),
            ),
        )
        sid = cur.lastrowid
        if legacy is not None:
            legacy_to_id[str(legacy)] = sid
        if first or last:
            username = make_username(first, last, taken)
            pw = secrets.token_urlsafe(6)
            db.execute(
                "INSERT INTO users (student_id, username, password_hash, role)"
                " VALUES (?,?,?,?)",
                (sid, username, generate_password_hash(pw), "student"),
            )
            credentials.append((f"{last}, {first}", username, pw))

    imported = skipped = 0
    for z in zeiten:
        sid = legacy_to_id.get((z.get("ZugangWert") or "").strip())
        work_date = parse_date(z.get("Tagdatum"))
        if sid is None or work_date is None:
            skipped += 1
            continue
        start = parse_hhmm(z.get("Tagbeginn"))
        bs = parse_hhmm(z.get("Tagmitte"))
        be = parse_hhmm(z.get("Tagweiter"))
        end = parse_hhmm(z.get("Tagende"))
        # Pause nur übernehmen, wenn sie sich tatsächlich unterscheidet
        if bs == be:
            bs = be = None
        if start is None and end is None:
            skipped += 1
            continue
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            cur = db.execute(
                "INSERT INTO time_entries (student_id, work_date, start_at,"
                " end_at, is_manual, created_at, updated_at)"
                " VALUES (?,?,?,?,1,?,?)",
                (sid, work_date, start, end, now, now),
            )
            if bs and be:
                db.execute(
                    "INSERT INTO breaks (entry_id, break_start, break_end) VALUES (?,?,?)",
                    (cur.lastrowid, bs, be),
                )
            imported += 1
        except sqlite3.IntegrityError:
            skipped += 1

    db.commit()
    n_students = db.execute("SELECT COUNT(*) FROM students").fetchone()[0]
    n_entries = db.execute("SELECT COUNT(*) FROM time_entries").fetchone()[0]
    db.close()

    print(f"\nImport abgeschlossen: {n_students} Personen, {n_entries} Zeiteinträge "
          f"({skipped} übersprungen).")
    print("\nZugangsdaten (bitte verteilen und Passwörter ändern lassen):")
    print("-" * 58)
    for name, user, pw in credentials:
        print(f"  {name:<28} {user:<14} {pw}")
    print("-" * 58)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Aufruf: python3 etl_access_to_sqlite.py <Arbeitszeiten.accdb>")
        sys.exit(1)
    main(sys.argv[1])
