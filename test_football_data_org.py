import json, os, sys, urllib.parse, urllib.request, urllib.error

API_URL = "https://api.football-data.org/v4/matches"
TEST_DATE = "2026-08-16"

TARGETS = [
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

def norm(value):
    value = (value or "").casefold()
    repl = {"õ":"o","ö":"o","ó":"o","í":"i","é":"e","á":"a","ñ":"n","’":"'","''":"'"}
    for a,b in repl.items():
        value = value.replace(a,b)
    return "".join(ch for ch in value if ch.isalnum() or ch.isspace()).strip()

def similar(a,b):
    na, nb = norm(a), norm(b)
    return na == nb or na in nb or nb in na

def main():
    key = os.environ.get("FOOTBALL_DATA_ORG_KEY","").strip()
    if not key:
        raise RuntimeError("FOOTBALL_DATA_ORG_KEY non disponibile nel workflow GitHub.")

    params = urllib.parse.urlencode({"dateFrom":TEST_DATE,"dateTo":TEST_DATE})
    req = urllib.request.Request(
        f"{API_URL}?{params}",
        headers={"X-Auth-Token":key},
        method="GET",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            payload = json.loads(r.read().decode("utf-8"))
            print("HTTP:", r.status)
            print("X-Requests-Available-Minute:", r.headers.get("X-Requests-Available-Minute"))
    except urllib.error.HTTPError as exc:
        print("HTTP ERROR:", exc.code)
        print(exc.read().decode("utf-8", errors="replace"))
        sys.exit(1)

    matches = payload.get("matches", []) or []
    print(f"\nData test: {TEST_DATE}")
    print(f"Partite restituite da football-data.org: {len(matches)}\n")

    for m in matches:
        home = (m.get("homeTeam") or {}).get("name","")
        away = (m.get("awayTeam") or {}).get("name","")
        status = m.get("status","")
        score = (m.get("score") or {}).get("fullTime") or {}
        comp = (m.get("competition") or {}).get("name","")
        print(f"{home} - {away} | {status} | {score.get('home')}-{score.get('away')} | {comp}")

    print("\n=== TEST TARGET ===")
    found = 0
    for th, ta in TARGETS:
        match = next((m for m in matches
                      if similar(th, (m.get("homeTeam") or {}).get("name",""))
                      and similar(ta, (m.get("awayTeam") or {}).get("name",""))), None)
        if not match:
            print(f"✗ NON TROVATA: {th} - {ta}")
            continue
        score = (match.get("score") or {}).get("fullTime") or {}
        print(f"✓ TROVATA: {th} - {ta} | {match.get('status','')} | {score.get('home')}-{score.get('away')}")
        found += 1

    print(f"\nTrovate {found}/{len(TARGETS)} partite target.")

if __name__ == "__main__":
    main()
