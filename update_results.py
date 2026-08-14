import os
import re
import time
import unicodedata
from difflib import SequenceMatcher
from datetime import datetime, timedelta

import requests
from supabase import create_client

SEARCH_API_URL = "https://www.thesportsdb.com/api/v1/json/123/searchevents.php"
LOOKUP_API_URL = "https://www.thesportsdb.com/api/v1/json/123/lookupevent.php"
STAKE = 20.0
LOOKBACK_DAYS = 14
REQUEST_DELAY = 2.1
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
        " ", value
    )
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def normalize_team_name(value):
    value = basic_normalize(value)
    if value.startswith("u "):
        expanded = "universidad " + value[2:].strip()
        if expanded in TEAM_ALIASES:
            value = expanded
    return TEAM_ALIASES.get(value, value)


def normalize_league_name(value):
    value = basic_normalize(value)
    value = re.sub(
        r"\b(league|liga|championship|cup|super|division|national|professional)\b",
        " ", value
    )
    return re.sub(r"\s+", " ", value).strip()


def similarity(left, right, team=False):
    a = normalize_team_name(left) if team else basic_normalize(left)
    b = normalize_team_name(right) if team else basic_normalize(right)
    if not a or not b:
        return 0.0

    seq = SequenceMatcher(None, a, b).ratio()
    containment = 0.0
    if a in b or b in a:
        containment = min(len(a), len(b)) / max(len(a), len(b))

    aw, bw = set(a.split()), set(b.split())
    union = aw | bw
    token = len(aw & bw) / len(union) if union else 0.0
    return max(seq, containment, token)


def league_similarity(left, right):
    a, b = normalize_league_name(left), normalize_league_name(right)
    return similarity(a, b) if a and b else 0.0


def split_match_name(value):
    parts = re.split(r"\s+-\s+", value or "", maxsplit=1)
    return (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else ("", "")


def useful_date(date_iso):
    try:
        d = datetime.strptime(date_iso, "%Y-%m-%d").date()
    except Exception:
        return False
    today = datetime.now().date()
    return today - timedelta(days=LOOKBACK_DAYS) <= d <= today


def to_int(value):
    try:
        return int(value) if value not in (None, "") else None
    except Exception:
        return None


def convert_event(event):
    return {
        "event_id": str(event.get("idEvent") or ""),
        "teams": {
            "home": {"name": event.get("strHomeTeam") or ""},
            "away": {"name": event.get("strAwayTeam") or ""},
        },
        "league": {"name": event.get("strLeague") or ""},
        "goals": {
            "home": to_int(event.get("intHomeScore")),
            "away": to_int(event.get("intAwayScore")),
        },
        "status": event.get("strStatus") or "",
        "date": event.get("dateEvent") or "",
    }


def fetch_match(match):
    home, away = split_match_name(match.get("match_name"))
    date_iso = str(match.get("date") or "")[:10]
    if not home or not away:
        return []

    queries = [(home, away)]
    nh, na = normalize_team_name(home), normalize_team_name(away)
    if nh and na and (nh.casefold(), na.casefold()) != (
        home.casefold(), away.casefold()
    ):
        queries.append((nh, na))

    for q_home, q_away in queries:
        response = requests.get(
            SEARCH_API_URL,
            params={"e": f"{q_home}_vs_{q_away}", "d": date_iso},
            timeout=30,
        )
        response.raise_for_status()
        events = response.json().get("event") or []

        soccer = [
            convert_event(e)
            for e in events
            if str(e.get("strSport") or "").casefold() == "soccer"
        ]
        if soccer:
            return soccer

        time.sleep(REQUEST_DELAY)

    return []


def lookup_event(event_id):
    if not event_id:
        return None

    time.sleep(REQUEST_DELAY)
    response = requests.get(
        LOOKUP_API_URL,
        params={"id": event_id},
        timeout=30,
    )
    response.raise_for_status()

    events = response.json().get("events") or []
    if not events:
        return None

    event = events[0]
    if str(event.get("strSport") or "").casefold() != "soccer":
        return None

    return convert_event(event)


def candidate_score(db_match, fixture):
    db_home, db_away = split_match_name(db_match.get("match_name"))
    api_home = fixture["teams"]["home"]["name"]
    api_away = fixture["teams"]["away"]["name"]

    home_score = similarity(db_home, api_home, team=True)
    away_score = similarity(db_away, api_away, team=True)
    league_score = league_similarity(
        db_match.get("league") or "",
        fixture["league"]["name"],
    )
    total = min(1.0, (home_score + away_score) / 2 + 0.04 * league_score)

    return total, {
        "db_home": db_home,
        "db_away": db_away,
        "api_home": api_home,
        "api_away": api_away,
        "home_score": home_score,
        "away_score": away_score,
        "league_score": league_score,
    }


def choose_fixture(db_match, fixtures):
    candidates = []
    for fixture in fixtures:
        total, details = candidate_score(db_match, fixture)
        candidates.append((total, details, fixture))

    candidates.sort(key=lambda x: x[0], reverse=True)
    if not candidates:
        return None, False, 0.0, "nessun candidato", {}

    total, details, fixture = candidates[0]
    second = candidates[1][0] if len(candidates) > 1 else 0.0
    margin = total - second
    hs, aas = details["home_score"], details["away_score"]
    ls = details["league_score"]

    # Quando la ricerca restituisce un solo evento, il margine non serve:
    # la conferma viene dai nomi delle due squadre.
    margin_ok = margin >= 0.05 or len(candidates) == 1

    if total >= HIGH_CONFIDENCE and hs >= 0.68 and aas >= 0.58 and margin_ok:
        return fixture, True, total, "alta confidenza", details

    alias_home = normalize_team_name(details["db_home"]) == normalize_team_name(
        details["api_home"]
    )
    alias_away = normalize_team_name(details["db_away"]) == normalize_team_name(
        details["api_away"]
    )

    if (
        REVIEW_CONFIDENCE <= total < HIGH_CONFIDENCE
        and hs >= 0.62
        and aas >= 0.55
        and margin_ok
        and (ls >= 0.45 or alias_home or alias_away)
    ):
        return fixture, True, total, "verifica rafforzata", details

    return fixture, False, total, "confidenza insufficiente", details


def finished(date_iso, fixture):
    hg = fixture["goals"]["home"]
    ag = fixture["goals"]["away"]
    if hg is None or ag is None:
        return False

    status = str(fixture.get("status") or "").casefold()
    if status in {"ft", "finished", "match finished", "aet", "pen"}:
        return True

    try:
        event_date = datetime.strptime(date_iso, "%Y-%m-%d").date()
    except Exception:
        return False

    # Per evitare di chiudere una partita live, se lo status non è chiaro
    # accettiamo il punteggio solo dal giorno successivo.
    return event_date < datetime.now().date()


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
        gross = round(STAKE * odds, 2)
        return gross, round(gross - STAKE, 2)
    return 0.0, -STAKE


def main():
    client = create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_KEY"],
    )

    open_matches = (
        client.table("matches")
        .select("id,date,time,league,match_name,pick,current_odds,outcome")
        .is_("outcome", "null")
        .order("date")
        .execute()
        .data
        or []
    )

    matches = []
    for match in open_matches:
        date_iso = str(match.get("date") or "")[:10]
        if useful_date(date_iso):
            matches.append(match)
        else:
            print(
                f'SALTO FUORI FINESTRA: {match.get("match_name")} ({date_iso})'
            )

    print(
        f"Partite aperte: {len(open_matches)} | "
        f"Da cercare su TheSportsDB: {len(matches)}"
    )

    updated = waiting = uncertain = 0

    for i, match in enumerate(matches, 1):
        date_iso = str(match.get("date") or "")[:10]
        print(f'[{i}/{len(matches)}] {match.get("match_name")} ({date_iso})')

        try:
            fixtures = fetch_match(match)
        except Exception as exc:
            uncertain += 1
            print(f"ERRORE TheSportsDB: {exc}")
            time.sleep(REQUEST_DELAY)
            continue

        fixture, safe, confidence, reason, details = choose_fixture(
            match, fixtures
        )

        if not fixture:
            uncertain += 1
            print(f'✗ NESSUN MATCH: {match.get("match_name")}')
            time.sleep(REQUEST_DELAY)
            continue

        print(
            f'  Candidata: {details.get("api_home")} - '
            f'{details.get("api_away")} | confidenza={confidence:.3f}'
        )

        if not safe:
            uncertain += 1
            print(f"  ✗ SCARTATA: {reason}")
            time.sleep(REQUEST_DELAY)
            continue

        event_id = fixture.get("event_id") or ""
        if not event_id:
            waiting += 1
            print("  ⏳ idEvent mancante: non salvo il risultato.")
            time.sleep(REQUEST_DELAY)
            continue

        try:
            verified_fixture = lookup_event(event_id)
        except Exception as exc:
            waiting += 1
            print(f"  ⏳ verifica finale fallita: {exc}")
            time.sleep(REQUEST_DELAY)
            continue

        if not verified_fixture:
            waiting += 1
            print("  ⏳ evento non disponibile nella verifica finale.")
            time.sleep(REQUEST_DELAY)
            continue

        verified_total, verified_details = candidate_score(match, verified_fixture)
        if (
            verified_total < HIGH_CONFIDENCE
            or verified_details["home_score"] < 0.68
            or verified_details["away_score"] < 0.58
        ):
            uncertain += 1
            print(
                "  ✗ VERIFICA FINALE SCARTATA: "
                f'confidenza={verified_total:.3f} | '
                f'Casa={verified_details["home_score"]:.3f} | '
                f'Trasferta={verified_details["away_score"]:.3f}'
            )
            time.sleep(REQUEST_DELAY)
            continue

        if not finished(date_iso, verified_fixture):
            waiting += 1
            print(
                "  ⏳ verifica finale: partita non conclusa "
                "o punteggio definitivo non disponibile."
            )
            time.sleep(REQUEST_DELAY)
            continue

        hg = verified_fixture["goals"]["home"]
        ag = verified_fixture["goals"]["away"]

        print(
            f'  ✓ VERIFICA FINALE idEvent={event_id}: '
            f'{verified_details.get("api_home")} - '
            f'{verified_details.get("api_away")} = {hg}-{ag}'
        )

        outcome = determine_outcome(match.get("pick"), hg, ag)
        if not outcome:
            uncertain += 1
            time.sleep(REQUEST_DELAY)
            continue

        gross, profit = calculate_money(outcome, match.get("current_odds"))
        values = {
            "final_score": f"{hg}-{ag}",
            "outcome": outcome,
            "gross_return": gross,
            "profit": profit,
        }

        client.table("matches").update(values).eq("id", match["id"]).execute()
        updated += 1

        print(
            f'  ✓ AGGIORNATA: {values["final_score"]} {outcome} '
            f'profitto {profit:+.2f} €'
        )
        time.sleep(REQUEST_DELAY)

    print(
        f"Completato: {updated} aggiornate, "
        f"{waiting} non terminate/non verificate, "
        f"{uncertain} non trovate/abbinate con sicurezza."
    )


if __name__ == "__main__":
    main()
