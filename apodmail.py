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
from datetime import date as date_cls
from email.message import EmailMessage
from html import unescape
from pathlib import Path
from urllib.parse import urljoin

import requests
from dotenv import dotenv_values

APOD_API_URL = "https://api.nasa.gov/planetary/apod"
APOD_CURRENT_URL = "https://science.nasa.gov/apod/"
SCRAPE_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; apodmail/1.0)"}
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


def fetch_current_apod_page(apod_date: str) -> tuple[str, str] | None:
    """Fetch science.nasa.gov/apod/, returning (html, url). That page only
    ever shows today's entry, so this only applies when apod_date is today;
    the API doesn't expose the explanation's inline links or the credits
    table, so we scrape this page for both when we can. Returns None if the
    date isn't today or the page can't be fetched, so callers can fall back
    to API-only content."""
    if apod_date != date_cls.today().isoformat():
        return None
    try:
        response = requests.get(APOD_CURRENT_URL, timeout=30, headers=SCRAPE_HEADERS)
        response.raise_for_status()
    except requests.RequestException:
        return None
    return response.text, APOD_CURRENT_URL


def absolutize_hrefs(fragment: str, base_url: str) -> str:
    return re.sub(
        r'href="(?!https?://)([^"]+)"',
        lambda m: f'href="{urljoin(base_url, m.group(1))}"',
        fragment,
        flags=re.IGNORECASE,
    )


def extract_explanation_fragment(page_html: str, base_url: str) -> str | None:
    match = re.search(
        r"Explanation:\s*</strong>(.*?)<br><br>",
        page_html,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    return absolutize_hrefs(match.group(1).strip(), base_url)


def extract_credits_rows(page_html: str, base_url: str) -> list[tuple[str, str]] | None:
    """Pull the Date / Credit & Copyright / Authors & editors / A service of
    table shown next to the image on science.nasa.gov/apod/."""
    table_match = re.search(
        r'<table class="media-detail-hero__meta-table.*?</table>',
        page_html,
        re.IGNORECASE | re.DOTALL,
    )
    if not table_match:
        return None
    rows = re.findall(
        r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>",
        table_match.group(0),
        re.IGNORECASE | re.DOTALL,
    )
    if not rows:
        return None
    cleaned = []
    for label_html, value_html in rows:
        label = clean_explanation(unescape(re.sub(r"<[^>]+>", "", label_html))).rstrip(":")
        cleaned.append((label, absolutize_hrefs(value_html.strip(), base_url)))
    return cleaned


def _links_to_text(fragment: str) -> str:
    return re.sub(
        r'<a\s+[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        r"\2 (\1)",
        fragment,
        flags=re.IGNORECASE | re.DOTALL,
    )


def explanation_fragment_to_text(fragment: str) -> str:
    """Render the scraped explanation as one flowing plain-text paragraph,
    matching how it reads on the APOD web page."""
    text = re.sub(r"<[^>]+>", "", _links_to_text(fragment))
    return clean_explanation(unescape(text))


def credits_fragment_to_text(fragment: str) -> str:
    """Render the scraped credits block as plain text, keeping its <br>
    line breaks (author line, NASA Official line, etc.) intact."""
    text = re.sub(r"<br\s*/?>", "\n", _links_to_text(fragment), flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in unescape(text).splitlines()]
    return "\n".join(line for line in lines if line)


def build_messages(apod: dict, sender: str, recipients: list[str]) -> list[EmailMessage]:
    title = apod.get("title", "Astronomy Picture of the Day")
    date = apod.get("date", "")
    copyright_line = apod.get("copyright")
    media_type = apod.get("media_type")
    image_url = apod.get("hdurl") or apod.get("url")

    page = fetch_current_apod_page(date) if date else None
    page_html, base_url = page if page else (None, None)

    explanation_fragment = (
        extract_explanation_fragment(page_html, base_url) if page_html else None
    )
    if explanation_fragment:
        explanation_html = explanation_fragment
        explanation = explanation_fragment_to_text(explanation_fragment)
    else:
        explanation = clean_explanation(apod.get("explanation", ""))
        explanation_html = explanation

    credits_rows = extract_credits_rows(page_html, base_url) if page_html else None
    if credits_rows:
        credits_table_html = "".join(
            '<tr><th style="text-align:left;vertical-align:top;white-space:nowrap;'
            f'padding:4px 12px 4px 0;font-weight:600;">{label}</th>'
            f'<td style="padding:4px 0;">{value_html}</td></tr>'
            for label, value_html in credits_rows
        )
        credits_table_html = (
            '<table style="margin-top:2em;padding-top:1em;border-top:1px solid #ccc;'
            'font-size:0.85em;color:#555;border-collapse:collapse;">'
            f"{credits_table_html}</table>"
        )
        credits_text_lines = [
            f"{label}: {credits_fragment_to_text(value_html).replace(chr(10), '; ')}"
            for label, value_html in credits_rows
        ]
    else:
        credits_table_html = ""
        credits_text_lines = []

    text_lines = ["Astronomy Picture of the Day", "", title, date, ""]
    if copyright_line:
        text_lines.append(f"Credit: {copyright_line}")
    text_lines += ["", explanation, "", image_url or ""]
    if credits_text_lines:
        text_lines += ["", "-" * 40, *credits_text_lines]
    text_body = "\n".join(text_lines)

    credit_html = f"<p><em>Credit: {copyright_line}</em></p>" if copyright_line else ""
    if media_type == "image" and image_url:
        media_html = f'<p><img src="{image_url}" alt="{title}" style="max-width:100%;"></p>'
    elif image_url:
        media_html = f'<p><a href="{image_url}">View today\'s APOD media</a></p>'
    else:
        media_html = ""

    html_body = f"""\
<html>
  <body style="font-family: sans-serif; max-width: 700px;">
    <h1>Astronomy Picture of the Day</h1>
    <h2>{title}</h2>
    <p>{date}</p>
    {media_html}
    {credit_html}
    <p>{explanation_html}</p>
    {credits_table_html}
  </body>
</html>
"""

    messages = []
    for recipient in recipients:
        msg = EmailMessage()
        msg["Subject"] = f"APOD {date}: {title}"
        msg["From"] = sender
        msg["To"] = recipient
        msg.set_content(text_body)
        msg.add_alternative(html_body, subtype="html")
        messages.append(msg)
    return messages


def send_messages(messages: list[EmailMessage], sender: str, app_password: str) -> None:
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
        server.login(sender, app_password)
        for msg in messages:
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
    messages = build_messages(apod, config["GMAIL_ADDRESS"], recipients)

    if args.dry_run:
        for msg in messages:
            print(msg)
        return

    send_messages(messages, config["GMAIL_ADDRESS"], config["GMAIL_APP_PASSWORD"])
    print(f"Sent APOD ({apod.get('date')}) to {len(recipients)} recipient(s).")


if __name__ == "__main__":
    main()
