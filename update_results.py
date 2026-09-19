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
SPORTAPI_BASE = "https://sportapi7.p.rapidapi.com/api/v1"
SPORTAPI_HOST = "sportapi7.p.rapidapi.com"

STAKE = 20.0
LOOKBACK_DAYS = 14
REQUEST_DELAY = 4.5
THESPORTSDB_RETRIES = 4
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
    "hanacka": "hanacka slavia kromeriz",
    "hanacka slavia": "hanacka slavia kromeriz",
    "hanacka slavia kromeriz": "hanacka slavia kromeriz",
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

def sportapi_headers(api_key):
    return {
        "X-RapidAPI-Key": api_key,
        "X-RapidAPI-Host": SPORTAPI_HOST,
        "Accept": "application/json",
    }


def score_value(score):
    if isinstance(score, dict):
        for key in ("current", "display", "normaltime"):
            value = to_int(score.get(key))
            if value is not None:
                return value
    return to_int(score)


def sportapi_league_name(event):
    tournament = event.get("tournament") or {}
    unique = tournament.get("uniqueTournament") or {}
    category = tournament.get("category") or {}
    return (
        unique.get("name")
        or tournament.get("name")
        or category.get("name")
        or ""
    )


def convert_sportapi_event(event, fallback_date=""):
    home = event.get("homeTeam") or {}
    away = event.get("awayTeam") or {}
    status = event.get("status") or {}
    timestamp = event.get("startTimestamp")
    date_iso = fallback_date
    if timestamp:
        try:
            date_iso = datetime.fromtimestamp(int(timestamp)).strftime("%Y-%m-%d")
        except Exception:
            pass
    return build_fixture(
        "SportAPI",
        event.get("id"),
        home.get("name") or home.get("shortName"),
        away.get("name") or away.get("shortName"),
        sportapi_league_name(event),
        score_value(event.get("homeScore")),
        score_value(event.get("awayScore")),
        status.get("type") or status.get("description") or status.get("code"),
        date_iso,
    )


SPORTAPI_COUNTRY_ALIASES = {
    "italy": {"italy", "italia"}, "england": {"england", "efl cup", "championship", "league one", "league two"},
    "scotland": {"scotland", "scozia"}, "wales": {"wales", "galles"}, "ireland": {"ireland", "irlanda"},
    "northern ireland": {"northern ireland", "irlanda del nord"}, "austria": {"austria"},
    "switzerland": {"switzerland", "svizzera"}, "germany": {"germany", "germania"},
    "france": {"france", "francia"}, "spain": {"spain", "spagna", "la liga"},
    "portugal": {"portugal", "portogallo"}, "netherlands": {"netherlands", "olanda"},
    "belgium": {"belgium", "belgio"}, "denmark": {"denmark", "danimarca"},
    "sweden": {"sweden", "svezia"}, "norway": {"norway", "norvegia"},
    "finland": {"finland", "finlandia"}, "iceland": {"iceland", "islanda"},
    "poland": {"poland", "polonia"}, "czech republic": {"czech republic", "repubblica ceca", "czechia", "ceca"},
    "croatia": {"croatia", "croazia"}, "serbia": {"serbia"}, "bulgaria": {"bulgaria"},
    "romania": {"romania"}, "ukraine": {"ukraine", "ucraina"}, "estonia": {"estonia"},
    "georgia": {"georgia"}, "turkey": {"turkey", "turchia"}, "saudi arabia": {"saudi arabia", "arabia saudita"},
    "south africa": {"south africa", "sudafrica"}, "japan": {"japan", "giappone"},
    "canada": {"canada"}, "usa": {"usa", "united states", "america"}, "mexico": {"mexico", "messico"},
    "brazil": {"brazil", "brasile"}, "argentina": {"argentina"}, "peru": {"peru"},
    "bolivia": {"bolivia"}, "chile": {"chile"}, "colombia": {"colombia"},
    "ecuador": {"ecuador"}, "paraguay": {"paraguay"}, "venezuela": {"venezuela"},
}

def sportapi_category_info(item):
    category = item.get("category") if isinstance(item, dict) else {}
    category = category if isinstance(category, dict) else {}
    if not category and isinstance(item, dict):
        category = item
    return str(category.get("id") or ""), str(category.get("name") or "")

def sportapi_category_match(league, category_name):
    league_norm = normalize_league_name(league)
    category_norm = normalize_league_name(category_name)
    if not category_norm:
        return False
    if category_norm == league_norm or category_norm in league_norm or league_norm in category_norm:
        return True
    return any(
        any(alias in league_norm for alias in aliases) and any(alias in category_norm for alias in aliases)
        for aliases in SPORTAPI_COUNTRY_ALIASES.values()
    )

def fetch_sportapi_categories_by_date(date_iso, api_key):
    response = requests.get(
        f"{SPORTAPI_BASE}/sport/football/{date_iso}/0/categories",
        headers=sportapi_headers(api_key),
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    categories = payload.get("categories") or payload.get("data") or []
    return [sportapi_category_info(item) for item in categories if sportapi_category_info(item)[0]]

def fetch_sportapi_category_events(date_iso, category_id, api_key):
    response = requests.get(
        f"{SPORTAPI_BASE}/category/{category_id}/scheduled-events/{date_iso}",
        headers=sportapi_headers(api_key),
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    events = payload.get("events") or payload.get("data") or []
    if isinstance(events, dict):
        events = events.get("events") or []
    return [convert_sportapi_event(event, date_iso) for event in events] if isinstance(events, list) else []

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

def thesportsdb_get_json(url, params):
    """Chiama TheSportsDB rispettando limiti e interruzioni temporanee."""
    last_error = None
    response = None
    for attempt in range(THESPORTSDB_RETRIES):
        try:
            response = requests.get(url, params=params, timeout=30)
        except requests.RequestException as exc:
            last_error = exc
            if attempt == THESPORTSDB_RETRIES - 1:
                raise
            wait_seconds = min(30.0, 5.0 * (2 ** attempt))
            print(
                f"  ⏳ Connessione TheSportsDB interrotta: "
                f"attendo {wait_seconds:.0f}s e riprovo "
                f"({attempt + 1}/{THESPORTSDB_RETRIES})."
            )
            time.sleep(wait_seconds)
            continue

        if response.status_code != 429:
            response.raise_for_status()
            payload = response.json()
            time.sleep(REQUEST_DELAY)
            return payload

        retry_after = str(response.headers.get("Retry-After") or "").strip()
        try:
            server_wait = float(retry_after)
        except Exception:
            server_wait = 0.0
        wait_seconds = max(server_wait, min(45.0, 12.0 * (2 ** attempt)))
        print(
            f"  ⏳ TheSportsDB limita le richieste (429): "
            f"attendo {wait_seconds:.0f}s e riprovo "
            f"({attempt + 1}/{THESPORTSDB_RETRIES})."
        )
        time.sleep(wait_seconds)

    if response is not None:
        response.raise_for_status()
    if last_error:
        raise last_error
    raise RuntimeError("TheSportsDB non ha restituito una risposta.")


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
        payload = thesportsdb_get_json(
            THESPORTSDB_SEARCH,
            {"e": f"{q_home}_vs_{q_away}", "d": date_iso},
        )
        events = payload.get("event") or []
        soccer = [
            convert_thesportsdb_event(e)
            for e in events
            if str(e.get("strSport") or "").casefold() == "soccer"
        ]
        if soccer:
            return soccer
    return []

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
    if fixture.get("source") == "SportAPI":
        return False
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
        if fixture.get("source") == "SportAPI":
            verified = fixture
        elif fixture.get("source") == "Footballdata.io":
            verified = lookup_football_data_match(fixture.get("event_id"), football_data_key)
        else:
            # La ricerca TheSportsDB restituisce già squadre, stato e risultato.
            # Riutilizzarla evita una seconda chiamata per ogni partita e dimezza
            # il rischio di blocco 429.
            verified = fixture
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

    rapidapi_key = os.getenv("RAPIDAPI_KEY", "").strip()
    if not rapidapi_key:
        raise RuntimeError("RAPIDAPI_KEY non disponibile nel workflow GitHub.")

    # Le vecchie fonti restano soltanto come fallback opzionale.
    football_data_key = os.getenv("FOOTBALLDATA_IO_KEY", "").strip()

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

    dates = sorted({str(m.get("date") or "")[:10] for m in matches})
    sportapi_cache = {}
    football_data_cache = {}

    for date_iso in dates:
        try:
            categories = fetch_sportapi_categories_by_date(date_iso, rapidapi_key)
            needed = [m for m in matches if str(m.get("date") or "")[:10] == date_iso]
            selected = [cid for cid, name in categories if any(sportapi_category_match(m.get("league") or "", name) for m in needed)]
            category_ids = list(dict.fromkeys(selected or [cid for cid, _ in categories]))[:70]
            fixtures = []
            for category_id in category_ids:
                try:
                    fixtures.extend(fetch_sportapi_category_events(date_iso, category_id, rapidapi_key))
                except Exception as category_exc:
                    print(f"  SPORTAPI categoria {category_id} saltata: {category_exc}")
            sportapi_cache[date_iso] = fixtures
            print(f"SPORTAPI {date_iso}: {len(fixtures)} partite disponibili ({len(category_ids)} categorie consultate).")
        except Exception as exc:
            sportapi_cache[date_iso] = []
            print(f"ERRORE SPORTAPI su {date_iso}: {exc}")

        if football_data_key:
            try:
                fixtures = fetch_football_data_by_date(date_iso, football_data_key)
                football_data_cache[date_iso] = fixtures
                print(f"FOOTBALLDATA.IO {date_iso}: {len(fixtures)} partite disponibili.")
            except Exception as exc:
                football_data_cache[date_iso] = []
                print(f"ERRORE FOOTBALLDATA.IO su {date_iso}: {exc}")
        else:
            football_data_cache[date_iso] = []

    updated = waiting = uncertain = 0
    used_sportapi = used_fd = used_tsdb = 0

    for i, match in enumerate(matches, 1):
        date_iso = str(match.get("date") or "")[:10]
        print(f'[{i}/{len(matches)}] {match.get("match_name")} ({date_iso})')

        fixture, safe, confidence, reason, details = choose_fixture(
            match, sportapi_cache.get(date_iso, [])
        )

        if fixture and safe:
            fixture["source"] = "SportAPI"
            print(
                f'  SPORTAPI candidata: {details.get("api_home")} - '
                f'{details.get("api_away")} | confidenza={confidence:.3f}'
            )
        else:
            fixture = None

        if fixture is None and football_data_key:
            print("  ↪ SportAPI non basta. Provo Footballdata.io.")
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
            print("  ↪ Fonti principali non bastano. Provo TheSportsDB.")
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

        # Aggiorna esclusivamente i quattro campi del risultato.
        client.table("matches").update(values).eq("id", match["id"]).execute()

        updated += 1
        if source == "SportAPI":
            used_sportapi += 1
        elif source == "Footballdata.io":
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
        f"Fonti usate: SportAPI={used_sportapi} | "
        f"Footballdata.io={used_fd} | TheSportsDB={used_tsdb}"
    )


if __name__ == "__main__":
    main()
