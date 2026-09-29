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
import re
import smtplib
import sys
from email.message import EmailMessage
from html import unescape
from pathlib import Path
from urllib.parse import urljoin

import requests
from dotenv import dotenv_values

APOD_API_URL = "https://api.nasa.gov/planetary/apod"
APOD_LEGACY_URL_TEMPLATE = "https://apod.nasa.gov/apod/ap{yy}{mm}{dd}.html"
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


def clean_explanation(text: str) -> str:
    """Normalize whitespace/dashes in the API's explanation field to match the
    tidy paragraph layout on the APOD web page."""
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace(" -- ", " — ")
    return text


def fetch_explanation_html(date: str) -> str | None:
    """Scrape the explanation paragraph (with its hyperlinks) from the legacy
    apod.nasa.gov page, since the API's explanation field is link-free plain
    text. Returns None if the page can't be fetched or parsed, so callers can
    fall back to the API's plain-text explanation."""
    yyyy, mm, dd = date.split("-")
    url = APOD_LEGACY_URL_TEMPLATE.format(yy=yyyy[2:], mm=mm, dd=dd)
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
    except requests.RequestException:
        return None

    match = re.search(
        r"Explanation:\s*</b>(.*?)<p>\s*<center>",
        response.text,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None

    fragment = match.group(1).strip()
    fragment = re.sub(
        r'href="(?!https?://)([^"]+)"',
        lambda m: f'href="{urljoin(url, m.group(1))}"',
        fragment,
        flags=re.IGNORECASE,
    )
    return fragment


def html_fragment_to_text(fragment: str) -> str:
    """Render a scraped explanation fragment as plain text, turning links
    into 'text (url)' instead of dropping them."""
    text = re.sub(
        r'<a\s+[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        r"\2 (\1)",
        fragment,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<[^>]+>", "", text)
    return clean_explanation(unescape(text))


def build_message(apod: dict, sender: str, recipients: list[str]) -> EmailMessage:
    title = apod.get("title", "Astronomy Picture of the Day")
    date = apod.get("date", "")
    copyright_line = apod.get("copyright")
    media_type = apod.get("media_type")
    image_url = apod.get("hdurl") or apod.get("url")

    explanation_fragment = fetch_explanation_html(date) if date else None
    if explanation_fragment:
        explanation_html = explanation_fragment
        explanation = html_fragment_to_text(explanation_fragment)
    else:
        explanation = clean_explanation(apod.get("explanation", ""))
        explanation_html = explanation

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
    <p>{explanation_html}</p>
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
