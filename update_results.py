import os
import re
import time
import unicodedata
from difflib import SequenceMatcher
from datetime import datetime, timedelta

import requests
from supabase import create_client

FOOTBALLDATA_BASE = "https://footballdata.io/api/v1"
THESPORTSDB_SEARCH = "https://www.thesportsdb.com/api/v1/json/123/searchevents.php"
THESPORTSDB_LOOKUP = "https://www.thesportsdb.com/api/v1/json/123/lookupevent.php"

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
    value = re.sub(r"\b(fc|cf|sc|ac|afc|fk|club|calcio|football|futbol|deportivo)\b", " ", value)
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
    value = re.sub(r"\b(league|liga|championship|cup|super|division|national|professional)\b", " ", value)
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
    aw, bw = set(a.split()), set(b.split())
    union = aw | bw
    token = len(aw & bw) / len(union) if union else 0.0
    return max(sequence, containment, token)

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

def build_fixture(source, event_id, home, away, league, home_goals, away_goals, status, date_iso):
    return {
        "source": source,
        "event_id": str(event_id or ""),
        "teams": {"home": {"name": home or ""}, "away": {"name": away or ""}},
        "league": {"name": league or ""},
        "goals": {"home": to_int(home_goals), "away": to_int(away_goals)},
        "status": str(status or ""),
        "date": str(date_iso or ""),
    }

def football_data_headers(api_key):
    return {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}

def extract_football_data_matches(payload):
    data = payload.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if isinstance(data.get("matches"), list):
            return data["matches"]
        if data.get("match_id") or data.get("home_team"):
            return [data]
    return []

def convert_football_data_match(item):
    home = item.get("home_team") or {}
    away = item.get("away_team") or {}
    league = item.get("league") or {}
    score = item.get("score") or {}
    league_name = league.get("competition_name") or league.get("name") or ""
    return build_fixture(
        "Footballdata.io",
        item.get("match_id"),
        home.get("team_name"),
        away.get("team_name"),
        league_name,
        score.get("home"),
        score.get("away"),
        item.get("status"),
        item.get("match_date"),
    )

def fetch_football_data_by_date(date_iso, api_key):
    response = requests.get(
        f"{FOOTBALLDATA_BASE}/matches/date/{date_iso}",
        headers=football_data_headers(api_key),
        params={"limit": 100, "sort": "asc"},
        timeout=30,
    )
    if response.status_code in {401, 403, 429}:
        print(f"FOOTBALLDATA.IO non disponibile ({response.status_code}) su {date_iso}")
        return []
    response.raise_for_status()
    payload = response.json()
    if payload.get("success") is False:
        print(f"FOOTBALLDATA.IO risposta non riuscita su {date_iso}: {payload}")
        return []
    return [convert_football_data_match(x) for x in extract_football_data_matches(payload)]

def lookup_football_data_match(match_id, api_key):
    if not match_id:
        return None
    response = requests.get(
        f"{FOOTBALLDATA_BASE}/matches/{match_id}",
        headers=football_data_headers(api_key),
        timeout=30,
    )
    if response.status_code != 200:
        return None
    payload = response.json()
    if payload.get("success") is False:
        return None
    items = extract_football_data_matches(payload)
    return convert_football_data_match(items[0]) if items else None

def convert_thesportsdb_event(event):
    return build_fixture(
        "TheSportsDB",
        event.get("idEvent"),
        event.get("strHomeTeam"),
        event.get("strAwayTeam"),
        event.get("strLeague"),
        event.get("intHomeScore"),
        event.get("intAwayScore"),
        event.get("strStatus"),
        event.get("dateEvent"),
    )

def fetch_thesportsdb_match(match):
    home, away = split_match_name(match.get("match_name"))
    date_iso = str(match.get("date") or "")[:10]
    if not home or not away:
        return []
    queries = [(home, away)]
    nh, na = normalize_team_name(home), normalize_team_name(away)
    if nh and na and (nh.casefold(), na.casefold()) != (home.casefold(), away.casefold()):
        queries.append((nh, na))
    for q_home, q_away in queries:
        response = requests.get(
            THESPORTSDB_SEARCH,
            params={"e": f"{q_home}_vs_{q_away}", "d": date_iso},
            timeout=30,
        )
        response.raise_for_status()
        events = response.json().get("event") or []
        soccer = [
            convert_thesportsdb_event(e)
            for e in events
            if str(e.get("strSport") or "").casefold() == "soccer"
        ]
        if soccer:
            return soccer
        time.sleep(REQUEST_DELAY)
    return []

def lookup_thesportsdb_event(event_id):
    if not event_id:
        return None
    time.sleep(REQUEST_DELAY)
    response = requests.get(THESPORTSDB_LOOKUP, params={"id": event_id}, timeout=30)
    response.raise_for_status()
    events = response.json().get("events") or []
    if not events:
        return None
    event = events[0]
    if str(event.get("strSport") or "").casefold() != "soccer":
        return None
    return convert_thesportsdb_event(event)

def candidate_score(db_match, fixture):
    db_home, db_away = split_match_name(db_match.get("match_name"))
    api_home = fixture.get("teams", {}).get("home", {}).get("name", "")
    api_away = fixture.get("teams", {}).get("away", {}).get("name", "")
    db_league = db_match.get("league") or ""
    api_league = fixture.get("league", {}).get("name", "")
    home_score = similarity(db_home, api_home, team=True)
    away_score = similarity(db_away, api_away, team=True)
    league_score = league_similarity(db_league, api_league)
    total = min(1.0, (home_score + away_score) / 2 + 0.04 * league_score)
    return total, {
        "db_home": db_home, "db_away": db_away,
        "api_home": api_home, "api_away": api_away,
        "home_score": home_score, "away_score": away_score,
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
    hs, aas, ls = details["home_score"], details["away_score"], details["league_score"]
    margin_ok = margin >= 0.05 or len(candidates) == 1
    if total >= HIGH_CONFIDENCE and hs >= 0.68 and aas >= 0.58 and margin_ok:
        return fixture, True, total, "alta confidenza", details
    alias_home = normalize_team_name(details["db_home"]) == normalize_team_name(details["api_home"])
    alias_away = normalize_team_name(details["db_away"]) == normalize_team_name(details["api_away"])
    if (
        REVIEW_CONFIDENCE <= total < HIGH_CONFIDENCE
        and hs >= 0.62 and aas >= 0.55 and margin_ok
        and (ls >= 0.45 or alias_home or alias_away)
    ):
        return fixture, True, total, "verifica rafforzata", details
    return fixture, False, total, "confidenza insufficiente", details

def is_finished(date_iso, fixture):
    hg = fixture.get("goals", {}).get("home")
    ag = fixture.get("goals", {}).get("away")
    if hg is None or ag is None:
        return False
    status = str(fixture.get("status") or "").strip().casefold()
    finished_statuses = {
        "complete", "completed", "finished", "match finished",
        "ft", "full time", "aet", "pen",
        "after extra time", "after penalties",
    }
    if status in finished_statuses:
        return True
    try:
        event_date = datetime.strptime(date_iso, "%Y-%m-%d").date()
    except Exception:
        return False
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

def verify_fixture(db_match, fixture, football_data_key):
    try:
        if fixture.get("source") == "Footballdata.io":
            verified = lookup_football_data_match(fixture.get("event_id"), football_data_key)
        else:
            verified = lookup_thesportsdb_event(fixture.get("event_id"))
    except Exception as exc:
        print(f"  ⏳ verifica finale fallita: {exc}")
        return None
    if not verified:
        return None
    total, details = candidate_score(db_match, verified)
    if total < HIGH_CONFIDENCE or details["home_score"] < 0.68 or details["away_score"] < 0.58:
        print(
            f'  ✗ VERIFICA FINALE SCARTATA: confidenza={total:.3f} | '
            f'Casa={details["home_score"]:.3f} | Trasferta={details["away_score"]:.3f}'
        )
        return None
    return verified

def main():
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

    football_data_key = os.getenv("FOOTBALLDATA_IO_KEY", "").strip()
    if not football_data_key:
        raise RuntimeError("FOOTBALLDATA_IO_KEY non disponibile nel workflow GitHub.")

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
            print(f'SALTO FUORI FINESTRA: {match.get("match_name")} ({date_iso})')

    print(f"Partite aperte: {len(open_matches)} | Da cercare: {len(matches)}")

    football_data_cache = {}
    dates = sorted({str(m.get("date") or "")[:10] for m in matches})

    for date_iso in dates:
        try:
            fixtures = fetch_football_data_by_date(date_iso, football_data_key)
            football_data_cache[date_iso] = fixtures
            print(f"FOOTBALLDATA.IO {date_iso}: {len(fixtures)} partite disponibili.")
        except Exception as exc:
            football_data_cache[date_iso] = []
            print(f"ERRORE FOOTBALLDATA.IO su {date_iso}: {exc}")

    updated = waiting = uncertain = 0
    used_fd = used_tsdb = 0

    for i, match in enumerate(matches, 1):
        date_iso = str(match.get("date") or "")[:10]
        print(f'[{i}/{len(matches)}] {match.get("match_name")} ({date_iso})')

        fixture, safe, confidence, reason, details = choose_fixture(
            match, football_data_cache.get(date_iso, [])
        )

        if fixture and safe:
            fixture["source"] = "Footballdata.io"
            print(
                f'  FOOTBALLDATA candidata: {details.get("api_home")} - '
                f'{details.get("api_away")} | confidenza={confidence:.3f}'
            )
        else:
            fixture = None

        if fixture is None:
            print("  ↪ Footballdata.io non basta. Provo TheSportsDB.")
            try:
                fallback = fetch_thesportsdb_match(match)
            except Exception as exc:
                fallback = []
                print(f"  ERRORE TheSportsDB: {exc}")

            fixture, safe, confidence, reason, details = choose_fixture(match, fallback)

            if fixture and safe:
                fixture["source"] = "TheSportsDB"
                print(
                    f'  THESPORTSDB candidata: {details.get("api_home")} - '
                    f'{details.get("api_away")} | confidenza={confidence:.3f}'
                )
            else:
                fixture = None

        if fixture is None:
            uncertain += 1
            print(f'  ✗ NESSUN MATCH SICURO: {match.get("match_name")}')
            continue

        verified = verify_fixture(match, fixture, football_data_key)
        if not verified:
            waiting += 1
            print("  ⏳ evento trovato ma verifica finale non disponibile/sicura.")
            continue

        source = verified.get("source")

        if not is_finished(date_iso, verified):
            waiting += 1
            print(f"  ⏳ {source}: partita non conclusa o risultato definitivo non disponibile.")
            continue

        hg = verified.get("goals", {}).get("home")
        ag = verified.get("goals", {}).get("away")

        print(
            f'  ✓ VERIFICA FINALE {source} id={verified.get("event_id")}: '
            f'{verified["teams"]["home"]["name"]} - {verified["teams"]["away"]["name"]} = {hg}-{ag}'
        )

        outcome = determine_outcome(match.get("pick"), int(hg), int(ag))
        if not outcome:
            uncertain += 1
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
        if source == "Footballdata.io":
            used_fd += 1
        else:
            used_tsdb += 1

        print(
            f'  ✓ AGGIORNATA: {values["final_score"]} {outcome} '
            f'profitto {profit:+.2f} € [{source}]'
        )

    print(
        f"Completato: {updated} aggiornate, {waiting} non terminate/non verificate, "
        f"{uncertain} non trovate/abbinate con sicurezza."
    )
    print(
        f"Fonti usate: Footballdata.io={used_fd} | TheSportsDB={used_tsdb}"
    )

if __name__ == "__main__":
    main()
