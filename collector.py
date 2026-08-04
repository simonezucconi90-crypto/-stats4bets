import os
from pathlib import Path

from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

LOGIN_URL = os.getenv("STATS4BETS_LOGIN_URL", "https://superfoglio.it/")
TARGET_URL = "https://superfoglio.it/stats_table.php?q=mindbet"

USERNAME = os.environ["STATS4BETS_USER"]
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


def save_debug(page, name):
    page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
    (OUT / f"{name}.html").write_text(page.content(), encoding="utf-8")
    (OUT / f"{name}_url.txt").write_text(page.url, encoding="utf-8")


def find_login_fields(page):
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
    return email, password


def accept_terms(page):
    # Prima prova: checkbox standard visibile.
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
            return
        except Exception:
            pass

    # Seconda prova: clic sull'etichetta che contiene il testo.
    labels = [
        'label:has-text("Dichiaro di aver letto")',
        'text=Dichiaro di aver letto e accettato',
        'text=termini e condizioni',
    ]

    for selector in labels:
        locator = page.locator(selector)
        try:
            if locator.count() > 0 and locator.first.is_visible():
                locator.first.click(force=True)
                return
        except Exception:
            continue

    raise RuntimeError(
        "Non sono riuscito a spuntare la casella dei termini. "
        "Controlla login_page.png negli artifact."
    )


def perform_login(page):
    email, password = find_login_fields(page)

    if email is None or password is None:
        raise RuntimeError(
            "Non trovo i campi Email e Password. "
            "Controlla login_page.png negli artifact."
        )

    email.fill(USERNAME)
    password.fill(PASSWORD)
    accept_terms(page)

    submit = first_visible(
        page,
        [
            'button[type="submit"]',
            'input[type="submit"]',
            'button:has-text("Login")',
            'button:has-text("Accedi")',
            'text=Login',
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


def login_form_visible(page):
    _, password = find_login_fields(page)
    return password is not None


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)

        context = browser.new_context(
            viewport={"width": 1440, "height": 1200},
            locale="it-IT",
            timezone_id="Europe/Rome",
        )

        page = context.new_page()

        print(f"Apro login: {LOGIN_URL}")
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)
        save_debug(page, "login_page")

        if login_form_visible(page):
            print("Compilo login e spunto i termini.")
            perform_login(page)
            save_debug(page, "after_login")
        else:
            print("La sessione sembra già autenticata.")

        print(f"Apro MindBet: {TARGET_URL}")
        page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)
        save_debug(page, "mindbet_page")

        if login_form_visible(page):
            raise RuntimeError(
                "Il login non è riuscito o la sessione non è stata mantenuta."
            )

        html_upper = page.content().upper()
        if "MINDBET" not in html_upper:
            raise RuntimeError(
                "La pagina è stata raggiunta, ma non trovo la parola MINDBET."
            )

        context.storage_state(path=str(OUT / "storage_state.json"))

        print("SUCCESSO: login effettuato e pagina MindBet raggiunta.")
        print(f"URL finale: {page.url}")

        browser.close()


if __name__ == "__main__":
    main()
