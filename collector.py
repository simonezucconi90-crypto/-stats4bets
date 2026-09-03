import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright
from supabase import create_client

LOGIN_URL = "https://superfoglio.it/"
TARGET_URL = "https://superfoglio.it/stats_table.php?q=mindbet"

USER = os.environ["STATS4BETS_USER"]
PASSWORD = os.environ["STATS4BETS_PASSWORD"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

OUT = Path("collector_artifacts")
OUT.mkdir(exist_ok=True)

METHOD_COLUMNS = {
    "1X2": "flag_1x2",
    "Over 1.5": "flag_over_15",
    "Over 2.5": "flag_over_25",
    "Under 2.5": "flag_under_25",
    "Under 3.5": "flag_under_35",
    "Multigol 1-3": "flag_multigol_13",
    "Multigol 1-4": "flag_multigol_14",
    "Formula 4": "flag_formula4",
    "Easy Over": "flag_easy_over",
    "Super Over": "flag_super_over",
}


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
    email = first_visible(page, [
        'input[type="email"]',
        'input[name*="email" i]',
        'input[id*="email" i]',
        'input[placeholder*="email" i]',
        'input[type="text"]',
    ])
    password = first_visible(page, [
        'input[type="password"]',
        'input[name*="pass" i]',
        'input[id*="pass" i]',
        'input[placeholder*="password" i]',
    ])

    if email is None or password is None:
        raise RuntimeError("Campi login non trovati.")

    email.fill(USER)
    password.fill(PASSWORD)

    checkbox = first_visible(page, ['input[type="checkbox"]'])
    if checkbox is not None:
        try:
            if not checkbox.is_checked():
                checkbox.check(force=True)
        except Exception:
            pass

    submit = first_visible(page, [
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("Login")',
        'button:has-text("Accedi")',
    ])

    if submit is None:
        password.press("Enter")
    else:
        submit.click()

    try:
        page.wait_for_load_state("networkidle", timeout=30000)
    except PlaywrightTimeoutError:
        page.wait_for_timeout(5000)


def clean(value):
    return re.sub(r"\s+", " ", value or "").strip()


def parse_number(value, default=0.0):
    value = clean(str(value)).replace(",", ".")
    match = re.search(r"(?<!\d)(\d+(?:\.\d+)?)(?!\d)", value)
    return float(match.group(1)) if match else default


def date_to_iso(value):
    match = re.fullmatch(r"(\d{2})-(\d{2})-(\d{2})", value)
    if not match:
        return ""
    day, month, year = match.groups()
    return f"20{year}-{month}-{day}"


def parse_list(html):
    soup = BeautifulSoup(html, "html.parser")
    grid = soup.select_one("div.grid.lg\\:hidden.grid-cols-1.gap-6.mb-6")
    if grid is None:
        raise RuntimeError("Griglia partite non trovata.")

    today = datetime.now(ZoneInfo("Europe/Rome")).strftime("%Y-%m-%d")
    rows, seen = [], set()

    for card in grid.find_all("div", recursive=False):
        source_id = clean(card.get("id", ""))
        if not source_id.isdigit() or source_id in seen:
            continue

        blocks = card.find_all("div", recursive=False)
        if len(blocks) < 5:
            continue

        date_parts = [clean(x) for x in blocks[0].stripped_strings]
        date_iso = date_to_iso(date_parts[0] if len(date_parts) > 0 else "")
        time_text = date_parts[1] if len(date_parts) > 1 else ""
        match_status = date_parts[2] if len(date_parts) > 2 else ""

        league_link = blocks[1].find("a")
        league = clean(league_link.get_text(" ", strip=True)) if league_link else ""
        league_spans = blocks[1].find_all("span", recursive=False)
        round_name = clean(league_spans[1].get_text(" ", strip=True)) if len(league_spans) > 1 else ""

        teams_span = blocks[2].find("span", class_=lambda c: c and "labelm" in c)
        teams = [clean(x) for x in teams_span.stripped_strings] if teams_span else []
        home = teams[0] if len(teams) > 0 else ""
        away = teams[1] if len(teams) > 1 else ""

        prediction_spans = blocks[3].find_all("span", recursive=False)
        prediction = clean(prediction_spans[0].get_text(" ", strip=True)) if prediction_spans else ""
        list_odds_text = clean(prediction_spans[1].get_text(" ", strip=True)) if len(prediction_spans) > 1 else ""
        list_odds = parse_number(list_odds_text)

        detail_link = card.select_one('a[href*="statistiche.php?id="]')
        detail_url = urljoin(TARGET_URL, detail_link.get("href")) if detail_link else ""

        if date_iso != today or prediction.casefold() != "ottimo 1":
            continue

        seen.add(source_id)
        rows.append({
            "source_match_id": source_id,
            "date": date_iso,
            "time": time_text,
            "match_status": match_status,
            "league": league,
            "round_name": round_name,
            "home_team": home,
            "away_team": away,
            "match_name": f"{home} - {away}".strip(" -"),
            "selected_by_ale": prediction,
            "list_odds": list_odds,
            "detail_url": detail_url,
        })

    return rows


def flat_text(html):
    return clean(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))


def number_after(text, pattern):
    match = re.search(pattern + r"\s+(\d+(?:[.,]\d+)?)", text, re.I)
    return float(match.group(1).replace(",", ".")) if match else 0.0


def three_values(text, label):
    match = re.search(
        label + r"\s+(\d+(?:[.,]\d+)?)\s+(\d+(?:[.,]\d+)?)\s+(\d+(?:[.,]\d+)?)",
        text, re.I
    )
    if not match:
        return 0.0, 0.0, 0.0
    return tuple(float(v.replace(",", ".")) for v in match.groups())


def colour_after(text, label):
    match = re.search(label + r"\s+(VE|GI|VI|RO)", text, re.I)
    return match.group(1).upper() if match else ""


def parse_detail(html, base):
    text = flat_text(html)

    c_aff_count_match = re.search(r"\b(\d+)\s+C\.\s*AFF\.", text, re.I)
    c_aff_count = int(c_aff_count_match.group(1)) if c_aff_count_match else None

    probability = re.search(
        r"PROBABILITA['’]?\s*1X2.*?\b1\s+X\s+2\s+"
        r"(\d+(?:[.,]\d+)?)%\s+(\d+(?:[.,]\d+)?)%\s+(\d+(?:[.,]\d+)?)%",
        text, re.I
    )
    if probability:
        prob_1, prob_x, prob_2 = [float(v.replace(",", ".")) for v in probability.groups()]
    else:
        prob_1 = prob_x = prob_2 = 0.0

    fair_1, _, _ = three_values(text, r"QUOTA COPPE ALLIBRATA")
    opening_1, _, _ = three_values(text, r"QUOTA INIZIALE")
    current_1, _, _ = three_values(text, r"QUOTA ATTUALE")

    allibramento = re.search(
        r"ALLIBRAMENTO\s+(\d+(?:[.,]\d+)?)\s+(VE|GI|VI|RO)", text, re.I
    )
    allib_value = float(allibramento.group(1).replace(",", ".")) if allibramento else 0.0
    allib_colour = allibramento.group(2).upper() if allibramento else ""
    allib_avg = number_after(text, r"ALLIBRAMENTO MEDIO")

    indicators = {
        "c_aff": colour_after(text, r"C\.\s*AFF\."),
        "flbk": colour_after(text, r"(?:FLBK|C\.\s*FLB\.)"),
        "c_fb": colour_after(text, r"C\.\s*FB\."),
        "qra_qa": colour_after(text, r"QRA/QA"),
        "qi_qa": colour_after(text, r"QI/QA"),
        "allb": colour_after(text, r"ALLB"),
        "mtr": colour_after(text, r"MTR"),
        "scl": colour_after(text, r"SCL"),
        "cal": colour_after(text, r"CAL"),
        "status": colour_after(text, r"STATUS"),
    }

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
    methods = [name for name, pattern in method_patterns.items() if re.search(pattern, text, re.I)]

    record = {
        **base,
        "market": "1X2",
        "pick": "1",
        "associated_method": " | ".join(methods),
        "prob_1": prob_1,
        "prob_x": prob_x,
        "prob_2": prob_2,
        "fair_odds": fair_1,
        "opening_odds": opening_1,
        "current_odds": current_1 or base["list_odds"],
        "allibramento_color": allib_colour,
        "allibramento_value": allib_value,
        "allibramento_avg": allib_avg,
        "stake": 20.0,
        "played_odds": current_1 or base["list_odds"],
        "c_aff_count": c_aff_count,
        **indicators,
    }

    for label, column in METHOD_COLUMNS.items():
        record[column] = 1 if label in methods else 0

    return record


def next_internal_id(client):
    response = client.table("matches").select("id").execute()
    numbers = []
    for row in response.data or []:
        try:
            numbers.append(int(str(row.get("id", ""))))
        except ValueError:
            pass
    return f"{(max(numbers) if numbers else 0) + 1:04d}"


def save_to_supabase(records):
    client = create_client(SUPABASE_URL, SUPABASE_KEY)
    inserted = 0
    updated = 0

    for record in records:
        source_id = record["source_match_id"]
        existing = (
            client.table("matches")
            .select("id")
            .eq("source_match_id", source_id)
            .limit(1)
            .execute()
            .data
        )

        payload = dict(record)
        payload.pop("home_team", None)
        payload.pop("away_team", None)
        payload.pop("list_odds", None)

        if existing:
            print(
                f'SALTATA: source_match_id={source_id} già presente '
                f'con id={existing[0]["id"]}'
            )
            updated += 1
            continue

        payload["id"] = next_internal_id(client)

        frozen_odds = float(payload.get("current_odds") or 0)
        payload["current_odds"] = frozen_odds
        payload["played_odds"] = frozen_odds
        payload["stake"] = 20.0

        client.table("matches").insert(payload).execute()
        inserted += 1

    return inserted, updated


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

        matches = parse_list(page.content())
        if not matches:
            raise RuntimeError("Nessuna Ottimo 1 trovata oggi.")

        records = []
        for index, match in enumerate(matches, 1):
            print(f'[{index}/{len(matches)}] {match["match_name"]}')
            page.goto(match["detail_url"], wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)
            records.append(parse_detail(page.content(), match))

        inserted, updated = save_to_supabase(records)
        print(f"Completato: {inserted} nuove, {updated} già presenti e non modificate.")
        browser.close()


if __name__ == "__main__":
    main()
