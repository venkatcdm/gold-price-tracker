import html
import json
import os
import re
import smtplib
import sys
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

IST = ZoneInfo("Asia/Kolkata")
HISTORY_FILE = Path("history.json")

# Official association websites
INDIA_URL = "https://ibja.co/"
INDIA_SOURCE = "India Bullion and Jewellers Association (IBJA)"

CHENNAI_URL = "https://cwja.org.in/"
CHENNAI_SOURCE = "Chennai Wholesale Jewellers Association (CWJA)"

HEADERS = {
    "User-Agent": "GoldPriceTracker/1.0 (personal daily email)"
}


def money(value: int) -> str:
    """Format an INR amount with comma separators."""
    return "₹" + f"{value:,}"


def get_page(url: str) -> BeautifulSoup:
    """Download a page and parse its HTML."""
    response = requests.get(url, headers=HEADERS, timeout=25)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def fetch_india_22k_rate() -> dict:
    """
    Fetch IBJA's indicative 22K retail jewellery rate per gram.
    Accepts whitespace variations around the currency and rate.
    Fails safely if the date or rate cannot be verified.
    """
    soup = get_page(INDIA_URL)
    page_text = " ".join(soup.stripped_strings)

    heading = re.search(
        r"IBJA['’]s\s+indicative\s+Retail\s+selling\s+Rates\s+"
        r"for\s+Gold\s+Jewellery\s*\(\s*(AM|PM)\s*\)",
        page_text,
        re.IGNORECASE,
    )

    if not heading:
        raise RuntimeError(
            "Could not find IBJA's retail gold-rate section. "
            "Check the official website or its page structure."
        )

    # Read the text after the heading, where the bulletin date and
    # rates are expected. Allow extra whitespace around all fields.
    section = page_text[heading.end():heading.end() + 2000]

    date_match = re.search(
        r"\b(\d{1,2}\s*/\s*\d{1,2}\s*/\s*\d{4})\b",
        section,
    )

    rate_match = re.search(
        r"\b22\s*KT\s*[:\-]?\s*"
        r"(?:₹\s*|Rs\.?\s*)?"
        r"([\d,]+(?:\.\d{1,2})?)\b",
        section,
        re.IGNORECASE,
    )

    if not date_match or not rate_match:
        # Print a short diagnostic excerpt in GitHub Actions logs.
        # This contains page text, not email credentials.
        print(
            "IBJA extraction diagnostic — expected date/rate not found.",
            file=sys.stderr,
        )
        print(
            f"Bulletin excerpt: {section[:700]}",
            file=sys.stderr,
        )
        raise RuntimeError(
            "Could not reliably extract IBJA's 22K rate and "
            "publication date. No guessed price will be used."
        )

    rate_text = rate_match.group(1).replace(",", "")
    rate_value = float(rate_text)

    if not rate_value.is_integer() or rate_value <= 0:
        raise RuntimeError(
            f"IBJA returned an invalid or unexpected 22K rate: "
            f"{rate_text}"
        )

    rate = int(rate_value)

    date_text = re.sub(r"\s+", "", date_match.group(1))
    published_date = datetime.strptime(
        date_text, "%d/%m/%Y"
    ).date()

    now = datetime.now(IST)

    if published_date > now.date():
        raise RuntimeError(
            f"IBJA publication date is in the future: {published_date}"
        )

    return {
        "rate_per_gram": rate,
        "source": INDIA_SOURCE,
        "source_url": INDIA_URL,
        "source_updated": published_date.isoformat(),
        "source_updated_label": (
            f"{published_date.strftime('%d %b %Y')} "
            f"({heading.group(1).upper()} bulletin)"
        ),
        "fetched_at": now.isoformat(),
        "stale_days": (now.date() - published_date).days,
    }


def fetch_chennai_22k_rate() -> dict:
    """
    Fetch the 22K rate per gram and update timestamp from CWJA.
    """
    soup = get_page(CHENNAI_URL)
    rate = None

    for row in soup.find_all("tr"):
        cells = [
            " ".join(cell.stripped_strings)
            for cell in row.find_all(["td", "th"])
        ]
        row_text = " ".join(cells)

        if re.search(
            r"1\s*Gm\s*Gold\s*22\s*Kt",
            row_text,
            re.IGNORECASE,
        ):
            for cell in cells[1:]:
                match = re.search(
                    r"(?:₹|Rs\.?\s*)?\s*([\d,]+(?:\.\d{1,2})?)",
                    cell,
                    re.IGNORECASE,
                )
                if match:
                    rate = round(
                        float(match.group(1).replace(",", ""))
                    )
                    break
            if rate:
                break

    page_text = " ".join(soup.stripped_strings)

    # Fallback if the price isn't exposed in a table.
    if not rate:
        match = re.search(
            r"1\s*Gm\s*Gold\s*22\s*Kt\s*"
            r"(?:₹|Rs\.?\s*)?\s*([\d,]+(?:\.\d{1,2})?)",
            page_text,
            re.IGNORECASE,
        )
        if match:
            rate = round(float(match.group(1).replace(",", "")))

    if not rate or rate <= 0:
        raise RuntimeError(
            "Could not reliably extract CWJA's 22K rate. "
            "No guessed price will be used."
        )

    timestamp_match = re.search(
        r"Update\s*Time\s*:\s*"
        r"(\d{1,2}-\d{1,2}-\d{4}\s+"
        r"\d{1,2}:\d{2}:\d{2}\s*[ap]m)",
        page_text,
        re.IGNORECASE,
    )

    if not timestamp_match:
        raise RuntimeError(
            "CWJA's expected Update Time was not found. "
            "Refusing to send an unverified report."
        )

    source_updated = datetime.strptime(
        timestamp_match.group(1).upper(),
        "%d-%m-%Y %I:%M:%S %p",
    ).replace(tzinfo=IST)

    now = datetime.now(IST)

    return {
        "rate_per_gram": rate,
        "source": CHENNAI_SOURCE,
        "source_url": CHENNAI_URL,
        "source_updated": source_updated.isoformat(),
        "source_updated_label": source_updated.strftime(
            "%d %b %Y, %I:%M:%S %p IST"
        ),
        "fetched_at": now.isoformat(),
        "stale_days": (now.date() - source_updated.date()).days,
    }


def load_history() -> dict:
    """Load saved daily rates."""
    if not HISTORY_FILE.exists():
        return {}

    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(
            f"Could not read {HISTORY_FILE}: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise RuntimeError("history.json must contain a JSON object.")

    return data


def save_history(history: dict) -> None:
    """Persist daily rates to history.json."""
    HISTORY_FILE.write_text(
        json.dumps(history, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def compare_to_yesterday(
    today_rate: int,
    history: dict,
    today_key: str,
    region: str,
) -> dict:
    """Compare one region's rate with its previous calendar day's rate."""
    yesterday_key = (
        datetime.fromisoformat(today_key).date() - timedelta(days=1)
    ).isoformat()

    yesterday_record = history.get(yesterday_key, {})
    previous = (
        yesterday_record.get(region)
        if isinstance(yesterday_record, dict)
        else None
    )

    # Backward compatibility with the original Chennai-only history format.
    if (
        region == "chennai"
        and previous is None
        and isinstance(yesterday_record, dict)
        and "rate_per_gram" in yesterday_record
    ):
        previous = yesterday_record

    if not isinstance(previous, dict) or "rate_per_gram" not in previous:
        return {
            "status": "baseline",
            "label": "Baseline — no saved rate for yesterday",
            "delta": None,
            "previous_date": yesterday_key,
        }

    previous_rate = int(previous["rate_per_gram"])
    delta = today_rate - previous_rate

    if delta > 0:
        status, label = "increased", "PRICE INCREASED"
    elif delta < 0:
        status, label = "decreased", "PRICE DECREASED"
    else:
        status, label = "unchanged", "NO CHANGE"

    return {
        "status": status,
        "label": label,
        "delta": delta,
        "previous_rate": previous_rate,
        "previous_date": yesterday_key,
    }


def comparison_text(comparison: dict) -> str:
    """Create a readable price-change summary."""
    delta = comparison["delta"]

    if delta is None:
        return (
            "No previous-day snapshot is available. "
            "This report establishes the baseline."
        )

    if delta > 0:
        return (
            f"↑ Increased by {money(delta)} per gram "
            f"({money(delta * 8)} per sovereign) "
            f"versus {comparison['previous_date']}."
        )

    if delta < 0:
        return (
            f"↓ Decreased by {money(abs(delta))} per gram "
            f"({money(abs(delta) * 8)} per sovereign) "
            f"versus {comparison['previous_date']}."
        )

    return f"No change versus {comparison['previous_date']}."


def build_region_html(
    title: str,
    market: dict,
    comparison: dict,
) -> str:
    """Build one region's section for the HTML email with a modern card design."""
    rate = int(market["rate_per_gram"])
    sovereign = rate * 8

    # Professional color scheme matching status
    colors = {
        "increased": {"border": "#dc2626", "bg": "#fef2f2", "text": "#991b1b"},
        "decreased": {"border": "#16a34a", "bg": "#f0fdf4", "text": "#166534"},
        "unchanged": {"border": "#64748b", "bg": "#f8fafc", "text": "#334155"},
        "baseline": {"border": "#d97706", "bg": "#fffbeb", "text": "#92400e"},
    }
    theme = colors[comparison["status"]]
    change = html.escape(comparison_text(comparison))

    stale_notice = ""
    if market["stale_days"] > 2:
        stale_notice = (
            '<div style="padding:12px;background:#fff7ed;color:#9a3412;'
            'border-left:4px solid #ea580c;border-radius:6px;margin-bottom:16px;'
            'font-size:13px;">'
            f'⚠️ <b>Warning:</b> The source publication is {market["stale_days"]} '
            'days old. Please verify rates before relying on them.'
            '</div>'
        )

    source = html.escape(market["source"])
    source_url = html.escape(market["source_url"], quote=True)
    updated = html.escape(market["source_updated_label"])

    return f"""
    <div style="margin-top:28px;margin-bottom:28px;">
      <h2 style="font-size:18px;color:#1e293b;margin:0 0 12px 0;padding-bottom:6px;border-bottom:2px solid #e2e8f0;">
        {html.escape(title)}
      </h2>
      {stale_notice}
      
      <!-- Price Cards Grid -->
      <table role="presentation" style="width:100%;border-collapse:separate;border-spacing:0 10px;">
        <tr>
          <td style="padding:16px 20px;background:#ffffff;border:1px solid #e2e8f0;border-top:4px solid #d97706;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,0.05);">
            <div style="font-size:12px;text-transform:uppercase;letter-spacing:0.5px;color:#64748b;font-weight:600;">22K Gold · 1 Gram</div>
            <div style="font-size:26px;font-weight:700;color:#0f172a;margin-top:6px;">
              {money(rate)}
            </div>
          </td>
        </tr>
        <tr>
          <td style="padding:16px 20px;background:#ffffff;border:1px solid #e2e8f0;border-top:4px solid #b45309;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,0.05);">
            <div style="font-size:12px;text-transform:uppercase;letter-spacing:0.5px;color:#64748b;font-weight:600;">22K Gold · 1 Sovereign (8 Grams)</div>
            <div style="font-size:26px;font-weight:700;color:#0f172a;margin-top:6px;">
              {money(sovereign)}
            </div>
          </td>
        </tr>
      </table>

      <!-- Comparison Banner -->
      <div style="padding:14px 16px;border-left:4px solid {theme['border']};background:{theme['bg']};color:{theme['text']};border-radius:6px;margin-top:12px;font-size:14px;">
        <div style="font-weight:700;margin-bottom:3px;">{html.escape(comparison['label'])}</div>
        <div style="font-size:13px;opacity:0.9;">{change}</div>
      </div>

      <!-- Source Meta Details -->
      <div style="margin-top:10px;padding:4px 2px;font-size:12px;color:#64748b;line-height:1.5;">
        <b>Source:</b> <a href="{source_url}" style="color:#2563eb;text-decoration:none;">{source}</a><br>
        <b>Published:</b> {updated}<br>
        <b>Fetched:</b> {html.escape(datetime.fromisoformat(market['fetched_at']).astimezone(IST).strftime('%d %b %Y, %I:%M %p IST'))}
      </div>
    </div>
    """


def build_email(
    today_key: str,
    india: dict,
    chennai: dict,
    india_comparison: dict,
    chennai_comparison: dict,
) -> tuple[str, str, str]:
    """Create subject, plain-text body, and HTML body."""
    subject = f"✨ Daily 22K Gold Price Report — India & Chennai — {today_key}"

    # India is intentionally displayed first.
    plain = (
        f"DAILY 22K GOLD PRICE REPORT — {today_key}\n\n"
        "================ INDIA ================\n"
        f"1 gram: {money(india['rate_per_gram'])}\n"
        f"1 sovereign (8 grams): {money(india['rate_per_gram'] * 8)}\n"
        f"Comparison: {india_comparison['label']}\n"
        f"{comparison_text(india_comparison)}\n"
        f"Source: {india['source']} ({india['source_url']})\n"
        f"Published: {india['source_updated_label']}\n\n"
        "============== CHENNAI ================\n"
        f"1 gram: {money(chennai['rate_per_gram'])}\n"
        f"1 sovereign (8 grams): "
        f"{money(chennai['rate_per_gram'] * 8)}\n"
        f"Comparison: {chennai_comparison['label']}\n"
        f"{comparison_text(chennai_comparison)}\n"
        f"Source: {chennai['source']} ({chennai['source_url']})\n"
        f"Published: {chennai['source_updated_label']}\n\n"
        "Note: These are indicative association-published rates. "
        "GST and making charges may be extra. Actual jewellery prices "
        "may differ. Verify with your jeweller before purchasing.\n"
    )

    html_body = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Daily Gold Price Report</title>
</head>
<body style="margin:0;padding:0;background-color:#f8fafc;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#0f172a;">
  <table role="presentation" style="width:100%;border-collapse:collapse;background-color:#f8fafc;padding:20px 0;">
    <tr>
      <td align="center">
        <!-- Main Email Container -->
        <table role="presentation" style="width:100%;max-width:600px;border-collapse:collapse;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 4px 6px -1px rgba(0,0,0,0.1),0 2px 4px -1px rgba(0,0,0,0.06);">
          
          <!-- Header Banner -->
          <tr>
            <td style="background:linear-gradient(135deg, #1e293b 0%, #0f172a 100%);padding:32px 24px;text-align:center;color:#ffffff;">
              <div style="font-size:12px;text-transform:uppercase;letter-spacing:2px;color:#fbbf24;font-weight:700;margin-bottom:8px;">Daily Market Report</div>
              <h1 style="margin:0;font-size:24px;font-weight:700;color:#ffffff;">22K Gold Price Tracker</h1>
              <p style="margin:8px 0 0 0;font-size:14px;color:#94a3b8;">Report Date: <b>{html.escape(today_key)}</b></p>
            </td>
          </tr>

          <!-- Content Body -->
          <tr>
            <td style="padding:32px 24px;">
              {build_region_html("1. India — IBJA", india, india_comparison)}
              
              <div style="height:1px;background-color:#e2e8f0;margin:16px 0;"></div>

              {build_region_html("2. Chennai — CWJA", chennai, chennai_comparison)}
            </td>
          </tr>

          <!-- Footer Disclaimer -->
          <tr>
            <td style="background-color:#f1f5f9;padding:20px 24px;font-size:12px;color:#64748b;line-height:1.6;border-top:1px solid #e2e8f0;">
              <p style="margin:0 0 8px 0;"><b>Disclaimer:</b> Indicative association-published rates only. IBJA retail rates exclude 3% GST and making charges. Actual retail jewellery prices differ. Verify with your jeweller before purchase.</p>
              <p style="margin:0;">Generated automatically via GitHub Actions &bull; Gold Price Tracker</p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

    return subject, plain, html_body


def send_email(
    subject: str,
    plain_body: str,
    html_body: str,
) -> None:
    """Send the report through Gmail SMTP."""
    username = os.environ.get("MAIL_USERNAME", "").strip()
    app_password = os.environ.get("MAIL_APP_PASSWORD", "").replace(" ", "")
    recipient = os.environ.get("RECIPIENT_EMAIL", "").strip()

    missing = [
        name
        for name, value in (
            ("MAIL_USERNAME", username),
            ("MAIL_APP_PASSWORD", app_password),
            ("RECIPIENT_EMAIL", recipient),
        )
        if not value
    ]

    if missing:
        raise RuntimeError(
            "Missing GitHub Actions secret(s): " + ", ".join(missing)
        )

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = username
    message["To"] = recipient
    message.set_content(plain_body)
    message.add_alternative(html_body, subtype="html")

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(username, app_password)
        server.send_message(message)


def main() -> None:
    today_key = datetime.now(IST).date().isoformat()

    # Load existing history before calculating either comparison.
    history = load_history()

    # Fetch both official association-published rates.
    india = fetch_india_22k_rate()
    chennai = fetch_chennai_22k_rate()

    india_comparison = compare_to_yesterday(
        india["rate_per_gram"], history, today_key, "india"
    )
    chennai_comparison = compare_to_yesterday(
        chennai["rate_per_gram"], history, today_key, "chennai"
    )

    # Store both rates under the same India calendar date.
    history[today_key] = {
        "india": {
            "rate_per_gram": india["rate_per_gram"],
            "source": india["source"],
            "source_updated": india["source_updated"],
            "fetched_at": india["fetched_at"],
        },
        "chennai": {
            "rate_per_gram": chennai["rate_per_gram"],
            "source": chennai["source"],
            "source_updated": chennai["source_updated"],
            "fetched_at": chennai["fetched_at"],
        },
    }

    # Save locally. GitHub Actions will commit this file after success.
    save_history(history)

    subject, plain, html_body = build_email(
        today_key,
        india,
        chennai,
        india_comparison,
        chennai_comparison,
    )

    send_email(subject, plain, html_body)

    print(f"Email sent for {today_key}.")
    print(
        f"India: ₹{india['rate_per_gram']}/g; "
        f"status={india_comparison['status']}; "
        f"source date={india['source_updated']}"
    )
    print(
        f"Chennai: ₹{chennai['rate_per_gram']}/g; "
        f"status={chennai_comparison['status']}; "
        f"source date={chennai['source_updated']}"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
