# apodmail
Fetches NASA's Astronomy Picture of the Day (via the official APOD API) and emails it to a local list of recipients.

## Setup

```bash
uv sync
cp .env.example .env              # fill in NASA_API_KEY, GMAIL_ADDRESS, GMAIL_APP_PASSWORD
cp recipients.example.txt recipients.txt   # add recipient addresses, one per line
```

- Get a free NASA API key at https://api.nasa.gov/
- Use a Gmail App Password (not your real password): https://myaccount.google.com/apppasswords
- `.env` and `recipients.txt` are gitignored (local-only, never committed)

## Usage

```bash
uv run apodmail.py                  # send today's APOD
uv run apodmail.py --date 2026-09-15
uv run apodmail.py --dry-run        # print the email instead of sending it
```
