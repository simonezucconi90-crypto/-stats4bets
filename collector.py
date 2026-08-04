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
        raise RuntimeError("Contenitore delle partite non trovato.")

    today = datetime.now(ZoneInfo("Europe/Rome")).strftime("%Y-%m-%d")
    rows = []
    seen_ids = set()

    for card in mobile_grid.find_all("div", recursive=False):
        match_id = clean_text(card.get("id", ""))
        if not match_id.isdigit() or match_id in seen_ids:
            continue

        blocks = card.find_all("div", recursive=False)
        if len(blocks) < 5:
            continue

        date_parts = [clean_text(x) for x in blocks[0].stripped_strings]
        date_text = date_parts[0] if len(date_parts) > 0 else ""
        time_text = date_parts[1] if len(date_parts) > 1 else ""
        status = date_parts[2] if len(date_parts) > 2 else ""
        date_iso = date_to_iso(date_text)

        league_link = blocks[1].find("a")
        league = clean_text(league_link.get_text(" ", strip=True)) if league_link else ""
        round_name = ""
        league_spans = blocks[1].find_all("span", recursive=False)
        if len(league_spans) > 1:
            round_name = clean_text(league_spans[1].get_text(" ", strip=True))

        teams_span = blocks[2].find(
            "span",
            class_=lambda classes: classes and "labelm" in classes,
        )
        teams = [clean_text(x) for x in teams_span.stripped_strings] if teams_span else []
        home_team = teams[0] if len(teams) > 0 else ""
        away_team = teams[1] if len(teams) > 1 else ""

        prediction_spans = blocks[3].find_all("span", recursive=False)
        prediction = clean_text(prediction_spans[0].get_text(" ", strip=True)) if prediction_spans else ""
        odds = clean_text(prediction_spans[1].get_text(" ", strip=True)).replace(",", ".") if len(prediction_spans) > 1 else ""

        detail_link = card.select_one('a[href*="statistiche.php?id="]')
        detail_url = urljoin(TARGET_URL, detail_link.get("href")) if detail_link else ""

        if date_iso != today or prediction.casefold() != "ottimo 1":
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
                "selected_by_ale": prediction,
                "list_odds": odds,
                "detail_url": detail_url,
            }
        )

    rows.sort(key=lambda row: (row["date"], row["time"], row["match_id"]))
    return rows


def text_after_label(text, label, value_pattern=r"([A-Z]{2}|-|\d+(?:[.,]\d+)?)"):
    match = re.search(rf"{label}\s*{value_pattern}", text, flags=re.I)
    return clean_text(match.group(1)) if match else ""


def parse_probabilities(text):
    match = re.search(
        r"PROBABILITA['’]?\s*1X2.*?\b1\s+X\s+2\s+"
        r"(\d+(?:[.,]\d+)?)%\s+(\d+(?:[.,]\d+)?)%\s+(\d+(?:[.,]\d+)?)%",
        text,
        flags=re.I | re.S,
    )
    if not match:
        return 0.0, 0.0, 0.0
    return tuple(float(value.replace(",", ".")) for value in match.groups())


def parse_three_odds(text, label):
    match = re.search(
        rf"{label}\s+(\d+(?:[.,]\d+)?)\s+(\d+(?:[.,]\d+)?)\s+(\d+(?:[.,]\d+)?)",
        text,
        flags=re.I,
    )
    if not match:
        return 0.0, 0.0, 0.0
    return tuple(float(value.replace(",", ".")) for value in match.groups())


def parse_detail_html(html, base):
    soup = BeautifulSoup(html, "html.parser")
    text = clean_text(soup.get_text(" ", strip=True))

    prob_1, prob_x, prob_2 = parse_probabilities(text)
    quota_reale_1, quota_reale_x, quota_reale_2 = parse_three_odds(text, r"QUOTA COPPE ALLIBRATA")
    quota_iniziale_1, quota_iniziale_x, quota_iniziale_2 = parse_three_odds(text, r"QUOTA INIZIALE")
    quota_attuale_1, quota_attuale_x, quota_attuale_2 = parse_three_odds(text, r"QUOTA ATTUALE")

    record = dict(base)
    record.update(
        {
            "market": "1X2",
            "pick": "1",
            "prob_1": prob_1,
            "prob_x": prob_x,
            "prob_2": prob_2,
            "fair_odds": quota_reale_1,
            "opening_odds": quota_iniziale_1,
            "current_odds": quota_attuale_1,
            "c_aff": text_after_label(text, r"C\.\s*AFF\."),
            "flbk": text_after_label(text, r"(?:FLBK|C\.\s*FLB\.)"),
            "c_fb": text_after_label(text, r"C\.\s*FB\."),
            "qra_qa": text_after_label(text, r"QRA/QA"),
            "qi_qa": text_after_label(text, r"QI/QA"),
            "allb": text_after_label(text, r"ALLB"),
            "mtr": text_after_label(text, r"MTR"),
            "scl": text_after_label(text, r"SCL"),
            "cal": text_after_label(text, r"CAL"),
            "detail_status": text_after_label(text, r"STATUS"),
            "associated_method": "",
            "allibramento_value": 0.0,
            "allibramento_color": "",
            "allibramento_avg": 0.0,
            "quota_reale_x": quota_reale_x,
            "quota_reale_2": quota_reale_2,
            "quota_iniziale_x": quota_iniziale_x,
            "quota_iniziale_2": quota_iniziale_2,
            "quota_attuale_x": quota_attuale_x,
            "quota_attuale_2": quota_attuale_2,
        }
    )

    method_patterns = {
        "1X2": r"\b1X2\b",
        "Over 1.5": r"\bOver 1[.,]5\b",
        "Over 2.5": r"\bOver 2[.,]5\b",
        "Under 2.5": r"\bUnder 2[.,]5\b",
        "Under 3.5": r"\bUnder 3[.,]5\b",
        "Multigol 1-3": r"\bMultigol 1\s*[-–]\s*3\b",
        "Multigol 1-4": r"\bMultigol 1\s*[-–]\s*4\b",
        "Formula 4": r"\bFormula 4\b",
        "Easy Over": r"\bEasy Over\b",
        "Super Over": r"\bSuper Over\b",
    }
    methods = [name for name, pattern in method_patterns.items() if re.search(pattern, text, flags=re.I)]
    record["associated_method"] = " | ".join(methods)

    allibramento = re.search(
        r"ALLIBRAMENTO\s+(\d+(?:[.,]\d+)?)\s+(VE|GI|VI|RO)",
        text,
        flags=re.I,
    )
    if allibramento:
        record["allibramento_value"] = float(allibramento.group(1).replace(",", "."))
        record["allibramento_color"] = allibramento.group(2).upper()

    medio = re.search(r"ALLIBRAMENTO MEDIO\s+(\d+(?:[.,]\d+)?)", text, flags=re.I)
    if medio:
        record["allibramento_avg"] = float(medio.group(1).replace(",", "."))

    return record


def save_outputs(rows):
    json_path = OUT / "ottimo1_details.json"
    csv_path = OUT / "ottimo1_details.csv"

    json_path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    fieldnames = sorted({key for row in rows for key in row.keys()})
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


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
            raise RuntimeError("Login non riuscito.")

        matches = parse_mobile_cards(page.content())
        if not matches:
            raise RuntimeError("Nessuna Ottimo 1 trovata oggi.")

        detail_rows = []

        for index, match in enumerate(matches, start=1):
            print(f'[{index}/{len(matches)}] Apro {match["match_name"]}')
            page.goto(match["detail_url"], wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3500)

            html = page.content()
            safe_id = match["match_id"]
            (OUT / f"detail_{safe_id}.html").write_text(html, encoding="utf-8")
            page.screenshot(
                path=str(OUT / f"detail_{safe_id}.png"),
                full_page=True,
            )

            parsed = parse_detail_html(html, match)
            detail_rows.append(parsed)

        save_outputs(detail_rows)

        print(f"Dettagli estratti: {len(detail_rows)}")
        for row in detail_rows:
            print(
                f'{row["match_name"]} | P1={row["prob_1"]} | '
                f'QA1={row["current_odds"]} | '
                f'ALLB={row["allibramento_color"]} '
                f'{row["allibramento_value"]}'
            )

        browser.close()


if __name__ == "__main__":
    main()
