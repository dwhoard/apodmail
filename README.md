# apodmail
Fetches NASA's Astronomy Picture of the Day and emails it to a local list of recipients.

For today's entry, it scrapes [science.nasa.gov/apod/](https://science.nasa.gov/apod/) for the title, image (or video link), explanation with its inline hyperlinks, and the Date / Credit & Copyright / Authors & editors / A service of credits table. If the page can't be fetched or parsed, it falls back to the official [APOD API](https://api.nasa.gov/) (plain-text explanation, no links or credits table). Runs for a past `--date` use the API only, since the page only shows today's entry. If neither source gives usable data, nothing is sent and the script exits with status 1.

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

Deploy from a plain local clone, not a cloud-synced folder (iCloud Drive, Dropbox, etc.):

```bash
git clone https://github.com/dwhoard/apodmail.git ~/apodmail
cd ~/apodmail
uv sync
# copy or create .env and recipients.txt (see Setup), then test once:
uv run apodmail.py --dry-run
```

Then add to `crontab -e`:

```
0 7 * * * cd $HOME/apodmail && /path/to/uv run apodmail.py >> apodmail.log 2>&1
```

- Find `uv`'s path with `which uv` on the machine that will run the cron job. It varies by install method and CPU architecture.
- Why local: on macOS, `cron` has no privacy (TCC) access to `~/Library/Mobile Documents`, so a job run from iCloud Drive fails with `Current directory does not exist` from `uv`. Cloud-synced folders can also be stale or missing right after boot. Granting `/usr/sbin/cron` Full Disk Access also works, but gives cron broad access.
- To update the deployed copy: `cd ~/apodmail && git pull && uv sync`.
- Each run starts with a timestamped `===== YYYY-MM-DD HH:MM:SS =====` line, so days are easy to tell apart in `apodmail.log`. The log is gitignored.

## License

MIT — see [LICENSE](LICENSE).
