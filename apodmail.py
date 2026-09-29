#!/usr/bin/env python3
"""Fetch NASA's Astronomy Picture of the Day and email it to a local list of recipients.

Usage:
    uv run apodmail.py                 # today's APOD
    uv run apodmail.py --date 2026-09-15
    uv run apodmail.py --dry-run       # print the email instead of sending it

Configuration:
    .env            NASA_API_KEY, GMAIL_ADDRESS, GMAIL_APP_PASSWORD (see .env.example)
    recipients.txt  one email address per line (see recipients.example.txt)
"""

from __future__ import annotations

import argparse
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

import requests
from dotenv import dotenv_values

APOD_API_URL = "https://api.nasa.gov/planetary/apod"
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465

REPO_DIR = Path(__file__).resolve().parent
ENV_PATH = REPO_DIR / ".env"
RECIPIENTS_PATH = REPO_DIR / "recipients.txt"


def load_config() -> dict[str, str]:
    if not ENV_PATH.exists():
        sys.exit(f"Missing {ENV_PATH}. Copy .env.example to .env and fill it in.")
    config = dotenv_values(ENV_PATH)
    required = ["NASA_API_KEY", "GMAIL_ADDRESS", "GMAIL_APP_PASSWORD"]
    missing = [key for key in required if not config.get(key)]
    if missing:
        sys.exit(f"Missing values in {ENV_PATH}: {', '.join(missing)}")
    return config


def load_recipients() -> list[str]:
    if not RECIPIENTS_PATH.exists():
        sys.exit(
            f"Missing {RECIPIENTS_PATH}. Copy recipients.example.txt to "
            "recipients.txt and add your addresses."
        )
    recipients = [
        line.strip()
        for line in RECIPIENTS_PATH.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not recipients:
        sys.exit(f"{RECIPIENTS_PATH} has no recipient addresses.")
    return recipients


def fetch_apod(api_key: str, date: str | None) -> dict:
    params = {"api_key": api_key}
    if date:
        params["date"] = date
    response = requests.get(APOD_API_URL, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def build_message(apod: dict, sender: str, recipients: list[str]) -> EmailMessage:
    title = apod.get("title", "Astronomy Picture of the Day")
    date = apod.get("date", "")
    explanation = apod.get("explanation", "")
    copyright_line = apod.get("copyright")
    media_type = apod.get("media_type")
    image_url = apod.get("hdurl") or apod.get("url")

    msg = EmailMessage()
    msg["Subject"] = f"APOD {date}: {title}"
    msg["From"] = sender
    msg["To"] = sender
    msg["Bcc"] = ", ".join(recipients)

    text_lines = [title, date, ""]
    if copyright_line:
        text_lines.append(f"Credit: {copyright_line}")
    text_lines += ["", explanation, "", image_url or ""]
    msg.set_content("\n".join(text_lines))

    credit_html = f"<p><em>Credit: {copyright_line}</em></p>" if copyright_line else ""
    if media_type == "image" and image_url:
        media_html = f'<p><img src="{image_url}" alt="{title}" style="max-width:100%;"></p>'
    elif image_url:
        media_html = f'<p><a href="{image_url}">View today\'s APOD media</a></p>'
    else:
        media_html = ""

    html = f"""\
<html>
  <body style="font-family: sans-serif; max-width: 700px;">
    <h2>{title}</h2>
    <p>{date}</p>
    {media_html}
    {credit_html}
    <p>{explanation}</p>
  </body>
</html>
"""
    msg.add_alternative(html, subtype="html")
    return msg


def send_message(msg: EmailMessage, sender: str, app_password: str) -> None:
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
        server.login(sender, app_password)
        server.send_message(msg)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="APOD date as YYYY-MM-DD (default: today)")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the email instead of sending it"
    )
    args = parser.parse_args()

    config = load_config()
    recipients = load_recipients()

    apod = fetch_apod(config["NASA_API_KEY"], args.date)
    msg = build_message(apod, config["GMAIL_ADDRESS"], recipients)

    if args.dry_run:
        print(msg)
        return

    send_message(msg, config["GMAIL_ADDRESS"], config["GMAIL_APP_PASSWORD"])
    print(f"Sent APOD ({apod.get('date')}) to {len(recipients)} recipient(s).")


if __name__ == "__main__":
    main()
