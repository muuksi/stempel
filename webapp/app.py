"""
Arbeitszeiten-Web-App — Flask + SQLite.

Start:
    python3 app.py                 # http://127.0.0.1:5057
    HOST=0.0.0.0 PORT=8000 python3 app.py   # für den Server
"""
from __future__ import annotations

import calendar
import csv
import io
import os
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (
    Flask, g, redirect, render_template, request, session, url_for,
    flash, Response, abort,
)
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
DB_PATH = os.path.join(INSTANCE_DIR, "arbeitszeiten.sqlite")
SECRET_PATH = os.path.join(INSTANCE_DIR, "secret_key")
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")

os.makedirs(INSTANCE_DIR, exist_ok=True)

ABSENCE_TYPES = {"urlaub": "Urlaub", "krank": "Krankheit", "sonstiges": "Sonstiges"}
STAFF_ROLES = ("admin", "supervisor")


def _load_secret_key() -> str:
    if os.path.exists(SECRET_PATH):
        return open(SECRET_PATH).read().strip()
    key = secrets.token_hex(32)
    with open(SECRET_PATH, "w") as fh:
        fh.write(key)
    os.chmod(SECRET_PATH, 0o600)
    return key


app = Flask(__name__)
app.config.update(
    SECRET_KEY=_load_secret_key(),
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)


# --------------------------------------------------------------------------- #
# Datenbank
# --------------------------------------------------------------------------- #
def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    db = sqlite3.connect(DB_PATH)
    db.executescript(open(SCHEMA_PATH).read())
    has_admin = db.execute(
        "SELECT COUNT(*) FROM users WHERE role = 'admin'"
    ).fetchone()[0]
    if has_admin == 0:
        pw = os.environ.get("ADMIN_PASSWORD") or secrets.token_urlsafe(9)
        db.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?,?,?)",
            ("admin", generate_password_hash(pw), "admin"),
        )
        db.commit()
        print("=" * 60)
        print(f"  Admin-Konto angelegt:  Benutzer 'admin'  Passwort '{pw}'")
        print("=" * 60)
    db.close()


def audit(actor_user_id, entity, entity_id, action, details=""):
    get_db().execute(
        "INSERT INTO audit_log (ts, actor_user_id, entity, entity_id, action, details)"
        " VALUES (?,?,?,?,?,?)",
        (now_iso(), actor_user_id, entity, entity_id, action, details),
    )


# --------------------------------------------------------------------------- #
# Zeit-Helfer
# --------------------------------------------------------------------------- #
def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def now_hhmm() -> str:
    return datetime.now().strftime("%H:%M")


def today_iso() -> str:
    return date.today().isoformat()


def hhmm_to_min(s):
    if not s:
        return None
    try:
        h, m = s.split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def entry_minutes(entry, breaks) -> int | None:
    start, end = hhmm_to_min(entry["start_at"]), hhmm_to_min(entry["end_at"])
    if start is None or end is None:
        return None
    total = end - start
    for b in breaks:
        bs, be = hhmm_to_min(b["break_start"]), hhmm_to_min(b["break_end"])
        if bs is not None and be is not None:
            total -= (be - bs)
    return total


def fmt_hours(minutes) -> str:
    if minutes is None:
        return "—"
    sign = "-" if minutes < 0 else ""
    minutes = abs(int(minutes))
    return f"{sign}{minutes // 60}:{minutes % 60:02d} h"


def month_working_days(year: int, month: int) -> int:
    n, d = 0, date(year, month, 1)
    while d.month == month:
        n += d.weekday() < 5
        d += timedelta(days=1)
    return n


def absence_working_days(student_id, year, month) -> int:
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    rows = get_db().execute(
        "SELECT * FROM absences WHERE student_id=? AND start_date<=? AND end_date>=?",
        (student_id, last.isoformat(), first.isoformat()),
    ).fetchall()
    days = 0
    for r in rows:
        s = max(datetime.strptime(r["start_date"], "%Y-%m-%d").date(), first)
        e = min(datetime.strptime(r["end_date"], "%Y-%m-%d").date(), last)
        d = s
        while d <= e:
            days += d.weekday() < 5
            d += timedelta(days=1)
    return days


def monthly_overview(entry_rows, student, year, month) -> dict:
    ist_min = sum(entry_minutes(r, r["breaks"]) or 0 for r in entry_rows)
    nom = month_working_days(year, month)
    target = student["monthly_target_hours"] if student else None
    daily = (target / nom) if (target and nom) else None
    abs_days = absence_working_days(student["id"], year, month) if student else 0
    abs_hours = (abs_days * daily) if daily else 0.0
    ist_hours = ist_min / 60
    saldo = (ist_hours + abs_hours - target) if target else None
    return {"target": target, "ist_hours": ist_hours, "abs_hours": abs_hours,
            "abs_days": abs_days, "saldo": saldo, "ist_min": ist_min}


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if current_user() is None:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


def role_required(*roles):
    def deco(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            user = current_user()
            if user is None:
                return redirect(url_for("login"))
            if user["role"] not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapper
    return deco


def is_staff(user) -> bool:
    return bool(user and user["role"] in STAFF_ROLES)


# --------------------------------------------------------------------------- #
# Eintrags-Helfer
# --------------------------------------------------------------------------- #
def get_open_entry(student_id):
    return get_db().execute(
        "SELECT * FROM time_entries WHERE student_id = ? AND end_at IS NULL"
        " ORDER BY work_date DESC LIMIT 1", (student_id,),
    ).fetchone()


def get_today_entry(student_id):
    return get_db().execute(
        "SELECT * FROM time_entries WHERE student_id = ? AND work_date = ?",
        (student_id, today_iso()),
    ).fetchone()


def breaks_for(entry_id):
    return get_db().execute(
        "SELECT * FROM breaks WHERE entry_id = ? ORDER BY break_start", (entry_id,)
    ).fetchall()


def attach_breaks(rows):
    result = []
    for r in rows:
        br = breaks_for(r["id"])
        d = dict(r)
        d["breaks"] = br
        d["minutes"] = entry_minutes(r, br)
        result.append(d)
    return result


def student_of(user):
    return user["student_id"] if user else None


def all_students(only_active=False):
    q = "SELECT * FROM students"
    if only_active:
        q += " WHERE active = 1"
    q += " ORDER BY last_name, first_name"
    return get_db().execute(q).fetchall()


def students_with_login():
    return get_db().execute(
        "SELECT s.*, u.username FROM students s"
        " LEFT JOIN users u ON u.student_id = s.id"
        " ORDER BY s.last_name, s.first_name"
    ).fetchall()


def get_target_sid(user):
    """Zielperson: eigenes Konto oder (für Staff) die ausgewählte Person."""
    if is_staff(user):
        val = request.values.get("student_id") or request.args.get("student_id")
        return int(val) if val and val.isdigit() else None
    return user["student_id"]


app.jinja_env.globals.update(current_user=current_user, fmt_hours=fmt_hours,
                             is_staff=is_staff)


# --------------------------------------------------------------------------- #
# Anwesenheits-Board + Kalender (für Staff)
# --------------------------------------------------------------------------- #
def board_today():
    """Status jeder aktiven Person für heute."""
    today = today_iso()
    db = get_db()
    absences = {
        r["student_id"]: r for r in db.execute(
            "SELECT * FROM absences WHERE start_date<=? AND end_date>=?", (today, today)
        ).fetchall()
    }
    out = []
    for s in all_students(only_active=True):
        entry = db.execute(
            "SELECT * FROM time_entries WHERE student_id=? AND work_date=?",
            (s["id"], today),
        ).fetchone()
        if s["id"] in absences:
            a = absences[s["id"]]
            out.append({"student": s, "key": "abwesend",
                        "label": ABSENCE_TYPES.get(a["type"], a["type"])})
            continue
        if entry is None:
            out.append({"student": s, "key": "nicht", "label": "nicht eingestempelt"})
            continue
        br = breaks_for(entry["id"])
        if entry["end_at"]:
            out.append({"student": s, "key": "feierabend",
                        "label": f"Feierabend ({fmt_hours(entry_minutes(entry, br))})"})
        elif any(b["break_end"] is None for b in br):
            out.append({"student": s, "key": "pause",
                        "label": f"Pause seit {br[-1]['break_start']}"})
        else:
            out.append({"student": s, "key": "anwesend",
                        "label": f"anwesend seit {entry['start_at']}"})
    return out


def month_grid(year, month):
    """Matrix Personen x Tage mit Stunden und Abwesenheiten."""
    db = get_db()
    students = all_students(only_active=True)
    ndays = calendar.monthrange(year, month)[1]
    prefix = f"{year:04d}-{month:02d}-"

    entries = attach_breaks(db.execute(
        "SELECT * FROM time_entries WHERE work_date LIKE ?", (prefix + "%",)
    ).fetchall())
    grid = {s["id"]: [{"hours": None, "abs": None} for _ in range(ndays)] for s in students}
    sid_set = set(grid)
    for e in entries:
        if e["student_id"] in sid_set and e["minutes"] is not None:
            day = int(e["work_date"][8:10])
            grid[e["student_id"]][day - 1]["hours"] = e["minutes"] / 60

    first = date(year, month, 1)
    last = date(year, month, ndays)
    abs_rows = db.execute(
        "SELECT * FROM absences WHERE start_date<=? AND end_date>=?",
        (last.isoformat(), first.isoformat()),
    ).fetchall()
    for a in abs_rows:
        if a["student_id"] not in sid_set:
            continue
        s = max(datetime.strptime(a["start_date"], "%Y-%m-%d").date(), first)
        e = min(datetime.strptime(a["end_date"], "%Y-%m-%d").date(), last)
        d = s
        while d <= e:
            grid[a["student_id"]][d.day - 1]["abs"] = a["type"]
            d += timedelta(days=1)

    days = [{"day": d, "weekday": date(year, month, d).weekday(),
             "weekend": date(year, month, d).weekday() >= 5}
            for d in range(1, ndays + 1)]
    return students, days, grid


def shift_month(year, month, delta):
    m = month + delta
    y = year + (m - 1) // 12
    m = (m - 1) % 12 + 1
    return y, m


# --------------------------------------------------------------------------- #
# Routen
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    if current_user() is None:
        return redirect(url_for("login"))
    return redirect(url_for("admin") if is_staff(current_user()) else url_for("punch"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        user = get_db().execute(
            "SELECT * FROM users WHERE lower(username) = ?", (username,)
        ).fetchone()
        if user and user["active"] and check_password_hash(user["password_hash"], password):
            session.permanent = bool(request.form.get("remember"))
            session["user_id"] = user["id"]
            audit(user["id"], "session", None, "login")
            dest = url_for("admin") if is_staff(user) else url_for("punch")
            return redirect(request.args.get("next") or dest)
        flash("Benutzername oder Passwort falsch.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    user = current_user()
    if user:
        audit(user["id"], "session", None, "logout")
    session.clear()
    return redirect(url_for("login"))


# ---------------------------- Stempeluhr (Studierende) --------------------- #
@app.route("/punch")
@login_required
def punch():
    user = current_user()
    sid = student_of(user)
    if sid is None:
        return redirect(url_for("admin"))          # Staff stempelt nicht
    entry = get_open_entry(sid)
    state, breaks = "idle", []
    if entry:
        breaks = breaks_for(entry["id"])
        state = "paused" if any(b["break_end"] is None for b in breaks) else "working"
    stale_date = entry["work_date"] if entry and entry["work_date"] != today_iso() else None
    recent = attach_breaks(get_db().execute(
        "SELECT * FROM time_entries WHERE student_id = ? ORDER BY work_date DESC LIMIT 10",
        (sid,)).fetchall())
    worked = fmt_hours(entry_minutes(entry, breaks)) if entry else "—"
    return render_template("punch.html", user=user, entry=entry, state=state,
                           breaks=breaks, stale_date=stale_date, recent=recent, worked=worked)


@app.route("/punch/<action>", methods=["POST"])
@login_required
def punch_action(action):
    user = current_user()
    sid = student_of(user)
    if sid is None:
        flash("Staff-Konten stempeln nicht.", "error")
        return redirect(url_for("admin"))
    db, entry, now = get_db(), get_open_entry(sid), now_hhmm()

    if action == "start":
        if entry:
            flash("Es läuft noch ein offener Eintrag — bitte zuerst beenden.", "error")
        else:
            cur = db.execute(
                "INSERT INTO time_entries (student_id, work_date, start_at, is_manual,"
                " created_at, updated_at) VALUES (?,?,?,0,?,?)",
                (sid, today_iso(), now, now_iso(), now_iso()))
            audit(user["id"], "time_entry", cur.lastrowid, "insert", f"start={now}")
            flash(f"Losgelegt um {now} Uhr.", "ok")
    elif action == "break":
        if not entry:
            flash("Kein laufender Eintrag.", "error")
        else:
            ob = db.execute("SELECT * FROM breaks WHERE entry_id=? AND break_end IS NULL",
                            (entry["id"],)).fetchone()
            if ob:
                db.execute("UPDATE breaks SET break_end=? WHERE id=?", (now, ob["id"]))
                audit(user["id"], "break", ob["id"], "update", f"end={now}")
                flash(f"Pause beendet um {now} Uhr.", "ok")
            else:
                cur = db.execute("INSERT INTO breaks (entry_id, break_start) VALUES (?,?)",
                                 (entry["id"], now))
                audit(user["id"], "break", cur.lastrowid, "insert", f"start={now}")
                flash(f"Pause ab {now} Uhr.", "ok")
    elif action == "end":
        if not entry:
            flash("Kein laufender Eintrag.", "error")
        else:
            ob = db.execute("SELECT * FROM breaks WHERE entry_id=? AND break_end IS NULL",
                            (entry["id"],)).fetchone()
            if ob:
                db.execute("UPDATE breaks SET break_end=? WHERE id=?", (now, ob["id"]))
            db.execute("UPDATE time_entries SET end_at=?, updated_at=? WHERE id=?",
                       (now, now_iso(), entry["id"]))
            audit(user["id"], "time_entry", entry["id"], "update", f"end={now}")
            flash(f"Arbeitstag beendet um {now} Uhr.", "ok")
    else:
        abort(404)
    db.commit()
    return redirect(url_for("punch"))


def _parse_time_list(form, prefix):
    starts, ends = form.getlist(f"{prefix}_start"), form.getlist(f"{prefix}_end")
    pairs = []
    for s, e in zip(starts, ends):
        s, e = (s or "").strip() or None, (e or "").strip() or None
        if s or e:
            pairs.append((s, e))
    return pairs


# ---------------------------- Manuell (Studierende + Staff) ---------------- #
@app.route("/manual", methods=["GET", "POST"])
@login_required
def manual():
    user = current_user()
    staff = is_staff(user)
    if request.method == "POST":
        sid = get_target_sid(user)
        if sid is None:
            flash("Bitte eine Person auswählen.", "error")
            return redirect(url_for("manual"))
        work_date = request.form.get("work_date")
        start = request.form.get("start_at") or None
        end = request.form.get("end_at") or None
        note = (request.form.get("note") or "").strip() or None
        br_pairs = _parse_time_list(request.form, "break")
        db = get_db()
        existing = db.execute(
            "SELECT id FROM time_entries WHERE student_id=? AND work_date=?",
            (sid, work_date)).fetchone()
        if existing:
            eid = existing["id"]
            db.execute("UPDATE time_entries SET start_at=?,end_at=?,is_manual=1,note=?,"
                       "updated_at=? WHERE id=?",
                       (start, end, note, now_iso(), eid))
            db.execute("DELETE FROM breaks WHERE entry_id=?", (eid,))
            audit(user["id"], "time_entry", eid, "update", "manuell")
        else:
            cur = db.execute(
                "INSERT INTO time_entries (student_id,work_date,start_at,end_at,is_manual,"
                "note,created_at,updated_at) VALUES (?,?,?,?,1,?,?,?)",
                (sid, work_date, start, end, note, now_iso(), now_iso()))
            eid = cur.lastrowid
            audit(user["id"], "time_entry", eid, "insert", "manuell")
        for s, e in br_pairs:
            db.execute("INSERT INTO breaks (entry_id,break_start,break_end) VALUES (?,?,?)",
                       (eid, s, e))
        db.commit()
        flash("Eintrag gespeichert.", "ok")
        return redirect(url_for("manual", student_id=sid) if staff else url_for("punch"))

    sid = get_target_sid(user)
    existing = None
    if sid:
        work_date = request.args.get("work_date") or today_iso()
        existing = get_db().execute(
            "SELECT * FROM time_entries WHERE student_id=? AND work_date=?",
            (sid, work_date)).fetchone()
        existing = attach_breaks([existing])[0] if existing else None
    return render_template("manual.html", user=user, staff=staff,
                           students=all_students() if staff else [],
                           student_id=sid, today=today_iso(), existing=existing)


# ---------------------------- Abwesenheiten -------------------------------- #
@app.route("/absences", methods=["GET", "POST"])
@login_required
def absences():
    user = current_user()
    staff = is_staff(user)
    if request.method == "POST":
        sid = get_target_sid(user)
        if sid is None:
            flash("Bitte eine Person auswählen.", "error")
            return redirect(url_for("absences"))
        typ = request.form.get("type")
        start = request.form.get("start_date")
        end = request.form.get("end_date") or start
        note = (request.form.get("note") or "").strip() or None
        if typ not in ABSENCE_TYPES:
            flash("Unbekannter Abwesenheitstyp.", "error")
        elif not start or end < start:
            flash("Bitte gültigen Zeitraum angeben.", "error")
        else:
            cur = get_db().execute(
                "INSERT INTO absences (student_id,type,start_date,end_date,note,created_at)"
                " VALUES (?,?,?,?,?,?)", (sid, typ, start, end, note, now_iso()))
            audit(user["id"], "absence", cur.lastrowid, "insert", f"{typ} {start}..{end}")
            get_db().commit()
            flash("Abwesenheit gespeichert.", "ok")
        return redirect(url_for("absences", student_id=sid) if staff else url_for("absences"))

    sid = get_target_sid(user)
    rows = get_db().execute(
        "SELECT * FROM absences WHERE student_id=? ORDER BY start_date DESC", (sid,)
    ).fetchall() if sid else []
    return render_template("absences.html", user=user, staff=staff,
                           students=all_students() if staff else [],
                           student_id=sid, rows=rows, types=ABSENCE_TYPES, today=today_iso())


@app.route("/absence/<int:aid>/delete", methods=["POST"])
@login_required
def absence_delete(aid):
    user = current_user()
    row = get_db().execute("SELECT * FROM absences WHERE id=?", (aid,)).fetchone()
    if not row or (row["student_id"] != student_of(user) and not is_staff(user)):
        abort(403)
    get_db().execute("DELETE FROM absences WHERE id=?", (aid,))
    audit(user["id"], "absence", aid, "delete")
    get_db().commit()
    return redirect(url_for("absences"))


@app.route("/entry/<int:eid>/delete", methods=["POST"])
@login_required
def entry_delete(eid):
    user = current_user()
    row = get_db().execute("SELECT * FROM time_entries WHERE id=?", (eid,)).fetchone()
    if not row or (row["student_id"] != student_of(user) and not is_staff(user)):
        abort(403)
    get_db().execute("DELETE FROM time_entries WHERE id=?", (eid,))
    audit(user["id"], "time_entry", eid, "delete")
    get_db().commit()
    flash("Eintrag gelöscht.", "ok")
    return redirect(url_for("punch"))


# ---------------------------- Monatsübersicht ------------------------------ #
@app.route("/month")
@login_required
def month():
    user = current_user()
    staff = is_staff(user)
    sid = get_target_sid(user)
    today = date.today()
    try:
        year, mon = map(int, (request.args.get("ym") or "").split("-"))
    except (ValueError, AttributeError):
        year, mon = today.year, today.month
    entries, student, ov = [], None, None
    if sid:
        student = get_db().execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone()
        entries = attach_breaks(get_db().execute(
            "SELECT * FROM time_entries WHERE student_id=? AND work_date LIKE ?"
            " ORDER BY work_date", (sid, f"{year:04d}-{mon:02d}-%")).fetchall())
        ov = monthly_overview(entries, student, year, mon)
    return render_template("month.html", user=user, staff=staff,
                           students=all_students() if staff else [],
                           student_id=sid, entries=entries, ov=ov,
                           year=year, month=mon,
                           prev_ym=shift_month(year, mon, -1),
                           next_ym=shift_month(year, mon, 1))


# ---------------------------- Verwaltung (Staff) --------------------------- #
@app.route("/admin")
@login_required
@role_required(*STAFF_ROLES)
def admin():
    today = date.today()
    try:
        year, mon = map(int, (request.args.get("ym") or "").split("-"))
    except (ValueError, AttributeError):
        year, mon = today.year, today.month
    students, days, grid = month_grid(year, mon)
    py, pm = shift_month(year, mon, -1)
    ny, nm = shift_month(year, mon, 1)
    db = get_db()
    rows = attach_breaks(db.execute(
        "SELECT t.*, s.first_name, s.last_name FROM time_entries t"
        " JOIN students s ON s.id = t.student_id ORDER BY t.work_date DESC LIMIT 60"
    ).fetchall())
    abs_rows = db.execute(
        "SELECT a.*, s.first_name, s.last_name FROM absences a"
        " JOIN students s ON s.id = a.student_id ORDER BY a.start_date DESC LIMIT 40"
    ).fetchall()
    return render_template(
        "admin.html", board=board_today(), grid_students=students, days=days, grid=grid,
        persons=students_with_login(), year=year, month=mon, prev=(py, pm), next=(ny, nm),
        rows=rows, abs_rows=abs_rows, types=ABSENCE_TYPES)


@app.route("/admin/student/add", methods=["POST"])
@login_required
@role_required(*STAFF_ROLES)
def admin_add_student():
    first = (request.form.get("first_name") or "").strip()
    last = (request.form.get("last_name") or "").strip()
    username = (request.form.get("username") or "").strip().lower()
    target = (request.form.get("monthly_target_hours") or "").strip()
    if not (first and last and username):
        flash("Vorname, Nachname und Benutzername sind Pflicht.", "error")
        return redirect(url_for("admin"))
    db = get_db()
    if db.execute("SELECT 1 FROM users WHERE lower(username)=?", (username,)).fetchone():
        flash(f"Benutzername '{username}' ist bereits vergeben.", "error")
        return redirect(url_for("admin"))
    try:
        target_val = float(target.replace(",", ".")) if target else None
    except ValueError:
        target_val = None
    cur = db.execute(
        "INSERT INTO students (first_name,last_name,monthly_target_hours,active)"
        " VALUES (?,?,?,1)", (first, last, target_val))
    sid = cur.lastrowid
    pw = request.form.get("password") or secrets.token_urlsafe(6)
    db.execute("INSERT INTO users (student_id,username,password_hash,role) VALUES (?,?,?,?)",
               (sid, username, generate_password_hash(pw), "student"))
    audit(current_user()["id"], "student", sid, "insert", f"add {username}")
    db.commit()
    flash(f"Person angelegt. Login: {username} / Startpasswort: {pw}", "ok")
    return redirect(url_for("admin"))


@app.route("/admin/student/<int:sid>/delete", methods=["POST"])
@login_required
@role_required(*STAFF_ROLES)
def admin_delete_student(sid):
    db = get_db()
    db.execute("DELETE FROM users WHERE student_id=?", (sid,))
    db.execute("DELETE FROM students WHERE id=?", (sid,))
    audit(current_user()["id"], "student", sid, "delete")
    db.commit()
    flash("Person und zugehörige Daten gelöscht.", "ok")
    return redirect(url_for("admin"))


@app.route("/admin/student/<int:sid>/active", methods=["POST"])
@login_required
@role_required(*STAFF_ROLES)
def admin_toggle_active(sid):
    db = get_db()
    db.execute("UPDATE students SET active = 1 - active WHERE id=?", (sid,))
    db.commit()
    return redirect(url_for("admin"))


@app.route("/admin/student/<int:sid>/target", methods=["POST"])
@login_required
@role_required(*STAFF_ROLES)
def admin_set_target(sid):
    val = (request.form.get("monthly_target_hours") or "").strip()
    try:
        value = float(val.replace(",", ".")) if val else None
    except ValueError:
        flash("Ungültiger Wert für Monatssoll.", "error")
        return redirect(url_for("admin"))
    get_db().execute("UPDATE students SET monthly_target_hours=? WHERE id=?", (value, sid))
    audit(current_user()["id"], "student", sid, "update", f"target={value}")
    get_db().commit()
    flash("Monatssoll aktualisiert.", "ok")
    return redirect(url_for("admin"))


@app.route("/admin/student/<int:sid>/setpass", methods=["POST"])
@login_required
@role_required(*STAFF_ROLES)
def admin_setpass(sid):
    pw = secrets.token_urlsafe(6)
    get_db().execute(
        "UPDATE users SET password_hash=? WHERE student_id=?",
        (generate_password_hash(pw), sid))
    user = get_db().execute("SELECT username FROM users WHERE student_id=?", (sid,)).fetchone()
    audit(current_user()["id"], "user", sid, "update", "reset password")
    get_db().commit()
    flash(f"Neues Passwort für {user['username'] if user else sid}: {pw}", "ok")
    return redirect(url_for("admin"))


@app.route("/admin/export.csv")
@login_required
@role_required(*STAFF_ROLES)
def admin_export():
    rows = attach_breaks(get_db().execute(
        "SELECT s.last_name, s.first_name, t.* FROM time_entries t"
        " JOIN students s ON s.id = t.student_id ORDER BY s.last_name, t.work_date"
    ).fetchall())
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(["Name", "Vorname", "Datum", "Beginn", "Pausen", "Ende",
                     "Stunden", "manuell", "Notiz"])
    for r in rows:
        pauses = ", ".join(f'{b["break_start"] or "?"}-{b["break_end"] or "?"}'
                           for b in r["breaks"])
        writer.writerow([r["last_name"], r["first_name"], r["work_date"], r["start_at"],
                         pauses, r["end_at"], f'{(r["minutes"] or 0)/60:.2f}',
                         "ja" if r["is_manual"] else "nein", r["note"]])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition":
                             "attachment; filename=arbeitszeiten_export.csv"})


if __name__ == "__main__":
    init_db()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5057"))
    app.run(host=host, port=port, debug=True)
