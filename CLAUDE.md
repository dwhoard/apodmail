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
- **No traceback on APOD API failure.** A `requests` exception message includes the URL, which contains `api_key`, and it would end up in the cron log. `fetch_apod` exits with the status code only.
- **uv standalone installer in the README.** Homebrew's uv installer refuses Intel Macs.
- **Cron runs from a local clone, not iCloud Drive (2026-09-30).** Cron runs failed with `Current directory does not exist` from uv. The cause is macOS TCC: `cron` has no access to `~/Library/Mobile Documents`, so `cd` and the log redirect succeed but uv's `getcwd()` is denied. The deployed copy moved to `~/apodmail`, which also avoids the sync-at-boot race. Granting `/usr/sbin/cron` Full Disk Access was rejected because it gives cron too much access. This repo's iCloud copy is for development only.
