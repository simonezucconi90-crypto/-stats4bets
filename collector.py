import csv, json, os, re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

LOGIN_URL = "https://superfoglio.it/"
TARGET_URL = "https://superfoglio.it/stats_table.php?q=mindbet"
USER = os.environ["STATS4BETS_USER"]
PASSWORD = os.environ["STATS4BETS_PASSWORD"]
OUT = Path("collector_artifacts")
OUT.mkdir(exist_ok=True)

def first_visible(page, selectors):
    for s in selectors:
        loc = page.locator(s)
        try:
            if loc.count() and loc.first.is_visible():
                return loc.first
        except Exception:
            pass
    return None

def login(page):
    email = first_visible(page, ['input[type="email"]','input[name*="email" i]','input[type="text"]'])
    password = first_visible(page, ['input[type="password"]'])
    if email is None or password is None:
        raise RuntimeError("Campi login non trovati")
    email.fill(USER)
    password.fill(PASSWORD)

    checkbox = first_visible(page, ['input[type="checkbox"]'])
    if checkbox is not None:
        try:
            if not checkbox.is_checked():
                checkbox.check(force=True)
        except Exception:
            pass

    submit = first_visible(page, ['button[type="submit"]','input[type="submit"]','button:has-text("Login")'])
    if submit is None:
        password.press("Enter")
    else:
        submit.click()
    try:
        page.wait_for_load_state("networkidle", timeout=30000)
    except PlaywrightTimeoutError:
        page.wait_for_timeout(5000)

def save_debug(page):
    page.screenshot(path=str(OUT/"mindbet_page.png"), full_page=True)
    (OUT/"mindbet_page.html").write_text(page.content(), encoding="utf-8")

def card_text(link):
    loc = link
    for _ in range(8):
        try:
            text = re.sub(r"\s+", " ", loc.inner_text(timeout=2000)).strip()
        except Exception:
            text = ""
        if "OTTIMO 1" in text.upper() and re.search(r"\d{2}-\d{2}-\d{2}", text):
            return text
        loc = loc.locator("..")
    return ""

def parse_date(text):
    m = re.search(r"(\d{2})-(\d{2})-(\d{2})", text)
    return f"20{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else ""

def parse_time(text):
    m = re.search(r"\b([01]\d|2[0-3]):[0-5]\d\b", text)
    return m.group(0) if m else ""

def parse_odds(text):
    vals = re.findall(r"(?<![\d-])(\d{1,2}[.,]\d{2})(?!\d)", text)
    return vals[-1].replace(",", ".") if vals else ""

def collect(page):
    today = datetime.now().strftime("%Y-%m-%d")
    anchors = page.locator('a[href*="statistiche.php?id="]')
    seen, rows = set(), []
    for i in range(anchors.count()):
        a = anchors.nth(i)
        href = a.get_attribute("href")
        if not href:
            continue
        url = urljoin(page.url, href)
        if url in seen:
            continue
        text = card_text(a)
        if "OTTIMO 1" not in text.upper():
            continue
        if parse_date(text) != today:
            continue
        seen.add(url)
        rows.append({
            "date": parse_date(text),
            "time": parse_time(text),
            "selection": "Ottimo 1",
            "odds": parse_odds(text),
            "detail_url": url,
            "raw_card_text": text,
        })
    return rows

def save_rows(rows):
    (OUT/"ottimo1_today.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    with (OUT/"ottimo1_today.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["date","time","selection","odds","detail_url","raw_card_text"])
        writer.writeheader()
        writer.writerows(rows)

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale="it-IT", timezone_id="Europe/Rome", viewport={"width":1440,"height":1200})
        page = context.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
        if page.locator('input[type="password"]').count():
            login(page)
        page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)
        save_debug(page)
        rows = collect(page)
        save_rows(rows)
        print(f"Ottimo 1 trovate oggi: {len(rows)}")
        for r in rows:
            print(r)
        if not rows:
            raise RuntimeError("Nessuna Ottimo 1 trovata oggi: controllare gli artifact")
        browser.close()

if __name__ == "__main__":
    main()
