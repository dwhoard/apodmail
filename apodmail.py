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
import time
from datetime import date as date_cls
from datetime import datetime
from email.message import EmailMessage
from html import escape, unescape
from pathlib import Path
from urllib.parse import urljoin

import requests
from dotenv import dotenv_values

APOD_API_URL = "https://api.nasa.gov/planetary/apod"
APOD_CURRENT_URL = "https://science.nasa.gov/apod/"
SCRAPE_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; apodmail/1.0)"}
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
REQUEST_ATTEMPTS = 5
REQUEST_BACKOFF_SECONDS = 5

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


def get_with_retries(url: str, **kwargs) -> requests.Response:
    """GET with retries and exponential backoff (5, 10, 20, 40 s) on network,
    timeout, and HTTP errors; NASA's endpoints intermittently time out or
    return 5xx on an otherwise-fine request."""
    last_exc: requests.RequestException | None = None
    for attempt in range(1, REQUEST_ATTEMPTS + 1):
        try:
            response = requests.get(url, timeout=30, **kwargs)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < REQUEST_ATTEMPTS:
                time.sleep(REQUEST_BACKOFF_SECONDS * 2 ** (attempt - 1))
    raise last_exc


def fetch_apod_from_api(api_key: str, date: str | None) -> dict | None:
    """Fetch an APOD entry from the API, normalized for build_messages.
    Returns None (after logging why) if the request fails or the API hands
    back placeholder data instead of a real entry."""
    params = {"api_key": api_key}
    if date:
        params["date"] = date
    try:
        response = get_with_retries(APOD_API_URL, params=params)
        data = response.json()
    except requests.RequestException as exc:
        # Don't print exc itself: its message includes the URL with api_key.
        status = exc.response.status_code if exc.response is not None else None
        detail = f"HTTP {status}" if status else type(exc).__name__
        print(f"NASA APOD API failed after {REQUEST_ATTEMPTS} attempts ({detail}).", file=sys.stderr)
        return None
    except ValueError:
        print("NASA APOD API returned invalid JSON.", file=sys.stderr)
        return None

    title = data.get("title")
    media_url = data.get("hdurl") or data.get("url")
    # Since the site move, the API sometimes returns the site's generic title
    # and logo instead of the real entry.
    if not title or not media_url or title == "NASA Science" or "nasa-logo" in media_url:
        print("NASA APOD API returned placeholder data.", file=sys.stderr)
        return None
    explanation = clean_explanation(data.get("explanation", ""))
    return {
        "source": "api.nasa.gov",
        "title": title,
        "date": data.get("date", date or ""),
        "media_type": data.get("media_type"),
        "media_url": media_url,
        "copyright": data.get("copyright"),
        "explanation_text": explanation,
        "explanation_html": escape(explanation),
        "credits_rows": None,
    }


def clean_explanation(text: str) -> str:
    """Normalize whitespace/dashes in the API's explanation field to match the
    tidy paragraph layout on the APOD web page."""
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace(" -- ", " — ")
    return text


def fetch_apod_from_page() -> dict | None:
    """Scrape today's APOD from science.nasa.gov/apod/, normalized for
    build_messages. That page only ever shows today's entry. It is the
    primary source: it has the explanation's inline links and the credits
    table (which the API lacks), and it has stayed correct while the API
    returned placeholder data. Returns None (after logging why) unless the
    title, media, and explanation all parse, so a partial layout change
    falls back to the API instead of sending a half-broken email."""
    try:
        response = get_with_retries(APOD_CURRENT_URL, headers=SCRAPE_HEADERS)
    except requests.RequestException as exc:
        print(f"science.nasa.gov/apod/ fetch failed ({type(exc).__name__}).", file=sys.stderr)
        return None
    page_html, base_url = response.text, APOD_CURRENT_URL

    title = extract_hero_title(page_html)
    media = extract_hero_media(page_html)
    explanation_fragment = extract_explanation_fragment(page_html, base_url)
    missing = [
        name
        for name, value in [("title", title), ("media", media), ("explanation", explanation_fragment)]
        if not value
    ]
    if missing:
        print(f"science.nasa.gov/apod/ parse failed (missing {', '.join(missing)}).", file=sys.stderr)
        return None

    credits_rows = extract_credits_rows(page_html, base_url)
    media_type, media_url = media
    return {
        "source": "science.nasa.gov",
        "title": title,
        "date": page_date(credits_rows) or date_cls.today().isoformat(),
        "media_type": media_type,
        "media_url": media_url,
        "copyright": None,  # covered by the credits table
        "explanation_text": explanation_fragment_to_text(explanation_fragment),
        "explanation_html": explanation_fragment,
        "credits_rows": credits_rows,
    }


def page_date(credits_rows: list[tuple[str, str]] | None) -> str | None:
    """Read the entry's date (e.g. "September 30, 2026") from the credits
    table's Date row, as YYYY-MM-DD."""
    for label, value_html in credits_rows or []:
        if label.lower() == "date":
            text = clean_explanation(unescape(re.sub(r"<[^>]+>", "", value_html)))
            try:
                return datetime.strptime(text, "%B %d, %Y").date().isoformat()
            except ValueError:
                return None
    return None


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


def extract_hero_title(page_html: str) -> str | None:
    """Pull the APOD title from the hero block on science.nasa.gov/apod/."""
    match = re.search(
        r'class="media-detail-hero__media.*?<h2[^>]*>(.*?)</h2>',
        page_html,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    title = clean_explanation(unescape(re.sub(r"<[^>]+>", "", match.group(1))))
    return title or None


def extract_hero_media(page_html: str) -> tuple[str, str] | None:
    """Pull (media_type, url) for the APOD image or video embed from the hero
    block on science.nasa.gov/apod/."""
    block = re.search(
        r'class="media-detail-hero__media(.*?)<h2',
        page_html,
        re.IGNORECASE | re.DOTALL,
    )
    if not block:
        return None
    match = re.search(
        r'<(img|iframe)\b[^>]*\bsrc="([^"]+)"',
        block.group(1),
        re.IGNORECASE,
    )
    if not match:
        return None
    media_type = "image" if match.group(1).lower() == "img" else "video"
    return media_type, unescape(match.group(2))


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
    title = apod["title"]
    date = apod["date"]
    copyright_line = apod["copyright"]
    media_type = apod["media_type"]
    image_url = apod["media_url"]
    explanation = apod["explanation_text"]
    explanation_html = apod["explanation_html"]
    credits_rows = apod["credits_rows"]

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

    credit_html = f"<p><em>Credit: {escape(copyright_line)}</em></p>" if copyright_line else ""
    if media_type == "image" and image_url:
        media_html = f'<p><img src="{escape(image_url)}" alt="{escape(title)}" style="max-width:100%;"></p>'
    elif image_url:
        media_html = f'<p><a href="{escape(image_url)}">View today\'s APOD media</a></p>'
    else:
        media_html = ""

    html_body = f"""\
<html>
  <body style="font-family: sans-serif; max-width: 700px;">
    <h1>Astronomy Picture of the Day</h1>
    <h2>{escape(title)}</h2>
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

    # Separator so each run stands out in the cron log. Flush so it lands
    # before any stderr output (stdout is block-buffered when redirected).
    print(f"===== {datetime.now():%Y-%m-%d %H:%M:%S} =====", flush=True)

    config = load_config()
    recipients = load_recipients()

    # Today: scrape the page first, API as fallback. Past dates: API only,
    # since the page only shows today's entry.
    today = date_cls.today().isoformat()
    apod = None
    if args.date in (None, today):
        apod = fetch_apod_from_page()
    if apod is None:
        apod = fetch_apod_from_api(config["NASA_API_KEY"], args.date)
    if apod is None:
        sys.exit("No usable APOD data from the page or the API; nothing sent.")

    messages = build_messages(apod, config["GMAIL_ADDRESS"], recipients)

    if args.dry_run:
        for msg in messages:
            print(msg)
        return

    send_messages(messages, config["GMAIL_ADDRESS"], config["GMAIL_APP_PASSWORD"])
    print(f"Sent APOD ({apod['date']}, from {apod['source']}) to {len(recipients)} recipient(s).")


if __name__ == "__main__":
    main()
