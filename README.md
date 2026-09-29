# apodmail
Fetches NASA's Astronomy Picture of the Day and emails it to a local list of recipients.

Pulls title, date, image, and explanation from the official [APOD API](https://api.nasa.gov/). When run for today's date, it also scrapes [science.nasa.gov/apod/](https://science.nasa.gov/apod/) for the explanation's inline hyperlinks and its Date / Credit & Copyright / Authors & editors / A service of credits table, since the API doesn't expose those. Runs for a past `--date` fall back to the API's plain-text explanation only (no links, no credits table) since that page only ever shows today's entry.

## Setup

Requires [uv](https://docs.astral.sh/uv/):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then:

```bash
uv sync
cp .env.example .env                       # fill in NASA_API_KEY, GMAIL_ADDRESS, GMAIL_APP_PASSWORD
cp recipients.example.txt recipients.txt   # add recipient addresses, one per line
```

- Get a free NASA API key at https://api.nasa.gov/
- Use a Gmail App Password (not your real password): https://myaccount.google.com/apppasswords — requires 2-Step Verification enabled on the Google account first
- `.env` and `recipients.txt` are gitignored (local-only, never committed)

## Usage

```bash
uv run apodmail.py                  # send today's APOD
uv run apodmail.py --date 2026-09-15
uv run apodmail.py --dry-run        # print the email(s) instead of sending them
```

Each recipient gets their own message (To: set to their address, not Bcc'd), so recipients don't see each other's addresses. HTTP requests to NASA retry a few times on timeout before giving up.

## Running daily via cron

```
0 7 * * * cd /path/to/apodmail && /path/to/uv run apodmail.py >> apodmail.log 2>&1
```

- Find `uv`'s path with `which uv` on the machine that will run the cron job — it varies by install method and CPU architecture.
- If the repo lives in a cloud-synced folder (iCloud Drive, Dropbox, etc.), a cron job can fire before the files finish syncing down on a machine that just booted or reconnected — test once manually, or keep the deployed copy in a plain local folder instead.
- `apodmail.log` is gitignored.

## License

MIT — see [LICENSE](LICENSE).
