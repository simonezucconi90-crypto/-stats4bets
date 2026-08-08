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

HIGH_CONFIDENCE = 0.72
REVIEW_CONFIDENCE = 0.65

TEAM_ALIASES = {
    "wolves": "wolverhampton wanderers",
    "wolverhampton": "wolverhampton wanderers",
    "wolverhampton wanderers": "wolverhampton wanderers",
    "shenzhen xinpengcheng": "shenzhen peng city",
    "sichuan jiuniu": "shenzhen peng city",
    "shenzhen peng city": "shenzhen peng city",
    "olympiacos": "olympiakos piraeus",
    "olympiakos": "olympiakos piraeus",
    "olympiakos piraeus": "olympiakos piraeus",
    "psv": "psv eindhoven",
    "psg": "paris saint germain",
    "man utd": "manchester united",
    "man united": "manchester united",
    "u catolica": "universidad catolica",
    "universidad catolica": "universidad catolica",
    "universidad catolica chile": "universidad catolica",
}



def basic_normalize(value):
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.casefold()
    value = re.sub(
        r"\b(fc|cf|sc|ac|afc|fk|club|calcio|football|futbol|deportivo)\b",
        " ",
        value,
    )
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def normalize_team_name(value):
    normalized = basic_normalize(value)

    # Abbreviazioni comuni e conservative.
    # Esempio: "U. Catolica" -> "Universidad Catolica".
    if normalized.startswith("u "):
        expanded = "universidad " + normalized[2:].strip()
        if expanded in TEAM_ALIASES:
            normalized = expanded

    return TEAM_ALIASES.get(normalized, normalized)


def normalize_league_name(value):
    value = basic_normalize(value)
    value = re.sub(
        r"\b(league|liga|championship|cup|super|division|national|professional)\b",
        " ",
        value,
    )
    return re.sub(r"\s+", " ", value).strip()


def similarity(left, right, team=False):
    a = normalize_team_name(left) if team else basic_normalize(left)
    b = normalize_team_name(right) if team else basic_normalize(right)

    if not a or not b:
        return 0.0

    sequence = SequenceMatcher(None, a, b).ratio()

    containment = 0.0
    if a in b or b in a:
        containment = min(len(a), len(b)) / max(len(a), len(b))

    a_words = set(a.split())
    b_words = set(b.split())
    union = a_words | b_words
    token_score = len(a_words & b_words) / len(union) if union else 0.0

    return max(sequence, containment, token_score)


def league_similarity(left, right):
    a = normalize_league_name(left)
    b = normalize_league_name(right)
    if not a or not b:
        return 0.0
    return similarity(a, b)


def split_match_name(value):
    parts = re.split(r"\s+-\s+", value or "", maxsplit=1)
    return (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else ("", "")


def fetch_by_date(date_iso, api_key):
    response = requests.get(
        API_URL,
        headers={"x-apisports-key": api_key},
        params={"date": date_iso, "timezone": "Europe/Rome"},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("errors"):
        raise RuntimeError(f"API-Football: {payload['errors']}")
    return payload.get("response", [])


def candidate_score(db_match, fixture):
    db_home, db_away = split_match_name(db_match.get("match_name"))
    api_home = fixture.get("teams", {}).get("home", {}).get("name", "")
    api_away = fixture.get("teams", {}).get("away", {}).get("name", "")

    db_league = db_match.get("league") or ""
    api_league = fixture.get("league", {}).get("name", "")

    home_score = similarity(db_home, api_home, team=True)
    away_score = similarity(db_away, api_away, team=True)
    league_score = league_similarity(db_league, api_league)

    team_score = (home_score + away_score) / 2
    total = min(1.0, team_score + 0.04 * league_score)

    return total, {
        "db_home": db_home,
        "db_away": db_away,
        "api_home": api_home,
        "api_away": api_away,
        "db_league": db_league,
        "api_league": api_league,
        "home_score": home_score,
        "away_score": away_score,
        "league_score": league_score,
    }


def choose_fixture(db_match, fixtures):
    candidates = []
    for fixture in fixtures:
        total, details = candidate_score(db_match, fixture)
        candidates.append((total, details, fixture))

    candidates.sort(key=lambda item: item[0], reverse=True)
    if not candidates:
        return None, False, 0.0, "nessun candidato", {}

    total, details, fixture = candidates[0]
    second = candidates[1][0] if len(candidates) > 1 else 0.0
    margin = total - second

    home_score = details["home_score"]
    away_score = details["away_score"]
    league_score = details["league_score"]

    if (
        total >= HIGH_CONFIDENCE
        and home_score >= 0.68
        and away_score >= 0.58
        and margin >= 0.05
    ):
        return fixture, True, total, "alta confidenza", details

    alias_home = (
        normalize_team_name(details["db_home"])
        == normalize_team_name(details["api_home"])
    )
    alias_away = (
        normalize_team_name(details["db_away"])
        == normalize_team_name(details["api_away"])
    )

    extra_confirmation = league_score >= 0.45 or alias_home or alias_away

    if (
        REVIEW_CONFIDENCE <= total < HIGH_CONFIDENCE
        and home_score >= 0.62
        and away_score >= 0.55
        and margin >= 0.04
        and extra_confirmation
    ):
        reasons = []
        if alias_home:
            reasons.append("alias casa")
        if alias_away:
            reasons.append("alias trasferta")
        if league_score >= 0.45:
            reasons.append("campionato compatibile")

        return (
            fixture,
            True,
            total,
            "verifica rafforzata: " + ", ".join(reasons),
            details,
        )

    return fixture, False, total, "confidenza insufficiente", details


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
            "id,date,time,league,match_name,pick,"
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

        fixture, safe, confidence, reason, details = choose_fixture(
            match,
            fixture_cache.get(date_iso, []),
        )

        if not fixture:
            uncertain += 1
            print(f'â NESSUN MATCH: {match.get("match_name")}')
            continue

        if not safe:
            uncertain += 1
            print(f'â MATCH SCARTATO: {match.get("match_name")}')
            print(
                f'  API candidata: {details.get("api_home")} - '
                f'{details.get("api_away")}'
            )
            print(
                f'  Confidenza={confidence:.3f} | '
                f'Casa={details.get("home_score", 0):.3f} | '
                f'Trasferta={details.get("away_score", 0):.3f} | '
                f'Lega={details.get("league_score", 0):.3f}'
            )
            print(f'  Motivo: {reason}')
            continue

        print(f'â MATCH ACCETTATO: {match.get("match_name")}')
        print(
            f'  API: {details.get("api_home")} - '
            f'{details.get("api_away")}'
        )
        print(
            f'  Confidenza={confidence:.3f} | '
            f'Casa={details.get("home_score", 0):.3f} | '
            f'Trasferta={details.get("away_score", 0):.3f} | '
            f'Lega={details.get("league_score", 0):.3f}'
        )
        print(f'  Motivo: {reason}')

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
