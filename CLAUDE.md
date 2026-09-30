# CLAUDE.md

Decision and coding history for apodmail. User-facing docs live in README.md.

## Overview

Single script, `apodmail.py`. Fetches NASA APOD via the API, optionally scrapes science.nasa.gov/apod/ for today's entry, and sends one email per recipient through Gmail SMTP. Python >= 3.11, managed with uv. Dependencies: `requests`, `python-dotenv`.

Secrets (`.env`) and addresses (`recipients.txt`) are local-only and gitignored.

## Decisions

- **Gmail SMTP with an App Password.** Simplest sender with no extra service. Credentials stay in `.env`.
- **Explanation hyperlinks by scraping.** The API returns plain text with no links. The matching page's explanation paragraph is scraped, relative URLs are absolutized, and the result is used for both the HTML part and the plain-text part (link text + URL). Falls back to the API text if the fetch or parse fails.
- **science.nasa.gov/apod/, not apod.nasa.gov.** The legacy site has moved. The new page also provides the credits table (Date / Credit & Copyright / Authors & editors / A service of), which is appended to the email. That page only shows today's entry, so links and credits apply only when the requested date is today. A past `--date` gets the API's plain text.
- **One message per recipient, To: set to that recipient.** Replaced an earlier To: sender + Bcc all, which delivered correctly but showed the sender's own address in To:. This also keeps recipients from seeing each other's addresses.
- **Retries on NASA HTTP requests.** api.nasa.gov and science.nasa.gov sometimes time out or return 5xx for minutes at a time. `get_with_retries` makes 5 attempts with exponential backoff (5, 10, 20, 40 s). This was raised from 3 attempts with 2 s and 4 s backoff on 2026-09-30, after a run hit three 500s in a row.
- **No traceback on APOD API failure.** A `requests` exception message includes the URL, which contains `api_key`, and it would end up in the cron log. `fetch_apod_from_api` logs the status code only.
- **Page first, API as fallback (2026-09-30).** Since the site move, the API returns the site's generic title ("NASA Science") and NASA logo in place of the real entry, for today and for past dates, and it has had multi-minute 500/503 stretches. The page stayed correct. For today, `fetch_apod_from_page` is tried first and `fetch_apod_from_api` only if it fails. Past `--date` runs use the API only. Both return the same normalized dict for `build_messages`.
  - The page counts only if title, media and explanation all parse. A partial layout change falls back instead of sending a half-broken email. The credits table is optional.
  - API data is rejected as placeholder if the title is "NASA Science" or the URL contains `nasa-logo`.
  - If both sources fail, the script sends nothing and exits 1. A degraded email was rejected.
  - The entry date comes from the page's credits Date row, falling back to local today.
  - Hero regexes anchor on `class="media-detail-hero__media` because the bare class name also appears in inline CSS earlier in the page.
  - Title, copyright and URLs are HTML-escaped in the email body.
- **Timestamped separator per run.** `main` prints a `=====` line with the date and time, flushed so it lands before stderr output in the cron log. It's in the script rather than the crontab line to avoid cron's `%` escaping.
- **uv standalone installer in the README.** Homebrew's uv installer refuses Intel Macs.
- **Cron runs from a local clone, not iCloud Drive (2026-09-30).** Cron runs failed with `Current directory does not exist` from uv. The cause is macOS TCC: `cron` has no access to `~/Library/Mobile Documents`, so `cd` and the log redirect succeed but uv's `getcwd()` is denied. The deployed copy moved to `~/apodmail`, which also avoids the sync-at-boot race. Granting `/usr/sbin/cron` Full Disk Access was rejected because it gives cron too much access. This repo's iCloud copy is for development only.
