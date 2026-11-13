"""
Kleines Verwaltungs-CLI für die Arbeitszeiten-App.

    python3 manage.py list
    python3 manage.py setpass <username> <passwort>
    python3 manage.py addstudent <username> <vorname> <nachname> [monatssoll]
    python3 manage.py addadmin <username> <passwort>
"""
from __future__ import annotations

import sys

from werkzeug.security import generate_password_hash

import app as A


def main(argv):
    if not argv:
        print(__doc__)
        return
    A.init_db()
    db = A.get_db() if False else None
    import sqlite3
    con = sqlite3.connect(A.DB_PATH)
    con.row_factory = sqlite3.Row
    cmd = argv[0]

    def users():
        return con.execute(
            "SELECT u.username, u.role, s.first_name, s.last_name FROM users u"
            " LEFT JOIN students s ON s.id = u.student_id ORDER BY u.role, u.username"
        ).fetchall()

    if cmd == "list":
        for u in users():
            name = f"{u['last_name'] or ''}, {u['first_name'] or ''}".strip(", ")
            print(f"  {u['username']:<16} {u['role']:<11} {name}")

    elif cmd == "setpass" and len(argv) == 3:
        cur = con.execute("UPDATE users SET password_hash=? WHERE username=?",
                          (generate_password_hash(argv[2]), argv[1]))
        con.commit()
        print("OK" if cur.rowcount else "Benutzer nicht gefunden.")

    elif cmd == "addstudent" and len(argv) >= 4:
        target = float(argv[4]) if len(argv) > 4 else None
        cur = con.execute(
            "INSERT INTO students (first_name,last_name,monthly_target_hours,active)"
            " VALUES (?,?,?,1)", (argv[2], argv[3], target),
        )
        con.execute(
            "INSERT INTO users (student_id,username,password_hash,role) VALUES (?,?,?,?)",
            (cur.lastrowid, argv[1], generate_password_hash(argv[1] + "123"), "student"),
        )
        con.commit()
        print(f"Angelegt: {argv[1]} (Startpasswort '{argv[1]}123')")

    elif cmd == "addadmin" and len(argv) == 3:
        con.execute("INSERT INTO users (username,password_hash,role) VALUES (?,?, 'admin')",
                    (argv[1], generate_password_hash(argv[2])))
        con.commit()
        print("Admin angelegt.")

    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
