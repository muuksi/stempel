# ⏱ Stempel

Web-App zur Arbeitszeiterfassung studentischer Hilfskräfte — **Flask + SQLite**,
bewusst schlank, kein Build-Schritt. Stempeln, Pausen, manuelle Einträge,
Abwesenheiten und eine Übersicht für Vorgesetzte, mobil-optimiert.

---

## Voraussetzungen

- **Python 3.11+**
- keine Datenbankinstallation (SQLite ist in Python enthalten)
- optional, nur für den Import einer vorhandenen Access-Datenbank: **mdbtools**

---

## Installation (lokal)

```bash
git clone https://github.com/muuksi/stempel.git
cd stempel/webapp
python3 -m venv .venv && source .venv/bin/activate   # optional
pip install -r requirements.txt
python3 app.py
```

→ läuft auf **http://127.0.0.1:5057**.

Beim ersten Start werden das Schema (`schema.sql`) und ein **Admin-Konto** angelegt.
Das Startpasswort wird auf der Konsole ausgegeben oder vorab per
`ADMIN_PASSWORD=...` gesetzt.

---

## Installation auf einem Ubuntu-Server

```bash
sudo apt update && sudo apt install -y python3-venv python3-pip nginx
sudo git clone https://github.com/muuksi/stempel.git /opt/stempel
cd /opt/stempel/webapp
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt gunicorn
```

**systemd-Unit** `/etc/systemd/system/stempel.service`:

```ini
[Unit]
Description=Stempel Arbeitszeiterfassung
After=network.target

[Service]
User=www-data
WorkingDirectory=/opt/stempel/webapp
Environment=HOST=127.0.0.1
Environment=PORT=5057
ExecStart=/opt/stempel/webapp/.venv/bin/gunicorn -w 2 -b 127.0.0.1:5057 app:app
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo chown -R www-data:www-data /opt/stempel/webapp/instance
sudo systemctl daemon-reload
sudo systemctl enable --now stempel
```

**nginx-Reverse-Proxy** `/etc/nginx/sites-available/stempel`:

```nginx
server {
    listen 80;
    server_name stempel.example.org;

    location / {
        proxy_pass http://127.0.0.1:5057;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/stempel /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# HTTPS (Let's Encrypt)
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d stempel.example.org
```

> Der Dienst muss in `webapp/instance/` schreiben dürfen (Datenbank, `secret_key`).

---

## Konfiguration (Umgebungsvariablen)

| Variable | Standard | Bedeutung |
|---|---|---|
| `HOST` | `127.0.0.1` | Bind-Adresse (`0.0.0.0` = im Netz erreichbar) |
| `PORT` | `5057` | Port (5000 ist auf macOS vom AirPlay-Empfänger belegt) |
| `ADMIN_PASSWORD` | zufällig | Startpasswort des Admin-Kontos bei Erstinitialisierung |

Das Session-Secret wird automatisch als `instance/secret_key` (Rechte `0600`) erzeugt.

---

## Konten verwalten

```bash
cd webapp
python3 manage.py list
python3 manage.py setpass    <username> <passwort>
python3 manage.py addstudent <username> <vorname> <nachname> [monatssoll]
python3 manage.py addadmin   <username> <passwort>
```

---

## Access-Migration (optional)

```bash
cd webapp
python3 etl_access_to_sqlite.py "../Arbeitszeiten.accdb"
```

- liest die Access-Tabellen über **mdbtools**
- korrigiert das Access-Zeitformat (`1899-12-30 HH:MM` → `HH:MM`)
- kombiniert `Tagdatum` + Uhrzeiten; Pausen landen in eigener Tabelle
- legt je Person ein Login an und gibt die Startpasswörter aus
- **löscht die vorhandene SQLite-DB** und baut sie neu auf

---

## QR-/NFC-Aushang (optional)

```bash
cd webapp
python3 gen_qr.py "https://<host>/punch"
# -> instance/qr_punch.png  und  instance/poster_punch.pdf
```

Der Code enthält **nur die URL, keine Identität** — ein Aushang gilt für alle.
Hinweis: Ein statischer Code ist kein echter Anwesenheitsnachweis (abfotografierbar).

---

## Projektstruktur

```
.
├── README.md                  # diese Datei
├── PROJEKT.md                 # ausführliche Projekt-Dokumentation
└── webapp/
    ├── app.py                 # Flask-App: Routen, Auth, Stempel-/Konto-Logik
    ├── schema.sql             # SQLite-Schema
    ├── etl_access_to_sqlite.py# Migration Access → SQLite
    ├── gen_qr.py              # QR-Code + A4-Aushang (PDF)
    ├── manage.py              # CLI: list / setpass / addstudent / addadmin
    ├── requirements.txt
    ├── templates/             # base, login, punch, manual, absences, month, admin
    ├── static/style.css
    └── instance/              # NICHT im Repo: DB, secret_key, Aushänge
```

---

## Sicherheit & Datenschutz

Dieses Repository enthält **ausschließlich Programmcode und Schema** — keine
personenbezogenen Daten (per `.gitignore` ausgeschlossen: `webapp/instance/`,
`*.sqlite`, `secret_key`, `*.accdb`, `*.mdb`).

Für den Betrieb:
- nicht mit `debug=True` starten, sondern hinter Gunicorn + HTTPS-Reverse-Proxy
- Backups: `sqlite3 instance/arbeitszeiten.sqlite ".backup backup.sqlite"`
- `instance/secret_key` schützen (Rechte `0600`)
