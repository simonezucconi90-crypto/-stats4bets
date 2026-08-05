import csv, os, re, unicodedata
from collections import defaultdict
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import requests
from supabase import create_client

API_URL = "https://v3.football.api-sports.io/fixtures"
OUT = Path("results_artifacts")
OUT.mkdir(exist_ok=True)

def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    s = re.sub(r"\b(fc|cf|sc|ac|afc|fk|club|calcio|football|futbol)\b", " ", s)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s)).strip()

def sim(a,b):
    return SequenceMatcher(None, norm(a), norm(b)).ratio()

def split_match(name):
    p = re.split(r"\s+-\s+", name or "", maxsplit=1)
    return (p[0].strip(), p[1].strip()) if len(p)==2 else ("","")

def api_time(f):
    ts = f.get("fixture",{}).get("timestamp")
    return datetime.fromtimestamp(ts).strftime("%H:%M") if ts else ""

def time_diff(a,b):
    try:
        x=datetime.strptime(str(a)[:5],"%H:%M"); y=datetime.strptime(str(b)[:5],"%H:%M")
        return abs(int((x-y).total_seconds()/60))
    except Exception:
        return 9999

def main():
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    matches = client.table("matches").select(
        "id,date,time,match_name,pick,played_odds,current_odds,stake,outcome"
    ).is_("outcome","null").order("date").execute().data or []

    by_date = defaultdict(list)
    for m in matches:
        d = str(m.get("date") or "")[:10]
        if d: by_date[d].append(m)

    fixtures = {}
    headers = {"x-apisports-key": os.environ["API_FOOTBALL_KEY"]}
    for d in by_date:
        r = requests.get(API_URL, headers=headers, params={"date":d,"timezone":"Europe/Rome"}, timeout=30)
        r.raise_for_status()
        payload = r.json()
        if payload.get("errors"):
            raise RuntimeError(payload["errors"])
        fixtures[d] = payload.get("response", [])

    rows=[]
    for m in matches:
        home,away = split_match(m.get("match_name"))
        cand=[]
        for f in fixtures.get(str(m.get("date"))[:10],[]):
            ah=f.get("teams",{}).get("home",{}).get("name","")
            aa=f.get("teams",{}).get("away",{}).get("name","")
            hs,as_=sim(home,ah),sim(away,aa)
            score=(hs+as_)/2 + (0.10 if time_diff(m.get("time"),api_time(f))<=30 else 0)
            cand.append((score,hs,as_,f))
        cand.sort(key=lambda x:x[0], reverse=True)
        best=cand[0] if cand else None
        row={"database_id":m.get("id"),"database_match":m.get("match_name"),"date":m.get("date"),"database_time":m.get("time")}
        if best:
            score,hs,as_,f=best
            second=cand[1][0] if len(cand)>1 else 0
            ah=f.get("teams",{}).get("home",{}).get("name","")
            aa=f.get("teams",{}).get("away",{}).get("name","")
            status=f.get("fixture",{}).get("status",{}).get("short","")
            hg=f.get("goals",{}).get("home"); ag=f.get("goals",{}).get("away")
            safe=score>=0.78 and hs>=0.68 and as_>=0.68 and (score-second)>=0.08
            outcome=""
            profit=""
            gross=""
            final=""
            if status in {"FT","AET","PEN"} and hg is not None and ag is not None:
                final=f"{hg}-{ag}"
                pick=str(m.get("pick") or "1").upper()
                outcome="V" if ((pick=="1" and hg>ag) or (pick=="X" and hg==ag) or (pick=="2" and ag>hg)) else "P"
                stake=float(m.get("stake") or 20)
                odds=float(m.get("played_odds") or m.get("current_odds") or 0)
                gross=round(stake*odds,2) if outcome=="V" else 0.0
                profit=round(gross-stake,2) if outcome=="V" else round(-stake,2)
            row.update({"api_match":f"{ah} - {aa}","api_time":api_time(f),"api_status":status,
                        "confidence":round(score,4),"safe_match":safe,"final_score":final,
                        "calculated_outcome":outcome,"gross_return":gross,"profit":profit})
        rows.append(row)

    fields=sorted({k for r in rows for k in r})
    with (OUT/"results_match_report.csv").open("w",newline="",encoding="utf-8-sig") as h:
        w=csv.DictWriter(h,fieldnames=fields); w.writeheader(); w.writerows(rows)

    print(f"Partite aperte: {len(matches)}")
    print(f"Abbinamenti sicuri: {sum(bool(r.get('safe_match')) for r in rows)}")
    print("Modalità test: Supabase non modificato.")

if __name__=="__main__":
    main()
