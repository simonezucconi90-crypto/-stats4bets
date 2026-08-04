import csv
import json
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

LOGIN_URL = "https://superfoglio.it/"
TARGET_URL = "https://superfoglio.it/stats_table.php?q=mindbet"

USER = os.environ["STATS4BETS_USER"]
PASSWORD = os.environ["STATS4BETS_PASSWORD"]

OUT = Path("collector_artifacts")
OUT.mkdir(exist_ok=True)


def first_visible(page, selectors):
    for selector in selectors:
        locator = page.locator(selector)
        try:
            if locator.count() > 0 and locator.first.is_visible():
                return locator.first
        except Exception:
            continue
    return None


def login(page):
    email = first_visible(
        page,
        [
            'input[type="email"]',
            'input[name*="email" i]',
            'input[id*="email" i]',
            'input[placeholder*="email" i]',
            'input[type="text"]',
        ],
    )
    password = first_visible(
        page,
        [
            'input[type="password"]',
            'input[name*="pass" i]',
            'input[id*="pass" i]',
            'input[placeholder*="password" i]',
        ],
    )

    if email is None or password is None:
        raise RuntimeError("Campi login non trovati.")

    email.fill(USER)
    password.fill(PASSWORD)

    checkbox = first_visible(
        page,
        [
            'input[type="checkbox"]',
            'input[name*="term" i]',
            'input[id*="term" i]',
            'input[name*="privacy" i]',
            'input[id*="privacy" i]',
        ],
    )

    if checkbox is not None:
        try:
            if not checkbox.is_checked():
                checkbox.check(force=True)
        except Exception:
            pass

    submit = first_visible(
        page,
        [
            'button[type="submit"]',
            'input[type="submit"]',
            'button:has-text("Login")',
            'button:has-text("Accedi")',
        ],
    )

    if submit is None:
        password.press("Enter")
    else:
        submit.click()

    try:
        page.wait_for_load_state("networkidle", timeout=30000)
    except PlaywrightTimeoutError:
        page.wait_for_timeout(5000)


def save_debug(page):
    page.screenshot(
        path=str(OUT / "mindbet_page.png"),
        full_page=True,
    )
    (OUT / "mindbet_page.html").write_text(
        page.content(),
        encoding="utf-8",
    )
    (OUT / "mindbet_page_url.txt").write_text(
        page.url,
        encoding="utf-8",
    )


def card_text(link):
    locator = link

    for _ in range(8):
        try:
            text = re.sub(
                r"\s+",
                " ",
                locator.inner_text(timeout=2000),
            ).strip()
        except Exception:
            text = ""

        if (
            "OTTIMO 1" in text.upper()
            and re.search(r"\d{2}-\d{2}-\d{2}", text)
        ):
            return text

        locator = locator.locator("..")

    return ""


def parse_date(text):
    match = re.search(r"(\d{2})-(\d{2})-(\d{2})", text)

    if not match:
        return ""

    day, month, year = match.groups()
    return f"20{year}-{month}-{day}"


def parse_time(text):
    match = re.search(
        r"\b([01]\d|2[0-3]):[0-5]\d\b",
        text,
    )
    return match.group(0) if match else ""


def parse_odds(text):
    values = re.findall(
        r"(?<![\d-])(\d{1,2}[.,]\d{2})(?!\d)",
        text,
    )
    return values[-1].replace(",", ".") if values else ""


def collect_today_ottimo1(page):
    today = datetime.now().strftime("%Y-%m-%d")
    anchors = page.locator('a[href*="statistiche.php?id="]')

    seen_urls = set()
    rows = []

    for index in range(anchors.count()):
        anchor = anchors.nth(index)
        href = anchor.get_attribute("href")

        if not href:
            continue

        detail_url = urljoin(page.url, href)

        if detail_url in seen_urls:
            continue

        text = card_text(anchor)

        if "OTTIMO 1" not in text.upper():
            continue

        if parse_date(text) != today:
            continue

        seen_urls.add(detail_url)

        rows.append(
            {
                "date": parse_date(text),
                "time": parse_time(text),
                "selection": "Ottimo 1",
                "odds": parse_odds(text),
                "detail_url": detail_url,
                "raw_card_text": text,
            }
        )

    return rows


def save_rows(rows):
    json_path = OUT / "ottimo1_today.json"
    csv_path = OUT / "ottimo1_today.csv"

    json_path.write_text(
        json.dumps(
            rows,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    fieldnames = [
        "date",
        "time",
        "selection",
        "odds",
        "detail_url",
        "raw_card_text",
    ]

    with csv_path.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
        )
        writer.writeheader()
        writer.writerows(rows)


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)

        context = browser.new_context(
            locale="it-IT",
            timezone_id="Europe/Rome",
            viewport={
                "width": 1440,
                "height": 1200,
            },
        )

        page = context.new_page()

        page.goto(
            LOGIN_URL,
            wait_until="domcontentloaded",
            timeout=60000,
        )
        page.wait_for_timeout(2500)

        if page.locator('input[type="password"]').count() > 0:
            login(page)

        page.goto(
            TARGET_URL,
            wait_until="domcontentloaded",
            timeout=60000,
        )
        page.wait_for_timeout(5000)

        save_debug(page)

        if page.locator('input[type="password"]').count() > 0:
            raise RuntimeError(
                "Login non riuscito o sessione non mantenuta."
            )

        rows = collect_today_ottimo1(page)
        save_rows(rows)

        print(f"Ottimo 1 trovate oggi: {len(rows)}")

        for row in rows:
            print(row)

        if not rows:
            raise RuntimeError(
                "Nessuna Ottimo 1 trovata oggi. "
                "Controlla gli artifact del workflow."
            )

        browser.close()


if __name__ == "__main__":
    main()
