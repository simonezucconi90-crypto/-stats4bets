import os
import requests
import unicodedata
from difflib import SequenceMatcher

TOKEN = os.getenv("SPORTMONKS_TOKEN")

if not TOKEN:
    raise RuntimeError("SPORTMONKS_TOKEN non trovato nei GitHub Secrets.")

TEST_DATE = "2026-08-16"

TARGET_MATCHES = [
    ("Cerro Porteño", "Sportivo San Lorenzo"),
    ("HJK", "Jaro"),
    ("Nõmme Kalju", "Vaprus"),
    ("Brann", "HamKam"),
    ("Dila", "Meshakhte"),
    ("Flora", "Kuressaare"),
    ("CSKA Sofia", "Botev Vratsa"),
    ("Genoa", "Ascoli"),
    ("Always Ready", "Real Potosí"),
    ("Lazio", "Mantova"),
    ("Colo-Colo", "O'Higgins"),
    ("Alianza Lima", "UTC Cajamarca"),
    ("Colorado Springs", "Birmingham Legion"),
    ("Colorado Rapids", "Sporting KC"),
]


def normalize(text):
    if not text:
        return ""

    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(
        char for char in text
        if not unicodedata.combining(char)
    )

    replacements = {
        "football club": "",
        "futbol club": "",
        "fc": "",
        "sc": "",
        "cf": "",
        "fk": "",
        "ac": "",
    }

    text = text.lower()

    for old, new in replacements.items():
        text = text.replace(old, new)

    text = "".join(
        char if char.isalnum() or char.isspace() else " "
        for char in text
    )

    return " ".join(text.split())


def similarity(a, b):
    return SequenceMatcher(
        None,
        normalize(a),
        normalize(b)
    ).ratio()


def get_team_names(fixture):
    participants = fixture.get("participants", [])

    home = None
    away = None

    for participant in participants:
        meta = participant.get("meta") or {}
        location = str(meta.get("location", "")).lower()

        if location == "home":
            home = participant.get("name")
        elif location == "away":
            away = participant.get("name")

    # Fallback se Sportmonks non restituisce meta.location
    if (not home or not away) and len(participants) >= 2:
        home = home or participants[0].get("name")
        away = away or participants[1].get("name")

    return home, away


def get_score(fixture):
    scores = fixture.get("scores", [])

    home_score = None
    away_score = None

    # Preferiamo CURRENT, che normalmente rappresenta
    # il punteggio corrente/finale della partita.
    current_scores = [
        score for score in scores
        if str(score.get("description", "")).upper() == "CURRENT"
    ]

    source = current_scores if current_scores else scores

    for score in source:
        score_data = score.get("score") or {}
        participant = str(
            score_data.get("participant", "")
        ).lower()

        goals = score_data.get("goals")

        if participant == "home":
            home_score = goals
        elif participant == "away":
            away_score = goals

    if home_score is None and away_score is None:
        return None

    return f"{home_score}-{away_score}"


def get_state(fixture):
    state = fixture.get("state") or {}
    return (
        state.get("name")
        or state.get("short_name")
        or fixture.get("state_id")
        or "?"
    )


url = (
    f"https://api.sportmonks.com/v3/football/"
    f"fixtures/date/{TEST_DATE}"
)

params = {
    "api_token": TOKEN,
    "include": "participants;scores;league;state",
    "per_page": 100,
}

all_fixtures = []
page = 1

print("=== SPORTMONKS TEST ===")
print("Data test:", TEST_DATE)
print()

while True:
    params["page"] = page

    response = requests.get(
        url,
        params=params,
        timeout=30,
    )

    print(
        f"Pagina {page} - HTTP:",
        response.status_code
    )

    if response.status_code != 200:
        print()
        print("ERRORE SPORTMONKS:")
        print(response.text)
        raise SystemExit(1)

    payload = response.json()

    fixtures = payload.get("data", [])
    all_fixtures.extend(fixtures)

    pagination = (
        payload.get("pagination")
        or payload.get("meta", {}).get("pagination")
        or {}
    )

    has_more = pagination.get("has_more")

    if has_more is True:
        page += 1
        continue

    # Fallback: se riceviamo esattamente 100 record,
    # proviamo la pagina successiva.
    if len(fixtures) >= 100:
        page += 1
        continue

    break


print()
print(
    "Partite restituite da Sportmonks:",
    len(all_fixtures)
)
print()


parsed_fixtures = []

for fixture in all_fixtures:
    home, away = get_team_names(fixture)

    if not home or not away:
        continue

    league = fixture.get("league") or {}
    league_name = league.get("name", "?")

    parsed_fixtures.append({
        "home": home,
        "away": away,
        "league": league_name,
        "state": get_state(fixture),
        "score": get_score(fixture),
    })


print("=== PRIME PARTITE RESTITUITE ===")

for fixture in parsed_fixtures[:30]:
    print(
        f'{fixture["home"]} - {fixture["away"]}'
        f' | {fixture["state"]}'
        f' | {fixture["score"]}'
        f' | {fixture["league"]}'
    )


print()
print("=== TEST TARGET ===")

found_count = 0

for target_home, target_away in TARGET_MATCHES:

    best_match = None
    best_score = 0

    for fixture in parsed_fixtures:

        home_sim = similarity(
            target_home,
            fixture["home"]
        )

        away_sim = similarity(
            target_away,
            fixture["away"]
        )

        combined = (
            home_sim + away_sim
        ) / 2

        if combined > best_score:
            best_score = combined
            best_match = fixture

    if best_match and best_score >= 0.60:

        found_count += 1

        print(
            f'✓ TROVATA: '
            f'{target_home} - {target_away}'
        )

        print(
            f'  Sportmonks: '
            f'{best_match["home"]} - '
            f'{best_match["away"]}'
        )

        print(
            f'  Stato: {best_match["state"]}'
            f' | Risultato: {best_match["score"]}'
            f' | Campionato: {best_match["league"]}'
            f' | Match: {best_score:.0%}'
        )

    else:
        print(
            f'✗ NON TROVATA: '
            f'{target_home} - {target_away}'
        )

        if best_match:
            print(
                f'  Più simile: '
                f'{best_match["home"]} - '
                f'{best_match["away"]}'
                f' ({best_score:.0%})'
            )


print()
print("==============================")
print(
    f"Trovate {found_count}/"
    f"{len(TARGET_MATCHES)} partite target."
)
print("==============================")

percentage = (
    found_count / len(TARGET_MATCHES) * 100
)

print(
    f"Copertura test: {percentage:.1f}%"
)

if found_count >= 12:
    print(
        "✅ OTTIMA: Sportmonks è un candidato "
        "molto forte per Stats4Bets."
    )
elif found_count >= 9:
    print(
        "🟡 BUONA: vale la pena approfondire."
    )
elif found_count >= 5:
    print(
        "🟠 PARZIALE: probabilmente servirà "
        "una seconda fonte."
    )
else:
    print(
        "🔴 INSUFFICIENTE: non conviene usarla "
        "come fonte principale."
    )
