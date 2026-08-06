import os
import re
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher

import requests
from supabase import create_client

API_URL = "https://v3.football.api-sports.io/fixtures"
FINISHED = {"FT", "AET", "PEN"}
STAKE = 20.0


def normalize_name(value):
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.casefold()
    value = value.replace("olympiacos", "olympiakos")
    value = re.sub(
        r"\b(fc|cf|sc|ac|afc|fk|club|calcio|football|futbol|deportivo)\b",
        " ",
        value,
    )
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def similarity(left, right):
    a = normalize_name(left)
    b = normalize_name(right)

    if not a or not b:
        return 0.0

    containment = (
        min(len(a), len(b)) / max(len(a), len(b))
        if a in b or b in a
        else 0.0
    )

    return max(
        SequenceMatcher(None, a, b).ratio(),
        containment,
    )


def split_match_name(value):
    parts = re.split(r"\s+-\s+", value or "", maxsplit=1)

    if len(parts) != 2:
        return "", ""

    return parts[0].strip(), parts[1].strip()


def fetch_by_date(date_iso, api_key):
    response = requests.get(
        API_URL,
        headers={"x-apisports-key": api_key},
        params={
            "date": date_iso,
            "timezone": "Europe/Rome",
        },
        timeout=30,
    )
    response.raise_for_status()

    payload = response.json()

    if payload.get("errors"):
        raise RuntimeError(f"API-Football: {payload['errors']}")

    return payload.get("response", [])


def candidate_score(db_match, fixture):
    db_home, db_away = split_match_name(db_match.get("match_name"))

    api_home = (
        fixture.get("teams", {})
        .get("home", {})
        .get("name", "")
    )
    api_away = (
        fixture.get("teams", {})
        .get("away", {})
        .get("name", "")
    )

    home_score = similarity(db_home, api_home)
    away_score = similarity(db_away, api_away)
    total = (home_score + away_score) / 2

    return total, home_score, away_score


def choose_fixture(db_match, fixtures):
    candidates = []

    for fixture in fixtures:
        total, home_score, away_score = candidate_score(
            db_match,
            fixture,
        )
        candidates.append(
            (total, home_score, away_score, fixture)
        )

    candidates.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    if not candidates:
        return None, False, 0.0

    total, home_score, away_score, fixture = candidates[0]
    second = candidates[1][0] if len(candidates) > 1 else 0.0

    safe = (
        total >= 0.72
        and home_score >= 0.68
        and away_score >= 0.58
        and (total - second) >= 0.05
    )

    return fixture, safe, total


def determine_outcome(pick, home_goals, away_goals):
    pick = str(pick or "1").strip().upper()

    if pick == "1":
        return "V" if home_goals > away_goals else "P"

    if pick == "X":
        return "V" if home_goals == away_goals else "P"

    if pick == "2":
        return "V" if away_goals > home_goals else "P"

    return ""


def calculate_money(outcome, current_odds):
    odds = float(current_odds or 0)

    if outcome == "V":
        gross_return = round(STAKE * odds, 2)
        profit = round(gross_return - STAKE, 2)
    else:
        gross_return = 0.0
        profit = -STAKE

    return gross_return, profit


def main():
    client = create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_KEY"],
    )

    api_key = os.environ["API_FOOTBALL_KEY"]

    open_matches = (
        client.table("matches")
        .select(
            "id,date,time,match_name,pick,"
            "current_odds,outcome"
        )
        .is_("outcome", "null")
        .order("date")
        .execute()
        .data
        or []
    )

    grouped = defaultdict(list)

    for match in open_matches:
        date_iso = str(match.get("date") or "")[:10]

        if date_iso:
            grouped[date_iso].append(match)

    fixture_cache = {
        date_iso: fetch_by_date(date_iso, api_key)
        for date_iso in grouped
    }

    updated = 0
    waiting = 0
    uncertain = 0

    for match in open_matches:
        date_iso = str(match.get("date") or "")[:10]

        fixture, safe, confidence = choose_fixture(
            match,
            fixture_cache.get(date_iso, []),
        )

        if not fixture or not safe:
            uncertain += 1
            print(
                f'NON SICURA: {match.get("match_name")} '
                f'(confidenza {confidence:.3f})'
            )
            continue

        status = (
            fixture.get("fixture", {})
            .get("status", {})
            .get("short", "")
        )

        home_goals = (
            fixture.get("goals", {})
            .get("home")
        )
        away_goals = (
            fixture.get("goals", {})
            .get("away")
        )

        if (
            status not in FINISHED
            or home_goals is None
            or away_goals is None
        ):
            waiting += 1
            continue

        outcome = determine_outcome(
            match.get("pick"),
            int(home_goals),
            int(away_goals),
        )

        if not outcome:
            uncertain += 1
            continue

        gross_return, profit = calculate_money(
            outcome,
            match.get("current_odds"),
        )

        # Il workflow risultati modifica soltanto questi quattro campi.
        values = {
            "final_score": f"{home_goals}-{away_goals}",
            "outcome": outcome,
            "gross_return": gross_return,
            "profit": profit,
        }

        (
            client.table("matches")
            .update(values)
            .eq("id", match["id"])
            .execute()
        )

        updated += 1

        print(
            f'AGGIORNATA: {match.get("match_name")} '
            f'{values["final_score"]} {outcome} '
            f'profitto {profit:+.2f} â¬'
        )

    print(
        f"Completato: {updated} aggiornate, "
        f"{waiting} non terminate, "
        f"{uncertain} non abbinate con sicurezza."
    )


if __name__ == "__main__":
    main()
