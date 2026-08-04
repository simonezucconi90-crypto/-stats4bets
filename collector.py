import csv
import json
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
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


def clean_text(value):
    return re.sub(r"\s+", " ", value or "").strip()


def parse_date_time_status(block):
    parts = [clean_text(x) for x in block.stripped_strings]
    date = parts[0] if len(parts) > 0 else ""
    time = parts[1] if len(parts) > 1 else ""
    status = parts[2] if len(parts) > 2 else ""
    return date, time, status


def date_to_iso(value):
    match = re.fullmatch(r"(\d{2})-(\d{2})-(\d{2})", value)
    if not match:
        return ""
    day, month, year = match.groups()
    return f"20{year}-{month}-{day}"


def parse_mobile_cards(html):
    soup = BeautifulSoup(html, "html.parser")

    mobile_grid = soup.select_one("div.grid.lg\\:hidden.grid-cols-1.gap-6.mb-6")
    if mobile_grid is None:
        raise RuntimeError("Contenitore mobile delle partite non trovato.")

    today = datetime.now(ZoneInfo("Europe/Rome")).strftime("%Y-%m-%d")
    rows = []
    seen_ids = set()

    for card in mobile_grid.find_all("div", recursive=False):
        match_id = clean_text(card.get("id", ""))
        if not match_id.isdigit() or match_id in seen_ids:
            continue

        direct_blocks = card.find_all("div", recursive=False)
        if len(direct_blocks) < 5:
            continue

        date_text, time_text, status = parse_date_time_status(direct_blocks[0])
        date_iso = date_to_iso(date_text)

        league_link = direct_blocks[1].find("a")
        league = clean_text(league_link.get_text(" ", strip=True)) if league_link else ""
        round_name = ""
        league_spans = direct_blocks[1].find_all("span", recursive=False)
        if len(league_spans) > 1:
            round_name = clean_text(league_spans[1].get_text(" ", strip=True))

        teams_span = direct_blocks[2].find(
            "span",
            class_=lambda classes: classes and "labelm" in classes,
        )
        teams = []
        if teams_span:
            teams = [clean_text(x) for x in teams_span.stripped_strings if clean_text(x)]

        home_team = teams[0] if len(teams) > 0 else ""
        away_team = teams[1] if len(teams) > 1 else ""

        prediction_block = direct_blocks[3]
        prediction_spans = prediction_block.find_all("span", recursive=False)
        prediction = (
            clean_text(prediction_spans[0].get_text(" ", strip=True))
            if len(prediction_spans) > 0
            else ""
        )
        odds = (
            clean_text(prediction_spans[1].get_text(" ", strip=True)).replace(",", ".")
            if len(prediction_spans) > 1
            else ""
        )
        result = (
            clean_text(prediction_spans[2].get_text(" ", strip=True))
            if len(prediction_spans) > 2
            else ""
        )

        detail_link = card.select_one('a[href*="statistiche.php?id="]')
        detail_url = urljoin(TARGET_URL, detail_link.get("href")) if detail_link else ""

        if date_iso != today:
            continue
        if prediction.casefold() != "ottimo 1":
            continue

        seen_ids.add(match_id)
        rows.append(
            {
                "match_id": match_id,
                "date": date_iso,
                "time": time_text,
                "status": status,
                "league": league,
                "round_name": round_name,
                "home_team": home_team,
                "away_team": away_team,
                "match_name": f"{home_team} - {away_team}".strip(" -"),
                "selection": prediction,
                "odds": odds,
                "result": result,
                "detail_url": detail_url,
            }
        )

    rows.sort(key=lambda row: (row["date"], row["time"], row["match_id"]))
    return rows


def save_results(rows):
    json_path = OUT / "ottimo1_today.json"
    csv_path = OUT / "ottimo1_today.csv"

    json_path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    fields = [
        "match_id",
        "date",
        "time",
        "status",
        "league",
        "round_name",
        "home_team",
        "away_team",
        "match_name",
        "selection",
        "odds",
        "result",
        "detail_url",
    ]

    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def save_debug(page):
    page.screenshot(
        path=str(OUT / "mindbet_page.png"),
        full_page=True,
    )
    (OUT / "mindbet_page.html").write_text(
        page.content(),
        encoding="utf-8",
    )


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            locale="it-IT",
            timezone_id="Europe/Rome",
            viewport={"width": 1440, "height": 1200},
        )
        page = context.new_page()

        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)

        if page.locator('input[type="password"]').count() > 0:
            login(page)

        page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)

        if page.locator('input[type="password"]').count() > 0:
            raise RuntimeError("Login non riuscito o sessione non mantenuta.")

        save_debug(page)

        rows = parse_mobile_cards(page.content())
        save_results(rows)

        print(f"Ottimo 1 trovate oggi: {len(rows)}")
        for row in rows:
            print(
                f'{row["time"]} | {row["match_name"]} | '
                f'{row["league"]} | {row["odds"]} | {row["detail_url"]}'
            )

        if not rows:
            raise RuntimeError(
                "Nessuna Ottimo 1 trovata per la data corrente. "
                "Controlla mindbet_page.html negli artifact."
            )

        browser.close()


if __name__ == "__main__":
    main()
