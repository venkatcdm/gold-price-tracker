# Chennai 22K Gold Price Email Tracker

A small Python + GitHub Actions project that emails a daily Chennai 22K gold benchmark report for:
- 1 gram
- 1 sovereign (8 grams)

The email compares the current source rate with the previous India-calendar-day snapshot:
- Green: rate decreased
- Red: rate increased
- Grey: no change / first-run baseline

## Price source and limitations

The initial source is the public CWJA website: https://cwja.org.in/. The page displays a 22K per-gram rate and an update timestamp. This is a published Chennai market benchmark, not a guaranteed quote from every jewellery shop. The site does not provide a documented public API in this implementation, so `main.py` parses the public HTML. If the website changes its page structure, the script intentionally fails rather than guessing a rate.

The report includes the source update timestamp. If the timestamp is older than 48 hours, the email displays a warning. Weekend emails still run and use the latest rate published by the source.

The first successful run establishes a baseline; it cannot compare with yesterday unless `history.json` already contains yesterday's observation. GitHub Actions commits the daily snapshot back to the repository.

## Setup

1. Create a new GitHub repository, for example `chennai-gold-tracker`.
2. Upload all files from this folder, preserving `.github/workflows/daily-gold-email.yml`.
3. In GitHub, open **Settings → Secrets and variables → Actions → New repository secret** and add:
   - `MAIL_USERNAME`: the Gmail address that sends the report.
   - `MAIL_APP_PASSWORD`: a Gmail App Password (not your normal Gmail password).
   - `RECIPIENT_EMAIL`: the email address that receives the report.
4. For Gmail SMTP, enable 2-Step Verification on the sender Google account, then create an App Password if your account permits it. Google says App Passwords require 2-Step Verification and may not be available for some managed/Advanced Protection accounts. Never put the password in source code.
5. Open **Settings → Actions → General → Workflow permissions**. Allow read and write permissions if your organization/repository policy requires this setting; the workflow also declares `contents: write`.
6. Open **Actions → Daily Chennai 22K Gold Email → Run workflow** to test it manually.
7. Check the run logs and the recipient inbox (including Spam). The scheduled workflow runs daily at 03:30 UTC, which is 09:00 IST.
8. Keep the repository's default branch as the branch where the workflow file lives; scheduled runs use the default branch.

## Secrets and safety

- Never commit SMTP passwords, `.env` files, or other credentials.
- Do not use a company-managed email account unless your organization's policies allow SMTP automation.
- `MAIL_APP_PASSWORD` is a Google App Password; revoke it if exposed.
- The script does not include a hard-coded recipient address.
- GitHub scheduled jobs can occasionally be delayed, so 09:00 is the target time rather than a guaranteed delivery minute.

## Run locally (optional)

Python 3.10+ is recommended.

```bash
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows PowerShell:
# .venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

Set `MAIL_USERNAME`, `MAIL_APP_PASSWORD`, and `RECIPIENT_EMAIL` as environment variables, then run:

```bash
python main.py
```

Do not commit credentials or paste them into chat.

## How the daily comparison works

The script compares today's source rate with the previous calendar day's saved snapshot in `history.json`. If the source does not update on a weekend, the latest published value is still emailed; the report shows the source timestamp and warns when it is older than 48 hours. A baseline is shown if no previous-day snapshot exists.

The 8-gram value is calculated as `rate_per_gram * 8`. Jewellery making charges, GST, and shop-specific premiums are not included.
