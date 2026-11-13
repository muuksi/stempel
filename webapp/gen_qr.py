"""
Erzeugt QR-Code (PNG) und einen druckfertigen A4-Aushang (PDF) für die Stempeluhr.

Aufruf:
    python3 gen_qr.py "https://<host>/punch"
Ergebnisse:
    instance/qr_punch.png
    instance/poster_punch.pdf
"""
from __future__ import annotations

import os
import sys

import qrcode
from fpdf import FPDF

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, "instance")
os.makedirs(OUT_DIR, exist_ok=True)


def make_qr(url: str) -> str:
    qr = qrcode.QRCode(
        version=None, error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=12, border=3,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    path = os.path.join(OUT_DIR, "qr_punch.png")
    img.save(path)
    return path


def make_poster(url: str, qr_png: str) -> str:
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(False)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 28)
    pdf.cell(0, 20, "Arbeitszeit stempeln", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 14)
    pdf.set_text_color(90, 90, 90)
    pdf.cell(0, 8, "QR-Code mit der Handy-Kamera scannen - fertig.",
             align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)

    # QR-Code zentriert, ca. 120 mm
    qr_w = 120
    pdf.image(qr_png, x=(210 - qr_w) / 2, y=pdf.get_y(), w=qr_w)
    pdf.ln(qr_w + 8)

    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, "So funktioniert es:", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 12)
    steps = [
        "1.  QR-Code mit der Kamera scannen (oder NFC-Tag ans Handy halten).",
        "2.  Einloggen - beim ersten Mal mit Benutzername und Passwort.",
        "3.  Beim Ankommen 'Loslegen', in der Pause 'Pause', am Ende 'Ende'.",
    ]
    for s in steps:
        pdf.multi_cell(0, 7, s, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_text_color(90, 90, 90)
    pdf.set_font("Helvetica", "I", 10)
    pdf.cell(0, 6, f"Adresse der Web-App: {url}", align="C",
             new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, "Bei Problemen bitte an die Arbeitsgruppe wenden.",
             align="C", new_x="LMARGIN", new_y="NEXT")

    path = os.path.join(OUT_DIR, "poster_punch.pdf")
    pdf.output(path)
    return path


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5057/punch"
    png = make_qr(url)
    pdf = make_poster(url, png)
    print(f"URL:   {url}")
    print(f"QR:    {png}")
    print(f"PDF:   {pdf}")
