import os
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

LOGIN_URL = os.getenv("STATS4BETS_LOGIN_URL", "https://superfoglio.it/")
TARGET_URL = "https://superfoglio.it/stats_table.php?q=mindbet"
USERNAME = os.environ["STATS4BETS_USERNAME"]
PASSWORD = os.environ["STATS4BETS_PASSWORD"]

OUT = Path("collector_artifacts")
OUT.mkdir(exist_ok=True)

def first_visible(page, selectors):
    for selector in selectors:
        loc = page.locator(selector)
        try:
            if loc.count() and loc.first.is_visible():
                return loc.first
        except Exception:
            pass
    return None

def save(page, name):
    page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
    (OUT / f"{name}.html").write_text(page.content(), encoding="utf-8")
    (OUT / f"{name}_url.txt").write_text(page.url, encoding="utf-8")

def login(page):
    user = first_visible(page, [
        'input[type="email"]',
        'input[name*="email" i]',
        'input[name*="user" i]',
        'input[id*="email" i]',
        'input[id*="user" i]',
        'input[type="text"]',
    ])
    pwd = first_visible(page, [
        'input[type="password"]',
        'input[name*="pass" i]',
        'input[id*="pass" i]',
    ])
    if user is None or pwd is None:
        raise RuntimeError("Campi login non trovati. Controlla login_page.png.")
    user.fill(USERNAME)
    pwd.fill(PASSWORD)
    submit = first_visible(page, [
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("Accedi")',
        'button:has-text("Login")',
        'button:has-text("Entra")',
    ])
    if submit:
        submit.click()
    else:
        pwd.press("Enter")

def has_password(page):
    loc = page.locator('input[type="password"]')
    try:
        return loc.count() and loc.first.is_visible()
    except Exception:
        return False

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1200},
            locale="it-IT",
            timezone_id="Europe/Rome",
        )
        page = context.new_page()

        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
        save(page, "login_page")

        if has_password(page):
            login(page)
            try:
                page.wait_for_load_state("networkidle", timeout=30000)
            except PlaywrightTimeoutError:
                page.wait_for_timeout(5000)

        page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)
        save(page, "mindbet_page")

        if has_password(page):
            raise RuntimeError("Login non riuscito. Controlla gli artifact.")
        if "MINDBET" not in page.content().upper():
            raise RuntimeError("Pagina aperta, ma MINDBET non trovato.")

        context.storage_state(path=str(OUT / "storage_state.json"))
        print("SUCCESSO: pagina MindBet raggiunta.")
        print(page.url)
        browser.close()

if __name__ == "__main__":
    main()
