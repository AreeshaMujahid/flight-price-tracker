"""
Pegasus price tracker
---------------------
Checks Pegasus Airlines (flypgs.com) "Light" one-way fares (no checked bag)
from Cologne (CGN), Frankfurt (FRA) and Nuremberg (NUE) to Karachi (KHI)
for 20 Feb 2027 - 9 Mar 2027, and emails a summary via Gmail.

It opens the same calendar pages you see on flypgs.com, in a normal Chromium
browser, and reads the price shown under each date.

Setup (once):
    pip install -r requirements.txt
    python -m playwright install chromium

Environment variables (for the email):
    GMAIL_USER          your Gmail address
    GMAIL_APP_PASSWORD  a Gmail "App Password" (not your normal password)
    ALERT_TO            where to send the email (defaults to GMAIL_USER)

Run:
    python pegasus_tracker.py            # check + email
    python pegasus_tracker.py --no-email # check + print only
"""

import argparse
import csv
import os
import smtplib
import sys
from datetime import date, datetime
from email.message import EmailMessage
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ---------------- settings ----------------
ORIGINS = {"CGN": "Cologne", "FRA": "Frankfurt", "NUE": "Nuremberg"}
DESTINATION = "KHI"
START = date(2027, 2, 20)
END = date(2027, 3, 9)
# The calendar shows a whole month, so one page per month in the range.
CALENDAR_ANCHORS = ["2027-02-15", "2027-03-04"]
STOP_AFTER = date(2026, 11, 24)   # tracker does nothing after this day
ALERT_BELOW_EUR = 140             # subject line says "PRICE DROP" below this
HISTORY_FILE = Path(__file__).with_name("price_history.csv")

URL = (
    "https://web.flypgs.com/flexible-search?adultCount=1"
    "&arrivalPort={dest}&currency=EUR&dateOption=1"
    "&departureDate={day}&departurePort={origin}&language=en"
)
BOOK_URL = (
    "https://web.flypgs.com/booking?language=en&adultCount=1"
    "&departurePort={origin}&arrivalPort={dest}&currency=EUR"
    "&dateOption=1&departureDate={day}"
)

# Reads every calendar cell: aria-label holds the date, .amount the price.
READ_CALENDAR_JS = """
() => [...document.querySelectorAll('td.CalendarDay')]
  .filter(td => td.querySelector('.amount'))
  .map(td => ({
     label: td.getAttribute('aria-label') || '',
     amount: td.querySelector('.amount').textContent.trim()
  }))
"""


def parse_label(label: str):
    """'Selected. Sunday, February 21, 2027' -> date(2027, 2, 21)"""
    text = label.split(". ")[-1]          # drop 'Selected. ' prefix
    try:
        return datetime.strptime(text, "%A, %B %d, %Y").date()
    except ValueError:
        return None


def read_month(page, origin: str, anchor: str) -> dict:
    page.goto(URL.format(dest=DESTINATION, day=anchor, origin=origin),
              wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector("td.CalendarDay .amount", timeout=45000)
    page.wait_for_timeout(1500)
    prices = {}
    for cell in page.evaluate(READ_CALENDAR_JS):
        d = parse_label(cell["label"])
        try:
            amount = float(cell["amount"].replace(",", ""))
        except ValueError:
            continue
        if d and START <= d <= END:
            prices[d] = amount
    return prices


def collect() -> dict:
    results = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(locale="en-US")
        for code in ORIGINS:
            results[code] = {}
            for anchor in CALENDAR_ANCHORS:
                try:
                    results[code].update(read_month(page, code, anchor))
                except PWTimeout:
                    print(f"[{code}] calendar for {anchor} did not load", file=sys.stderr)
        browser.close()
    return results


def save_history(results: dict) -> None:
    new_file = not HISTORY_FILE.exists()
    now = datetime.now().isoformat(timespec="minutes")
    with HISTORY_FILE.open("a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["checked_at", "origin", "flight_date", "light_fare_eur"])
        for code, prices in results.items():
            for d, amount in sorted(prices.items()):
                w.writerow([now, code, d.isoformat(), amount])


def build_report(results: dict):
    lines, overall_min = [], None
    for code, name in ORIGINS.items():
        prices = results.get(code, {})
        if not prices:
            lines.append(f"{name} ({code}): is dafa check nahi ho saka\n")
            continue
        cheapest = sorted(prices.items(), key=lambda kv: (kv[1], kv[0]))
        best_d, best_p = cheapest[0]
        overall_min = best_p if overall_min is None else min(overall_min, best_p)
        lines.append(f"{name} ({code}): sab se sasta EUR {best_p:.0f} "
                     f"({best_d:%a %d %b})")
        lines.append("  Book: " + BOOK_URL.format(origin=code, dest=DESTINATION,
                                                  day=best_d.isoformat()))
        lines.append("  Saari dates: " + ", ".join(
            f"{d:%d %b} {p:.0f}" for d, p in sorted(prices.items())))
        lines.append("")
    lines.append("Prices: Pegasus Light fare, one-way, bina checked bag, 1 adult.")
    body = "\n".join(lines)

    if overall_min is None:
        subject = "Pegasus → Karachi: prices check nahi ho sakin"
    elif overall_min < ALERT_BELOW_EUR:
        subject = f"PRICE DROP! Pegasus → Karachi from EUR {overall_min:.0f}"
    else:
        subject = f"Pegasus → Karachi: sab se sasta EUR {overall_min:.0f}"
    return subject, body


def send_email(subject: str, body: str) -> None:
    user = os.environ["GMAIL_USER"]
    pwd = os.environ["GMAIL_APP_PASSWORD"]
    to = os.environ.get("ALERT_TO") or user
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(user, pwd)
        s.send_message(msg)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-email", action="store_true")
    args = ap.parse_args()

    if date.today() > STOP_AFTER:
        print("Tracking period khatam ho gaya (STOP_AFTER). Kuch nahi kiya.")
        return

    results = collect()
    save_history(results)
    subject, body = build_report(results)
    print(subject, "\n", body, sep="")
    if not args.no_email:
        send_email(subject, body)
        print("Email bhej di.")


if __name__ == "__main__":
    main()
